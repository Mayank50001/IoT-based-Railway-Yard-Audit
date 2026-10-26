/* Yard Monitor dashboard.
 *
 * The pages are server-rendered shells; this file reads the station-scoped JSON
 * API and writes the result into the shell. That keeps scoping and validation
 * rules in one place (the API) while still giving the demo live updates.
 *
 * Every value that reaches innerHTML passes through esc(). Timestamps are
 * formatted explicitly in Asia/Kolkata, because the policy is "store UTC,
 * display IST", not "whatever timezone the demo laptop happens to use".
 */
(() => {
  'use strict';

  const body = document.body;
  const page = body.dataset.page || '';
  const canWrite = body.dataset.canWrite === 'true';
  const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const isLogin = page === 'login';

  const IST = 'Asia/Kolkata';
  const dateTimeFormatter = new Intl.DateTimeFormat('en-IN', {
    timeZone: IST,
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

  const ENTITY = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  };

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ENTITY[character]);
  }

  function fmtTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return `${dateTimeFormatter.format(date)} IST`;
  }

  function fmtDuration(milliseconds) {
    if (milliseconds === null || milliseconds === undefined) return '—';
    if (milliseconds < 1000) return `${milliseconds} ms`;
    return `${(milliseconds / 1000).toFixed(1)} s`;
  }

  function badge(text, kind = 'neutral') {
    return `<span class="badge ${kind}">${esc(text)}</span>`;
  }

  function stateBadge(value) {
    if (!value) return '<span class="muted">—</span>';
    return badge(value, value === 'OPEN' ? 'open' : 'closed');
  }

  function resultBadge(value) {
    if (!value) return '<span class="muted">—</span>';
    const kind = { VALID: 'valid', FLAGGED: 'fail', UNVERIFIED: 'warn' }[value];
    return badge(value, kind || 'neutral');
  }

  function severityBadge(value) {
    const kind = {
      INFO: 'info',
      WARN: 'warn',
      FAIL: 'fail',
      WARNING: 'warn',
      CRITICAL: 'fail',
    }[value];
    return badge(value, kind || 'neutral');
  }

  function directionBadge(value) {
    const kind = { ENTRY: 'entry', EXIT: 'exit' }[value];
    return badge(value, kind || 'neutral');
  }

  function inventoryStatusBadge(value) {
    return badge(value, value === 'IN_YARD' ? 'in' : 'out');
  }

  function verificationBadge(value) {
    return badge(value, value === 'VERIFIED' ? 'valid' : 'warn');
  }

  function movementLink(id) {
    return `<a href="/movements/${encodeURIComponent(id)}/">#${esc(id)}</a>`;
  }

  class ApiError extends Error {
    constructor(status, code, detail) {
      super(detail);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
    }
  }

  async function api(path, { method = 'GET', body: payload = null } = {}) {
    const options = {
      method,
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    };
    if (payload !== null && payload !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(payload);
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      options.headers['X-CSRFToken'] = csrfToken;
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      throw new ApiError(0, 'NETWORK_ERROR', 'Cannot reach the server.');
    }

    // A session can expire while the dashboard is polling. Send the operator
    // back to sign in with this page as the return path.
    if (response.status === 401 && !isLogin) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = `/login/?next=${next}`;
      throw new ApiError(401, 'NOT_AUTHENTICATED', 'Your session has expired.');
    }

    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        data = null;
      }
    }

    if (!response.ok) {
      let detail = data?.detail ?? `Request failed (HTTP ${response.status}).`;
      if (typeof detail !== 'string') {
        detail = JSON.stringify(detail);
      }
      throw new ApiError(response.status, data?.code || `HTTP_${response.status}`, detail);
    }
    return data;
  }

  function toast(message, kind = 'ok') {
    const region = document.getElementById('toast-region');
    if (!region) return;
    const node = document.createElement('div');
    node.className = `toast ${kind}`;
    node.textContent = message;
    region.appendChild(node);
    setTimeout(() => node.remove(), 4800);
  }

  function reportError(error) {
    const box = document.getElementById('page-error');
    if (!box) return;
    const suffix = error.code ? ` (${error.code})` : '';
    box.textContent = `${error.message}${suffix}`;
    box.classList.remove('hidden');
  }

  function clearError() {
    const box = document.getElementById('page-error');
    if (!box) return;
    box.classList.add('hidden');
    box.textContent = '';
  }

  function setUpdated() {
    const node = document.getElementById('refresh-status');
    if (!node) return;
    const now = new Date();
    node.textContent = `Updated ${dateTimeFormatter.format(now)} IST`;
  }

  function poll(load, intervalMilliseconds, { stopWhen = null } = {}) {
    let timer = null;
    let running = false;
    let stopped = false;

    const stop = () => {
      stopped = true;
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    const start = () => {
      if (stopped || timer !== null) return;
      timer = setInterval(tick, intervalMilliseconds);
    };

    async function tick() {
      if (stopped || document.hidden || running) return;
      running = true;
      try {
        const data = await load();
        clearError();
        setUpdated();
        if (stopWhen && stopWhen(data)) stop();
      } catch (error) {
        reportError(error);
      } finally {
        running = false;
      }
    }

    tick().finally(start);

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) tick();
    });

    return { stop };
  }

  function emptyRow(columns, message) {
    return `<tr><td colspan="${columns}" class="empty">${esc(message)}</td></tr>`;
  }

  function applyParamsToForm(form, params) {
    for (const field of form.elements) {
      if (!field.name) continue;
      const value = params.get(field.name);
      if (value !== null) field.value = value;
    }
  }

  function formToParams(form) {
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(form).entries()) {
      const trimmed = String(value).trim();
      if (trimmed) params.set(name, trimmed);
    }
    return params;
  }

  function goToParams(params) {
    const query = params.toString();
    location.href = `${location.pathname}${query ? `?${query}` : ''}`;
  }

  function pageUrl(params, limit, offset) {
    const next = new URLSearchParams(params);
    next.set('limit', String(limit));
    next.set('offset', String(offset));
    return `${location.pathname}?${next.toString()}`;
  }

  function renderPager(node, { count, limit, offset, params }) {
    if (!node) return;
    const previousOffset = Math.max(0, offset - limit);
    const nextOffset = offset + limit;
    const previous =
      offset > 0
        ? `<a href="${esc(pageUrl(params, limit, previousOffset))}">← Previous</a>`
        : '<span class="disabled">← Previous</span>';
    const upcoming =
      offset + limit < count
        ? `<a href="${esc(pageUrl(params, limit, nextOffset))}">Next →</a>`
        : '<span class="disabled">Next →</span>';
    node.innerHTML = `${previous}${upcoming}`;
  }

  function ackControl(alert) {
    if (alert.acknowledged_at) {
      const by = alert.acknowledged_by || 'an operator';
      return `<span class="ack-state">${badge('Acknowledged', 'valid')}<span>Acknowledged by ${esc(by)} · ${fmtTime(alert.acknowledged_at)}</span></span>`;
    }
    if (!canWrite) return badge('Open', 'warn');
    return `<button class="btn btn-ghost btn-sm" type="button" data-alert-id="${esc(alert.id)}">Acknowledge</button>`;
  }

  async function acknowledgeAlert(id, button) {
    if (button) {
      button.disabled = true;
      button.textContent = 'Acknowledging…';
    }
    try {
      await api(`/api/alerts/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' });
      toast('Alert acknowledged.');
      return true;
    } catch (error) {
      toast(`${error.message}${error.code ? ` (${error.code})` : ''}`, 'error');
      if (button) {
        button.disabled = false;
        button.textContent = 'Acknowledge';
      }
      return false;
    }
  }

  // --- overview ------------------------------------------------------------

  function renderOverview(data) {
    const lanes = data.lanes || {};
    const modes = lanes.by_mode || {};
    document.getElementById('stat-lanes').textContent = lanes.total ?? 0;
    document.getElementById('stat-lanes-detail').textContent =
      `${modes.ENTRY ?? 0} entry · ${modes.EXIT ?? 0} exit · ${modes.UNSET ?? 0} unset`;

    const inventory = data.inventory || {};
    document.getElementById('stat-inyard').textContent = inventory.in_yard ?? 0;
    document.getElementById('stat-out').textContent = inventory.out ?? 0;
    document.getElementById('stat-unverified').textContent = inventory.unverified ?? 0;
    document.getElementById('stat-alerts').textContent = data.alerts?.unacknowledged ?? 0;

    const open = data.open_movements || [];
    document.getElementById('open-count').textContent = open.length;
    const rows = document.getElementById('open-movements');
    rows.innerHTML = open.length
      ? open
          .map(
            (movement) => `<tr>
              <td>${movementLink(movement.id)}</td>
              <td><code>${esc(movement.device_id)}</code></td>
              <td>${directionBadge(movement.direction)}</td>
              <td class="nowrap">${fmtTime(movement.started_at)}</td>
              <td class="nowrap">${fmtTime(movement.last_evidence_at)}</td>
              <td>${movement.plan_code ? `<code>${esc(movement.plan_code)}</code>` : '<span class="muted">No plan</span>'}</td>
            </tr>`,
          )
          .join('')
      : emptyRow(6, 'No movement is open right now.');

    const latestNode = document.getElementById('latest-movement');
    const latest = data.latest_movement;
    if (!latest) {
      latestNode.className = 'panel-body muted';
      latestNode.textContent = 'No movement has been recorded yet.';
      return;
    }

    latestNode.className = 'panel-body';
    latestNode.innerHTML = `<div class="latest-movement">
      <div class="badge-row">${stateBadge(latest.state)}${resultBadge(latest.validation_result)}</div>
      <div class="big">${movementLink(latest.id)}</div>
      <div class="meta-grid">
        <div class="meta-item"><span class="meta-label">Lane</span><span class="meta-value"><code>${esc(latest.device_id)}</code> · ${directionBadge(latest.direction)}</span></div>
        <div class="meta-item"><span class="meta-label">Plan</span><span class="meta-value">${latest.plan_code ? `<code>${esc(latest.plan_code)}</code>` : '<span class="muted">No plan bound</span>'}</span></div>
        <div class="meta-item"><span class="meta-label">Started</span><span class="meta-value">${fmtTime(latest.started_at)}</span></div>
        <div class="meta-item"><span class="meta-label">Closed</span><span class="meta-value">${fmtTime(latest.closed_at)}</span></div>
      </div>
    </div>`;
  }

  function initOverview() {
    poll(async () => {
      const data = await api('/api/overview');
      renderOverview(data);
      return data;
    }, 5000);
  }

  // --- lanes ---------------------------------------------------------------

  let openByLane = new Map();

  function renderLanes(lanes) {
    const rows = document.getElementById('lane-rows');
    document.getElementById('lane-count').textContent = lanes.length;

    rows.innerHTML = lanes.length
      ? lanes
          .map((lane) => {
            const open = openByLane.get(lane.device_id);
            const mode = lane.lane_mode;
            const disabled = !canWrite || !lane.is_active;
            const buttons = canWrite
              ? `<div class="seg" role="group" aria-label="Set direction for ${esc(lane.device_id)}">
                  <button class="seg-btn ${mode === 'ENTRY' ? 'active' : ''}" type="button"
                    data-device="${esc(lane.device_id)}" data-direction="ENTRY"
                    ${mode === 'ENTRY' || disabled ? 'disabled' : ''}>Entry</button>
                  <button class="seg-btn ${mode === 'EXIT' ? 'active' : ''}" type="button"
                    data-device="${esc(lane.device_id)}" data-direction="EXIT"
                    ${mode === 'EXIT' || disabled ? 'disabled' : ''}>Exit</button>
                </div>`
              : '<span class="muted small">Read only</span>';

            return `<tr>
              <td><code>${esc(lane.device_id)}</code>${lane.label ? `<div class="muted small">${esc(lane.label)}</div>` : ''}</td>
              <td>${mode ? directionBadge(mode) : badge('UNSET', 'warn')}</td>
              <td>${lane.is_active ? badge('Active', 'valid') : badge('Disabled', 'fail')}<div class="muted small">${lane.last_seen_at ? `Seen ${fmtTime(lane.last_seen_at)}` : 'Never seen'}</div></td>
              <td>${open ? `${movementLink(open.id)} ${directionBadge(open.direction)}<div class="muted small">${fmtTime(open.started_at)}</div>` : '<span class="muted">—</span>'}</td>
              <td>${buttons}</td>
            </tr>`;
          })
          .join('')
      : emptyRow(5, 'No lanes are configured for this station.');
  }

  async function loadLanes() {
    const [laneData, overview] = await Promise.all([
      api('/api/lanes'),
      api('/api/overview'),
    ]);
    openByLane = new Map(
      (overview.open_movements || []).map((movement) => [movement.device_id, movement]),
    );
    renderLanes(laneData.lanes || []);
  }

  async function changeLaneMode(button) {
    const deviceId = button.dataset.device;
    const direction = button.dataset.direction;
    const open = openByLane.get(deviceId);
    if (
      open &&
      open.direction !== direction &&
      !window.confirm(
        `A movement is open on ${deviceId} (${open.direction}).\n\n` +
          `The lane will change to ${direction} for the next movement.\n` +
          'The open movement keeps its direction. Continue?',
      )
    ) {
      return;
    }

    button.disabled = true;
    try {
      const result = await api(`/api/lanes/${encodeURIComponent(deviceId)}/mode`, {
        method: 'POST',
        body: { direction },
      });
      if (result.changed) {
        toast(
          `${deviceId} set to ${direction}.` +
            (result.applies_to === 'NEXT_MOVEMENT' ? ' Applies to the next movement.' : ''),
        );
      } else {
        toast(`${deviceId} is already set to ${direction}.`);
      }
      await loadLanes();
    } catch (error) {
      toast(`${error.message}${error.code ? ` (${error.code})` : ''}`, 'error');
      button.disabled = false;
    }
  }

  function initLanes() {
    document.getElementById('lane-rows').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-direction]');
      if (!button || !canWrite) return;
      changeLaneMode(button);
    });
    poll(loadLanes, 5000);
  }

  // --- movements list ------------------------------------------------------

  function populateLaneFilter(selected) {
    const select = document.getElementById('filter-device');
    if (!select) return;
    api('/api/lanes')
      .then(({ lanes }) => {
        for (const lane of lanes) {
          const option = document.createElement('option');
          option.value = lane.device_id;
          option.textContent = lane.device_id;
          select.appendChild(option);
        }
        select.value = selected || '';
      })
      .catch((error) => reportError(error));
  }

  function renderMovements(data) {
    const rows = document.getElementById('movement-rows');
    const movements = data.movements || [];
    const offset = data.offset || 0;
    const limit = data.limit || 25;

    document.getElementById('movement-count').textContent = data.count ?? 0;
    document.getElementById('movement-range').textContent = data.count
      ? `Showing ${offset + 1}–${Math.min(offset + movements.length, data.count)} of ${data.count}`
      : 'No movements match';

    rows.innerHTML = movements.length
      ? movements
          .map(
            (movement) => `<tr>
              <td>${movementLink(movement.id)}</td>
              <td class="nowrap">${fmtTime(movement.started_at)}</td>
              <td><code>${esc(movement.device_id)}</code></td>
              <td>${directionBadge(movement.direction)}</td>
              <td>${stateBadge(movement.state)}</td>
              <td>${resultBadge(movement.validation_result)}</td>
              <td>${movement.plan_code ? `<code>${esc(movement.plan_code)}</code>` : '<span class="muted">—</span>'}</td>
            </tr>`,
          )
          .join('')
      : emptyRow(
          7,
          new URLSearchParams(location.search).toString()
            ? 'No movements match these filters.'
            : 'No movements yet. Events will appear here as lanes report.',
        );

    renderPager(document.getElementById('movement-pager'), {
      count: data.count || 0,
      limit,
      offset,
      params: new URLSearchParams(location.search),
    });
  }

  function initMovements() {
    const form = document.getElementById('movement-filters');
    const params = new URLSearchParams(location.search);
    applyParamsToForm(form, params);
    populateLaneFilter(params.get('device_id'));

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      goToParams(formToParams(form));
    });

    poll(async () => {
      const query = new URLSearchParams(location.search);
      query.set('limit', '25');
      if (!query.has('offset')) query.set('offset', '0');
      const data = await api(`/api/movements?${query.toString()}`);
      renderMovements(data);
      return data;
    }, 5000);
  }

  // --- movement detail -----------------------------------------------------

  function metaItem(label, value) {
    return `<div class="meta-item"><span class="meta-label">${esc(label)}</span><span class="meta-value">${value}</span></div>`;
  }

  function matchSourceLabel(value) {
    return (
      {
        NONE: 'Not matched',
        LOCOMOTIVE_RFID: 'Lead locomotive RFID',
        COACH_ASSIGNMENT: 'Coach assignment',
      }[value] || value || '—'
    );
  }

  function directionSourceLabel(value) {
    return (
      {
        OPERATOR: 'Operator lane mode',
        PLAN: 'Movement plan',
        INFERRED: 'Inventory inference',
      }[value] || value || '—'
    );
  }

  function renderMovementHeader(movement) {
    document.getElementById('page-sub').textContent =
      `Lane ${movement.device_id} · ${movement.direction} · ` +
      (movement.state === 'OPEN' ? 'collecting evidence' : 'closed');

    document.getElementById('movement-badges').innerHTML =
      stateBadge(movement.state) +
      resultBadge(movement.validation_result) +
      (movement.plan_code ? badge(movement.plan_code, 'neutral') : badge('NO PLAN', 'warn'));

    const expected = movement.expected_counts;
    const counts = movement.observed_counts || {};

    document.getElementById('movement-meta').innerHTML = [
      metaItem(
        'Plan',
        movement.plan_code
          ? `<code>${esc(movement.plan_code)}</code><div class="muted small">${esc(matchSourceLabel(movement.plan_match_source))}</div>`
          : '<span class="muted">No plan bound</span>',
      ),
      metaItem(
        'Direction',
        `${directionBadge(movement.direction)}<div class="muted small">${esc(directionSourceLabel(movement.direction_source))}</div>`,
      ),
      metaItem('Started', fmtTime(movement.started_at)),
      metaItem('Last evidence', fmtTime(movement.last_evidence_at)),
      metaItem('Closed', fmtTime(movement.closed_at)),
      metaItem('Validated', fmtTime(movement.validated_at)),
      metaItem(
        'Evidence',
        `${counts.rfid_observations ?? 0} RFID read(s) · ${counts.passages ?? 0} passage(s)`,
      ),
      metaItem(
        'Expected',
        expected
          ? `${expected.locos} loco · ${expected.coaches} coach · ${expected.wagons} wagon`
          : '<span class="muted">Plan not bound</span>',
      ),
    ].join('');
  }

  function renderCounts(movement) {
    const observed = movement.observed_counts || {};
    const expected = movement.expected_counts;
    const agree = movement.counts_agree;

    let matchValue;
    if (expected === null || expected === undefined) {
      matchValue = badge('NOT COMPARABLE', 'neutral');
    } else if (agree) {
      matchValue = badge('MATCHES PLAN', 'valid');
    } else {
      matchValue = badge('DIFFERS', 'fail');
    }

    const card = (label, value, note) =>
      `<div class="card"><span class="card-label">${esc(label)}</span><span class="card-value">${esc(value)}</span><span class="card-note">${esc(note)}</span></div>`;

    document.getElementById('counts-board').innerHTML = [
      card('Locomotives', observed.locos ?? 0, expected ? `expected ${expected.locos}` : 'no plan'),
      card('Coaches', observed.coaches ?? 0, expected ? `expected ${expected.coaches}` : 'no plan'),
      card('Wagons', observed.wagons ?? 0, expected ? `expected ${expected.wagons}` : 'no plan'),
      card('Distinct UIDs', observed.distinct_uids ?? 0, `${observed.unknown_uids ?? 0} unknown`),
      `<div class="card"><span class="card-label">Plan match</span><span class="card-value">${matchValue}</span><span class="card-note">${esc(`${observed.rfid_observations ?? 0} reads · ${observed.passages ?? 0} passages (advisory)`)}</span></div>`,
    ].join('');
  }

  function renderFindings(movement) {
    const node = document.getElementById('movement-findings');
    const findings = movement.findings || [];
    document.getElementById('findings-count').textContent = findings.length;

    if (!findings.length) {
      let message = 'No findings recorded.';
      if (movement.validation_result === 'VALID') {
        message = 'No findings. The observed consist matches the plan.';
      } else if (!movement.expected_counts) {
        message = 'No plan was bound, so the consist could not be compared.';
      }
      node.className = 'panel-body muted';
      node.innerHTML = `<p>${esc(message)}</p>`;
      return;
    }

    node.className = 'panel-body';
    node.innerHTML = findings
      .map(
        (finding) => `<article class="finding">
          <div class="finding-head">
            ${severityBadge(finding.severity)}
            <span class="finding-code">${esc(finding.code)}</span>
            ${finding.subject ? `<span class="finding-subject">${esc(finding.subject)}</span>` : ''}
          </div>
          <div class="finding-message">${esc(finding.message)}</div>
        </article>`,
      )
      .join('');
  }

  function renderMovementAlerts(movement) {
    const node = document.getElementById('movement-alerts');
    const alerts = movement.alerts || [];
    document.getElementById('alerts-count').textContent = alerts.length;

    if (!alerts.length) {
      node.className = 'panel-body muted';
      node.innerHTML = '<p>No alerts for this movement.</p>';
      return;
    }

    node.className = 'panel-body';
    node.innerHTML = alerts
      .map(
        (alert) => `<article class="alert-item">
          <div class="alert-head">
            ${severityBadge(alert.severity)}
            <span class="alert-code">${esc(alert.code)}</span>
            ${alert.subject ? `<span class="finding-subject">${esc(alert.subject)}</span>` : ''}
          </div>
          <div class="alert-message">${esc(alert.message)}</div>
          <div class="alert-meta">Raised ${fmtTime(alert.raised_at)}</div>
          <div class="alert-actions">${ackControl(alert)}</div>
        </article>`,
      )
      .join('');
  }

  function renderEvents(movement) {
    const rows = document.getElementById('movement-events');
    const events = movement.events || [];
    document.getElementById('events-count').textContent = events.length;

    rows.innerHTML = events.length
      ? events
          .map((event) => {
            const isRfid = event.kind === 'RFID_DETECTED';
            const stock = event.rolling_stock
              ? `<strong>${esc(event.rolling_stock.number)}</strong><div class="muted small">${esc(event.rolling_stock.kind)}</div>`
              : isRfid
                ? badge('Unknown', 'warn')
                : '<span class="muted">—</span>';
            return `<tr>
              <td class="nowrap">${fmtTime(event.received_at)}</td>
              <td>${badge(isRfid ? 'RFID' : 'PASSAGE', isRfid ? 'info' : 'neutral')}</td>
              <td>${event.rfid_uid ? `<code>${esc(event.rfid_uid)}</code>` : '<span class="muted">—</span>'}</td>
              <td>${stock}</td>
              <td class="num">${event.sequence ?? '—'}</td>
              <td>${event.passage_duration_ms !== null && event.passage_duration_ms !== undefined ? fmtDuration(event.passage_duration_ms) : '<span class="muted">—</span>'}</td>
            </tr>`;
          })
          .join('')
      : emptyRow(6, 'No events recorded.');
  }

  function renderEffects(movement) {
    const rows = document.getElementById('movement-effects');
    const effects = movement.inventory_effects || [];
    document.getElementById('effects-count').textContent = effects.length;

    rows.innerHTML = effects.length
      ? effects
          .map(
            (effect) => `<tr>
              <td><code>${esc(effect.rfid_uid)}</code></td>
              <td>${effect.status_before ? inventoryStatusBadge(effect.status_before) : '<span class="muted">absent</span>'}</td>
              <td>${inventoryStatusBadge(effect.status_after)}</td>
              <td class="nowrap">${effect.applied_at ? fmtTime(effect.applied_at) : badge('PENDING', 'warn')}</td>
            </tr>`,
          )
          .join('')
      : emptyRow(4, 'No inventory transition was applied.');
  }

  function initMovement() {
    const movementId = body.dataset.movementId;

    async function loadMovement() {
      const movement = await api(`/api/movements/${encodeURIComponent(movementId)}`);
      renderMovementHeader(movement);
      renderCounts(movement);
      renderFindings(movement);
      renderMovementAlerts(movement);
      renderEvents(movement);
      renderEffects(movement);
      return movement;
    }

    document.getElementById('movement-alerts').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-alert-id]');
      if (!button) return;
      if (await acknowledgeAlert(button.dataset.alertId, button)) {
        try {
          await loadMovement();
        } catch (error) {
          reportError(error);
        }
      }
    });

    poll(loadMovement, 5000, { stopWhen: (movement) => movement.state === 'CLOSED' });
  }

  // --- inventory -----------------------------------------------------------

  function renderInventory(data) {
    const rows = document.getElementById('inventory-rows');
    const records = data.inventory || [];
    const offset = data.offset || 0;
    const limit = data.limit || 50;

    document.getElementById('inventory-count').textContent = data.count ?? 0;
    document.getElementById('inventory-range').textContent = data.count
      ? `Showing ${offset + 1}–${Math.min(offset + records.length, data.count)} of ${data.count}`
      : 'No inventory records match';

    rows.innerHTML = records.length
      ? records
          .map((record) => {
            const stock = record.rolling_stock
              ? `<strong>${esc(record.rolling_stock.number)}</strong><div class="muted small">${esc(record.rolling_stock.kind)}${record.rolling_stock.is_active ? '' : ' · inactive'}</div>`
              : badge('UNKNOWN TAG', 'warn');
            return `<tr>
              <td><code>${esc(record.rfid_uid)}</code></td>
              <td>${stock}</td>
              <td>${inventoryStatusBadge(record.status)}</td>
              <td>${verificationBadge(record.verification)}</td>
              <td class="nowrap">${fmtTime(record.since)}</td>
              <td>${record.last_movement_id ? movementLink(record.last_movement_id) : '<span class="muted">—</span>'}</td>
            </tr>`;
          })
          .join('')
      : emptyRow(
          6,
          new URLSearchParams(location.search).toString()
            ? 'No inventory records match these filters.'
            : 'Inventory is empty. It fills when a movement is validated.',
        );

    renderPager(document.getElementById('inventory-pager'), {
      count: data.count || 0,
      limit,
      offset,
      params: new URLSearchParams(location.search),
    });
  }

  function initInventory() {
    const form = document.getElementById('inventory-filters');
    const params = new URLSearchParams(location.search);
    applyParamsToForm(form, params);

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      goToParams(formToParams(form));
    });

    poll(async () => {
      const query = new URLSearchParams(location.search);
      query.set('limit', '50');
      if (!query.has('offset')) query.set('offset', '0');
      const data = await api(`/api/inventory?${query.toString()}`);
      renderInventory(data);
      return data;
    }, 10000);
  }

  // --- alerts --------------------------------------------------------------

  function renderAlerts(data) {
    const rows = document.getElementById('alert-rows');
    const alerts = data.alerts || [];
    const offset = data.offset || 0;
    const limit = data.limit || 50;

    document.getElementById('alert-count').textContent = data.count ?? 0;
    document.getElementById('alert-range').textContent = data.count
      ? `Showing ${offset + 1}–${Math.min(offset + alerts.length, data.count)} of ${data.count}`
      : 'No alerts match';

    rows.innerHTML = alerts.length
      ? alerts
          .map(
            (alert) => `<tr>
              <td class="nowrap">${fmtTime(alert.raised_at)}</td>
              <td>${severityBadge(alert.severity)}</td>
              <td><span class="alert-code">${esc(alert.code)}</span></td>
              <td>${movementLink(alert.movement_id)}</td>
              <td>${esc(alert.message)}</td>
              <td>${ackControl(alert)}</td>
            </tr>`,
          )
          .join('')
      : emptyRow(
          6,
          new URLSearchParams(location.search).toString()
            ? 'No alerts match these filters.'
            : 'No alerts. Validation has not raised anything.',
        );

    renderPager(document.getElementById('alert-pager'), {
      count: data.count || 0,
      limit,
      offset,
      params: new URLSearchParams(location.search),
    });
  }

  function initAlerts() {
    const form = document.getElementById('alert-filters');
    const params = new URLSearchParams(location.search);
    applyParamsToForm(form, params);

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      goToParams(formToParams(form));
    });

    const load = async () => {
      const query = new URLSearchParams(location.search);
      query.set('limit', '50');
      if (!query.has('offset')) query.set('offset', '0');
      const data = await api(`/api/alerts?${query.toString()}`);
      renderAlerts(data);
      return data;
    };

    document.getElementById('alert-rows').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-alert-id]');
      if (!button) return;
      if (await acknowledgeAlert(button.dataset.alertId, button)) {
        try {
          await load();
        } catch (error) {
          reportError(error);
        }
      }
    });

    poll(load, 10000);
  }

  // --- login ---------------------------------------------------------------

  function safeNext(value) {
    return value && value.startsWith('/') && !value.startsWith('//') ? value : '/';
  }

  function initLogin() {
    const form = document.getElementById('login-form');
    const errorBox = document.getElementById('login-error');
    const button = form.querySelector('button[type="submit"]');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      errorBox.classList.add('hidden');
      errorBox.textContent = '';
      button.disabled = true;
      button.textContent = 'Signing in…';

      try {
        await api('/api/auth/login', {
          method: 'POST',
          body: {
            username: form.elements.username.value.trim(),
            password: form.elements.password.value,
          },
        });
        location.href = safeNext(form.dataset.next);
      } catch (error) {
        let message = `${error.message}${error.code ? ` (${error.code})` : ''}`;
        if (error.status === 429) {
          message = 'Too many sign-in attempts. Wait a minute and try again.';
        } else if (error.code === 'CSRF_FAILED') {
          message = 'Your page token expired. Reload the page and try again.';
        }
        errorBox.textContent = message;
        errorBox.classList.remove('hidden');
        form.elements.password.value = '';
        form.elements.password.focus();
        button.disabled = false;
        button.textContent = 'Sign in';
      }
    });
  }

  // --- boot ----------------------------------------------------------------

  const pages = {
    overview: initOverview,
    lanes: initLanes,
    movements: initMovements,
    movement: initMovement,
    inventory: initInventory,
    alerts: initAlerts,
    login: initLogin,
  };

  if (pages[page]) pages[page]();
})();
