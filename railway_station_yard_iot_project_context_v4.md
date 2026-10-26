# Railway Station Yard IoT — RFID-Primary Validation
## Project Context & Technical Specification — v4

**Updated:** 2026-10-25  
**Implementation stack:** Django + Django REST Framework + Django ORM + SQLite.

> **Current baseline:** The user approved moving forward with distinct RFID UID counting as the primary validation method and ultrasonic passage counts as supporting evidence. This document supersedes v3's mandatory sensor pairing, pairing-dependent validation, and paired-only inventory rules. No backend implementation is claimed by this specification.

Read [Hardware Progress and Backend Handoff](railway_station_yard_iot_backend_handoff.md) for reported hardware progress. Use [v3](railway_station_yard_iot_project_context_v3.md) for compatible details about authentication, event transport, station scoping, APIs, and the physical prototype. Where those documents conflict with this revision, **v4 takes precedence**.

## 1. The simple system

For each movement, the backend keeps:

- the distinct RFID UIDs actually read;
- the known units' types: `LOCO`, `COACH`, or `WAGON`;
- an independent ultrasonic passage count;
- the expected movement plan, if one can be identified;
- stored findings explaining the comparison.

RFID answers **which registered units were observed**. Ultrasonic answers **how many physical interruptions were observed**. The backend compares these at movement level; it does not match each tag to an individual interruption.

Eight distinct registered coach UIDs satisfy an expectation of eight coaches. A UID repeated ten times still represents one identified unit. Unknown UIDs remain visible but cannot silently count as coaches, wagons, or locomotives.

This is a passive, hand-operated tabletop monitoring/auditing prototype. It does not control railway infrastructure or establish production railway reliability.

## 2. What remains unchanged

- A registered device belongs to one station. Inventory is station-level; there is no separate `yard_id`.
- A device is a **physical lane**, not an entry-only or exit-only gate. Its operating direction is the lane mode chosen by an authenticated operator, recorded with who chose it and when.
- Station comes from the authenticated device record. Direction comes from the lane mode captured on the movement when its bucket opens. Neither is trusted from the device payload.
- Inventory state is **never** used to infer movement direction. It is an output of validated movements and an input only to warnings such as `UNEXPECTED_EXIT`.
- Device API keys and yard-master authentication are separate. Dashboard requests are scoped to the user's station.
- Events are stored transactionally and deduplicated by globally unique `event_id`. Retrying an event returns HTTP 200 without counting or assigning it again.
- The ESP32 senses, filters, timestamps, queues, and transmits independent events. It does not decide plans, counts, inventory, validation, movement boundaries, or direction.
- Preserve unfamiliar project files and follow `AGENTS.md`: code is provided in chat unless the human explicitly authorizes code-file edits; ask before accessing files outside the project.

Previous inventory state is useful for identifying unexpected exits or repeated entries. It must not silently turn an incoming observation into an exit, or vice versa.

## 3. Lanes, lane mode, and movement buckets

One station may have one shared gate or several separate gates. A `device` record represents one physical crossing lane; it is not permanently an entry gate or an exit gate.

### Lane mode

1. Each lane has a current `lane_mode` of `ENTRY`, `EXIT`, or unset.
2. An authenticated operator sets the mode before passing stock. Every change is appended to `lane_mode_changes` with `set_by`, `changed_at`, and an optional note. History is never edited. Through the dashboard, only `YARD_MASTER` and `ADMIN` may change it; `VIEWER` is read-only. Repeating the current mode is a no-op and writes no audit row. A disabled lane cannot be changed.
3. Ingestion **rejects** events for a lane whose mode is unset (HTTP 409, `LANE_MODE_NOT_SET`). It does not guess, default, or create a movement without a direction. The firmware queue retains the events and retries.
4. The mode applies to the **next** movement opened on that lane. An already-open movement keeps the direction it was opened with, even if the operator changes the mode mid-consist.
5. The movement stores its `direction`, a `direction_source` (`OPERATOR` now; `PLAN` and `INFERRED` are reserved), and the exact `lane_mode_change` row that authorised it. The audit trail therefore answers "who said this was an entry?"
6. Inventory state must not contradict the chosen mode silently. If most observed units are already in the state that contradicts the mode, the movement is flagged `DIRECTION_CONFLICT` for review. Nothing is auto-flipped, and no second direction is opened mid-movement.

If the operator forgets to switch modes, the result is a clearly attributable wrong direction (an audited mode plus inventory warnings), not a compounding inversion of every future movement. This is the accepted cost of not inferring direction from stock state.

There is at most one `OPEN` movement per device. Direction is part of the movement, not part of the lane key.

1. **Any accepted event can start a movement**, including a coach RFID read or an ultrasonic passage. A locomotive read is not required to open the bucket.
2. Use server `received_at` for segmentation and update `last_evidence_at` on every assigned event.
3. An evidence gap **less than or equal to 20 seconds** stays in the same movement. A gap **greater than 20 seconds** starts a new one.
4. Close an idle movement through both a periodic sweep and lazy checks on reads/ingestion. Closing twice must have no additional effect.
5. At closure, resolve the plan, calculate distinct counts, store findings, and apply inventory changes once.

Device sensing timestamps and passage entry/exit metadata remain useful audit data. Passage timestamps continue to describe detection-zone entry, even though the event is emitted on exit. They are **not used for per-unit pairing** in v4.

Store timestamps in UTC and display IST on the dashboard. Do not label an unsynchronised clock as UTC. Delayed queue replay can still distort receive-time movement segmentation; this inherited prototype limitation is not solved by changing the counting policy.

### Authentication and station scoping

Dashboard access uses a Django **session cookie with CSRF protection**, not a per-user API token. A token in browser storage is a second copy of the credential with no operational advantage here, and a session can be revoked by deactivating the account. Device API keys and human sessions are separate credentials; neither authenticates the other surface.

Every account is bound to exactly one station through `StaffProfile`. There is **no cross-station administrator**: `ADMIN` means full rights *at that station*. A dashboard request for another station's object returns 404, identical to a missing object, so one station cannot confirm that another station's lane exists.

| Role | Read lanes and yard state | Change lane mode and other yard state |
|---|---|---|
| `VIEWER` | yes | no |
| `YARD_MASTER` | yes | yes |
| `ADMIN` | yes | yes, at the same station |

The Django admin site is a separate superuser operations console and is **not** station-scoped. The `create_staff` command deliberately creates a dashboard account without Django admin access; an account that needs both must be granted admin access separately and knowingly. This keeps the "no cross-station administrator" rule true for the dashboard rather than pretending a role name also secures the admin site.

Login is rate-limited per client IP (initially 10 attempts per minute), because login is the only unauthenticated write. A successful login creates a fresh session key and the session expires after a yard shift (12 hours by default).

The lane API used by the dashboard:

```text
GET  /api/auth/csrf           -> CSRF cookie and token for the next unsafe request
POST /api/auth/login          -> start a session; returns user, station, role
POST /api/auth/logout         -> end the session
GET  /api/auth/me             -> current user, station, role
GET  /api/lanes               -> lanes at the account's station
GET  /api/lanes/<device_id>   -> one lane, recent mode changes, current open movement
POST /api/lanes/<device_id>/mode  -> {"direction": "ENTRY"|"EXIT", "note": "..."}
```

A successful mode change returns `applies_to: "NEXT_MOVEMENT"` and the still-open movement, if any. That distinction is deliberate: the operator changed the lane, but the consist already being watched did not change direction.

The station-scoped read API used by the dashboard:

```text
GET  /api/overview                       -> lanes, inventory totals, open movements, open alerts
GET  /api/movements                      -> filters: state, direction, result, device_id; limit/offset
GET  /api/movements/<id>                 -> movement, findings, alerts, events, inventory effects, observed counts
GET  /api/findings                       -> filters: severity, code, movement
GET  /api/alerts                         -> filters: severity, code, acknowledged
POST /api/alerts/<id>/acknowledge        -> record the operator and time; repeating is a no-op
GET  /api/inventory                      -> filters: status, verification, q (UID fragment)
```

The operator-facing dashboard is served by Django at the site root and uses those endpoints with the same session cookie:

```text
GET  /login/             -> sign-in page; an unauthenticated page redirects here with ?next=
POST /logout/            -> end the session (CSRF-protected form)
GET  /                   -> overview
GET  /lanes/             -> lane modes and audited changes
GET  /movements/         -> filterable movement history
GET  /movements/<id>/    -> one movement with findings, alerts, events, inventory effects
GET  /inventory/         -> current yard inventory
GET  /alerts/            -> alert history and acknowledgement
```

Pages are thin Django shells; data comes from the read API, so station scoping and validation have one implementation rather than a second copy in templates. A page for another station's movement is a 404 before the browser calls the API. Timestamps are stored in UTC and rendered explicitly in IST. Open data refreshes by polling every 5–10 seconds; a websocket is not required for this prototype.

Movement reads close idle buckets first, so a dashboard without a scheduler never shows a movement that should already have closed. This means some GETs perform a write; that is an accepted prototype trade-off. Read responses never recompute business rules that a stored finding already carries.

### Device event contract

```text
POST /api/ingest/events
X-Device-ID: gate-1
X-Api-Key: <device key>
Content-Type: application/json
```

The body is either one event object or `{"events": [...]}` with at most 50 events. One batch shares a single server receive instant, so events drained from the firmware queue during one request cannot straddle a bucket boundary.

```json
{
  "event_id": "gate-1-7f3a2c-000042",
  "event_type": "RFID_DETECTED",
  "device_id": "gate-1",
  "timestamp": "2026-10-25T09:30:08.120Z",
  "sequence": 42,
  "rfid_uid": "FA37E63F"
}
```

```json
{
  "event_id": "gate-1-7f3a2c-000043",
  "event_type": "PHYSICAL_PASSAGE",
  "device_id": "gate-1",
  "timestamp": "2026-10-25T09:30:09.400Z",
  "sequence": 43,
  "metadata": {
    "started_at": "2026-10-25T09:30:09.400Z",
    "ended_at": "2026-10-25T09:30:09.850Z",
    "duration_ms": 450,
    "min_distance_cm": 8,
    "peak_distance_cm": 41
  }
}
```

`event_id` is generated once per logical event and reused on every retry. The contract is:

```text
<device_id>-<boot_id>-<sequence>
```

`boot_id` is a short random string created at firmware start and persisted with the queue, so a queued event keeps its identifier across a reboot. `sequence` is a monotonically increasing counter. `event_id` is globally unique on the server; the same value arriving from a different device is a conflict, not a retry.

`event_type` is the documented field name. `kind` is accepted as an alias for compatibility with earlier scripts. `device_id` is optional and, when present, must match the authenticated device.

Per-event result statuses:

| Status | Meaning | Firmware action |
|---|---|---|
| `accepted` | Stored and assigned to a movement | Delete from queue |
| `duplicate` | Same `event_id` already stored; returns the original movement | Delete from queue |
| `rejected` | Permanently invalid event, with `error.code` | Delete from queue and log; retrying cannot help |

Whole-request failures mean nothing was stored:

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `PARSE_ERROR` | Malformed JSON or envelope |
| 401 | `DEVICE_AUTH_REQUIRED` / `DEVICE_AUTH_FAILED` | Missing or wrong credentials; fix provisioning |
| 403 | `DEVICE_INACTIVE` | Device disabled by an operator |
| 403 | `DEVICE_ID_MISMATCH` | Payload claims a different device; fix firmware |
| 409 | `LANE_MODE_NOT_SET` | Retryable: wait for an operator to select the lane mode |
| 409 | `EVENT_ID_DEVICE_MISMATCH` | Another device already used this `event_id`; investigate |

HTTP 200 means the request was processed; per-event statuses carry the outcome. A batch can therefore contain accepted, duplicate, and rejected events at once, so one bad payload in the queue does not block the rest.

## 4. Counting rules

| Quantity | Definition |
|---|---|
| `rfid_observation_count` | Number of accepted `RFID_DETECTED` events, excluding transport retries |
| `unique_rfid_count` | Number of distinct canonical UIDs, including unknown UIDs |
| observed locomotive count | Distinct known, eligible `LOCO` units actually read |
| observed coach count | Distinct known, eligible `COACH` units actually read |
| observed wagon count | Distinct known, eligible `WAGON` units actually read |
| `physical_passage_count` | Number of accepted `PHYSICAL_PASSAGE` events, excluding transport retries |
| expected total | `expected_locos + expected_coaches + expected_wagons` |

Normalize UIDs before lookup and counting. Preserve leading zeros and use the same canonical string representation in events and master data.

Preserve logical observations even when a UID repeats. They contribute only one identified unit to composition. Repeated logical observations may produce a `DUPLICATE_OBSERVATION` warning; ordinary repeated raw reader polls should already be suppressed by firmware cooldown. A duplicate UID is never an additional coach.

Counts are compared by type, not merely by the total number of UIDs. An extra locomotive cannot substitute for a missing coach.

## 5. Plans and coach-based locomotive lookup

### Expected data

Retain the existing movement-plan information:

- station and direction;
- expected lead locomotive;
- expected locomotive, coach, and wagon counts;
- expected start time, origin, destination, and plan status.

Multiple locomotives are supported through `expected_locos`; the lead locomotive provides the normal plan-matching identity. Additional locomotive identities are not claimed to be validated individually unless explicitly included in plan assignments.

Locomotive identity is claimed **per unit named**, never inferred from the count. If the assignments name every expected locomotive, the plan has claimed their identities and a different observed locomotive is a failure (`LOCOMOTIVE_MISMATCH`). If they name only some, the unnamed remainder is counted but not identified, so a different second locomotive is legitimate and is caught only if the count is wrong. This keeps an ordinary multi-loco freight valid while still failing a rake whose named locomotive did not turn up.

Locomotive identity is deliberately **not** a flag on the rolling-stock master. The same unit hauls different rakes on different days, so a boolean on `RollingStock` cannot answer "is this the locomotive for this movement"; the plan-scoped assignment can. A static property such as `kind = LOCO` or `is_active` describes the unit, not the rake.

Home shed, division, and zone are rolling-stock master data. Division and zone are optional additions requested in the current discussion. Current yard presence is derived from movement/inventory records, not from two independently editable `entered`/`exited` booleans.

### Current plan assignments

Add a small `movement_plan_assignments` association with:

- `plan_id`;
- `rolling_stock_id`;
- uniqueness on `(plan_id, rolling_stock_id)`.

Assign known coaches to the plan for their current intended movement. Wagons and additional locomotives may use the same association where needed. This lets a coach identify its expected hauling locomotive through the plan.

Assignments belong to a particular plan, not permanently to one locomotive. Historical assignments can be retained because current lookup filters by eligible plan status, station, direction, and time window.

These associations provide **plan lookup context**. They do not automatically turn a count-based plan into an exact-coach-roster validation requirement. Exact roster validation is a separate future requirement if requested.

### Matching rules

1. Consider eligible, unused plans for the same station and direction, with `SCHEDULED` or `ACTIVE` status and expected start time within the configured window, initially 60 minutes.
2. Prefer a plan whose expected lead locomotive was actually read. Retain v3's nearest-expected-time selection and ambiguity warning for multiple direct locomotive matches.
3. If the lead locomotive cannot identify a plan, use observed coaches' current plan assignments. Bind through this fallback only when the usable assignments agree on one eligible plan. Do not guess among conflicting assignments.
4. Record the match source, such as `LOCOMOTIVE_RFID` or `COACH_ASSIGNMENT`.
5. Preserve the existing rule that one plan can bind to at most one movement. Set a bound plan to `ACTIVE`, then `COMPLETED` on closure.
6. No usable plan remains an unplanned movement. A conflicting observed locomotive or conflicting assignments remain findings, not something the fallback silently overwrites.

Fetching the expected locomotive's database record **does not create an RFID observation**. If its tag was not read:

- continue validating the observed coaches and other evidence;
- show the expected locomotive separately from observed locomotives;
- record `MISSING_LOCOMOTIVE_RFID`;
- do not include its UID in observed counts or apply a new inventory transition to it.

An absent read is missing identity evidence, not conclusive proof that the expected locomotive physically passed. A different hauling locomotive or an outdated assignment is also possible.

## 6. Primary validation and findings

Validation is authoritative at movement closure. Keep lifecycle and outcome separate:

- `state = OPEN | CLOSED`;
- `validation_result = NULL` while open;
- closed outcome is `VALID`, `FLAGGED`, or `UNVERIFIED`.

Primary checks include:

| Check | Outcome when a problem is present |
|---|---|
| Unknown UID | `FAIL`, with `UNKNOWN_RFID` |
| Invalid/ineligible rolling-stock type or retired stock | `FAIL` with an explanatory finding |
| No matching plan | `FAIL`, with `UNPLANNED_MOVEMENT` |
| Conflicting expected and observed locomotive/plan information | `FAIL`, with `UNAUTHORIZED_MOVEMENT` or a plan-conflict finding |
| Distinct known composition differs from expected counts | `FAIL`, with `COUNT_MISMATCH` |
| Expected lead locomotive RFID absent | `FAIL`, with `MISSING_LOCOMOTIVE_RFID`; continue the other checks |
| Unexpected exit from inventory | `FAIL`, with `UNEXPECTED_EXIT` |
| Repeated logical UID observation | `WARN`; count the UID once |
| Ultrasonic count disagreement | `WARN`; does not itself fail RFID composition |

Unknown UIDs are separately reported, and a planned movement containing unknown units is not silently considered a complete known composition.

The closed outcome procedure is:

1. Zero known RFID observations: `UNVERIFIED`, with all applicable findings/alerts retained.
2. Otherwise, any `FAIL`: `FLAGGED`.
3. Otherwise: `VALID`, possibly with warnings.

`VALID` now means **the required RFID-based checks passed**. It does not mean both sensors independently verified each unit, or that an exact coach roster was checked.

Save the expected and observed numbers behind every finding. The dashboard reads stored findings rather than recomputing business rules. Revalidation replaces findings atomically and must not duplicate alerts or inventory effects.

Implemented finding codes (severity is fixed per code; `subject` is the UID or unit number where the finding is about one unit):

| Code | Severity | Raised when |
|---|---|---|
| `UNKNOWN_RFID` | `FAIL` | A read UID has no rolling-stock master row |
| `RETIRED_ROLLING_STOCK` | `FAIL` | A read unit is inactive, so it cannot satisfy a count |
| `UNPLANNED_MOVEMENT` | `FAIL` | No eligible plan could be bound |
| `PLAN_AMBIGUOUS` | `WARN` | Several eligible plans matched, so none was guessed |
| `LOCOMOTIVE_MISMATCH` | `FAIL` | A plan naming every expected locomotive saw a different one |
| `MISSING_LOCOMOTIVE_RFID` | `FAIL` | A named locomotive (the lead, or a roster member) was not read |
| `COUNT_MISMATCH` | `FAIL` | Distinct known composition differs from the plan, by type |
| `DUPLICATE_OBSERVATION` | `WARN` | One UID was read more than once; still one unit |
| `ULTRASONIC_COUNT_MISMATCH` | `WARN` | Passage total differs from the distinct UID total |
| `DIRECTION_CONFLICT` | `WARN` | Most observed units were already in the yard on an `ENTRY` |
| `ENTRY_ALREADY_PRESENT` | `WARN` | That unit was already recorded inside the yard |
| `UNEXPECTED_EXIT` | `FAIL` | That unit was never recorded inside the yard |

Alerts are notifications: one per failing or warning finding, created once, never deleted, and acknowledging one does not change the finding. Findings are current state and are replaced when a movement is revalidated.

## 7. Ultrasonic as the safety net

Compare the independent passage count with the distinct UID count. Keep the plan's expected total in the same finding when available.

For an expected **1 locomotive + 8 coaches**, the normal physical total is **9**, not 8.

| Observation | Meaning to report |
|---|---|
| Passage count equals distinct UID count | Aggregate counts agree; this does not prove per-unit correspondence |
| Passage count exceeds distinct UID count | More interruptions than identities; possible missed RFID, untagged stock, repeat physical passage, or sensor noise |
| Passage count is below distinct UID count | Fewer interruptions than identities; possible missed/merged passages or a tag presented without physical passage |
| Passage count reaches the expected total but RFID composition is short | Supporting evidence consistent with missed identification; it does not recover the missing UID |
| Both observed totals are short | Report the shortfall; do not claim which sensor failed or that the missing unit definitely passed |

Use a movement-level `ULTRASONIC_COUNT_MISMATCH` warning when passage and distinct UID totals disagree. Do not use v3's unpaired-event reasoning or fabricate individual `PHYSICAL_WITHOUT_RFID`/`RFID_WITHOUT_PHYSICAL` links from aggregate counts.

A complete, otherwise valid RFID composition can remain `VALID` even with fewer, extra, or zero ultrasonic passages. The warning stays visible for review; the disagreement is not hidden.

The system cannot identify a unit from an interruption alone. Equal totals can also conceal different missed detections. These limits are recorded honestly without adding a pairing algorithm.

## 8. RFID-primary inventory

At movement closure, update inventory once for every distinct UID **actually read**, without requiring an ultrasonic pair:

- `ENTRY`: recorded presence becomes `IN_YARD`;
- `EXIT`: recorded presence becomes `OUT`.

Rules:

- A known UID in a `VALID` movement has inventory status `VERIFIED`, meaning RFID-validated under this policy, not independently proven physical presence.
- Known UIDs in `FLAGGED`/`UNVERIFIED` movements and unknown UIDs have status `UNVERIFIED`.
- An ultrasonic warning alone does not downgrade an otherwise valid RFID inventory update.
- Preserve unknown UIDs as unidentified inventory entries; do not discard them.
- An interruption with no UID cannot create an identified inventory row.
- A planned/inferred locomotive with no actual RFID read does not receive a new inventory transition.
- A flagged movement still updates the UIDs that were observed. Do not erase observed evidence because another unit was missing.
- Keep one row per `(station_id, rfid_uid)`, preserve exit rows as `OUT`, and change `since` only when presence changes.
- An entry already recorded as `IN_YARD` leaves presence unchanged and raises a warning. An exit not previously recorded as `IN_YARD` raises `UNEXPECTED_EXIT` and is unverified.
- Evaluate inventory anomalies against the state before the movement's first application. Revalidation must not mistake its own previous update for a new entry/exit, repeat transitions, or overwrite a later movement's inventory state.

This explicitly replaces v3's paired-only inventory rule. The accepted assumption is that a gate RFID observation is the primary record of a unit's movement; ultrasonic disagreement remains visible as supporting evidence.

## 9. Minimal backend model changes

Retain the station, yard-master/user, device, rolling-stock, plan, movement, event, finding, inventory, and alert models described in compatible parts of v3.

Changes for v4:

- Add current movement-plan assignments for coach-based lookup.
- Add optional rolling-stock division and zone fields alongside home shed.
- Record the plan-match source and validation policy on movements.
- Keep expected locomotive information separate from actually observed locomotive information.
- Derive distinct UID/type counts from stored RFID events. A separate per-observation pairing table is not required.
- Do not implement `paired_passage_event_id`, a pairing service, or a backend pairing-window setting.
- Model the gate as a lane: `device.lane_mode` holds the operator-selected direction, and `lane_mode_changes` is an append-only audit of who changed it, when, and why.
- Record `direction`, `direction_source`, and the authorising `lane_mode_change` on every movement.
- Enforce one open movement **per device**, one movement per bound plan, station-scoped inventory uniqueness, event-id uniqueness, and alert deduplication.
- Record one frozen pre-movement inventory effect per distinct read UID, so a revalidation evaluates anomalies against the state before the movement and never replays or rewinds a transition.
- Do not enforce a fixed entry/exit classification on devices, and do not add an editable `entered`/`exited` pair to rolling stock.

The event contract remains `RFID_DETECTED` / `PHYSICAL_PASSAGE`, with `event_id`, `device_id`, `sequence`, device timestamp, UID where applicable, and passage metadata. No firmware change is needed merely to make the backend count distinct UIDs instead of pairing events.

## 10. Acceptance scenarios

Unless otherwise stated, use a plan expecting **1 lead locomotive + 4 coaches**. Every named observed unit below has a distinct, registered UID, and inventory state is appropriate for the direction.

| Scenario | Expected result |
|---|---|
| Lead loco + 4 coaches, 5 passages | `VALID`; five observed UIDs enter inventory |
| Lead loco + 4 coaches, 4 passages | `VALID` with ultrasonic warning; five inventory updates |
| Lead loco + 4 coaches, zero passages | `VALID` with ultrasonic warning; five inventory updates |
| Lead loco + 4 coaches, 7 passages | `VALID` with ultrasonic warning; no invented extra inventory identities |
| Lead loco + 3 coaches, 5 passages | `FLAGGED`; count mismatch plus supporting ultrasonic warning |
| Lead loco + 3 coaches, 4 passages | `FLAGGED`; count mismatch even though sensor totals agree |
| 4 coaches, no loco RFID, 5 passages, unique current plan assignment | Plan found through coaches; `FLAGGED` for missing locomotive RFID/count; only four observed UIDs update inventory |
| 4 coaches, no loco RFID, no usable plan assignment | Recorded and `FLAGGED` as unplanned; no guessed locomotive observation |
| Wrong loco + 4 coaches associated with the expected plan | `FLAGGED`; fallback must not conceal the locomotive conflict |
| 1 coach replaced by an unknown UID | `FLAGGED`; unknown identity and incomplete known composition; retain unknown inventory UID |
| Unknown RFID only | `UNVERIFIED`; retain unknown-tag findings and inventory UID |
| Passages only | `UNVERIFIED`; no invented UID or identified inventory row |
| Repeated transport `event_id` | HTTP 200; one event and no repeated assignment/count/inventory effect |
| Same UID in multiple logical RFID events | One identified unit; preserve events and a duplicate warning; never fill a missing coach count |
| Coaches point to conflicting eligible plans | Flag the ambiguity; do not arbitrarily choose a fallback plan |
| EXIT for observed stock never recorded inside | `FLAGGED` with `UNEXPECTED_EXIT`; recorded as `OUT` and unverified |
| Plan expects 2 locos + 4 coaches, all read, 6 passages | `VALID` when lead identity and other required checks pass |
| Events arrive on a lane whose mode is unset | HTTP 409 `LANE_MODE_NOT_SET`; no movement, no event counted, no inventory change |
| Operator switches the lane mode while a movement is open | Open movement keeps its original direction; the new mode applies to the next movement |
| Operator sets `ENTRY` while most observed units are already `IN_YARD` | Movement stays `ENTRY`; `DIRECTION_CONFLICT` warning; inventory is not auto-flipped |
| Duplicate `event_id` sent again after a lost response | HTTP 200, `duplicate` status, one stored event, no second movement assignment |
| One malformed event inside a batch | That event is `rejected` with a code; the rest are accepted; the queue is not blocked |
| Same `event_id` sent by a different device | HTTP 409 `EVENT_ID_DEVICE_MISMATCH`; nothing is stored |
| Same physical lane used for an entry and later an exit | One device record; two movements with different directions; both auditable to their `lane_mode_change` |
| Dashboard request without a session | HTTP 401 `NOT_AUTHENTICATED`; no yard data |
| Session for an account with no `StaffProfile` | HTTP 403 `NO_STAFF_PROFILE`; no session is created at login |
| Session for an inactive profile or inactive station | HTTP 403 `STAFF_INACTIVE` / `STATION_INACTIVE` |
| Account requests another station's lane | HTTP 404, byte-identical to a nonexistent lane; no confirmation of its existence |
| `VIEWER` attempts a lane-mode change | HTTP 403 `ROLE_NOT_PERMITTED`; lane unchanged |
| Operator repeats the current lane mode | HTTP 200 `changed: false`; no new audit row |
| Operator changes the mode on a disabled lane | HTTP 409 `LANE_INACTIVE`; lane unchanged |
| Device key sent to a dashboard endpoint | HTTP 401; device credentials do not authenticate a human |
| Dashboard session sent to ingestion without device keys | HTTP 401 `DEVICE_AUTH_REQUIRED`; a session does not authenticate a device |
| Dashboard page without a session | HTTP 302 to `/login/?next=<path>`; no yard data is rendered |
| Dashboard movement page for another station | HTTP 404, identical to a missing movement |
| Dashboard timestamp | Rendered explicitly in IST while storage remains UTC |

Also test UID normalization, spoofed station/direction fields, disabled devices, exact 20-second boundaries, closure without future events, alert deduplication, and revalidation without repeated inventory transitions.

Use a controllable **server ingestion clock** to test receive-time segmentation without sleeping. Changing device timestamps alone must not change the backend's receive-time movement boundary.

The earlier hardware acceptance report covers emitted observations/passages, not necessarily distinct rolling-stock identities. Reusing one tag for many bench passes tests sensing/cooldown, but cannot represent many distinct coaches in a count-based consist demonstration.

## 11. Implementation order

1. Finish the existing Django foundation: configuration, UTC storage, database health endpoint, and tests.
2. Add the minimal domain models and repeatable seed data, including current coach-to-plan assignments.
3. Implement authenticated single/batch ingestion, canonical UIDs, transactional persistence, and event-id idempotency.
4. Implement movement buckets, the 20-second gap, and idempotent periodic/lazy closure.
5. Implement direct/fallback plan lookup, distinct RFID composition checks, aggregate ultrasonic warnings, and stored findings.
6. Apply RFID-primary inventory changes once and expose station-scoped dashboard/history/alert APIs.
7. Provide the Django-served dashboard over those read APIs, including lane-mode control and alert acknowledgement.
8. Rehearse with the real ESP32 and seeded master data. A simulator is **not** part of the delivery: the demonstration uses physical sensors, and any additional data is seeded. This supersedes the earlier simulator step.
9. Provide final ESP32 firmware only after the API contract is stable and the missing wiring/clock/transport details have been confirmed. **Delivered as CP-09** in `firmware/`: `main.py`, `logic.py`, `mfrc522.py`, `config.example.py`, 29 hardware-free logic tests, and `README.md`. Wiring is bench-confirmed: SPI bus 2 at 1 MHz with SCK 18, MOSI 23, MISO 19, CS 5, RST 22; HC-SR04 TRIG 26 and ECHO 25. Firmware queued events keep their `event_id` across a reboot, and no `timestamp` is sent until NTP synchronises, so an unsynced device never looks authoritative.

Keep code delivery in chat unless explicit permission to edit code files is given. This documentation revision does not complete any backend implementation milestone.

## 12. Configuration

Backend defaults:

- `movement_gap_seconds = 20`;
- `closure_sweep_seconds = 5`;
- `plan_match_window_minutes = 60`;
- `validation_policy = RFID_PRIMARY_COUNTS_V4`;
- `device_auth_cache_seconds = 60`;
- `ingest_max_batch_size = 50`;
- `login_throttle_rate = 10/min` per client IP;
- `session_cookie_age = 12 hours` (one yard shift).

Firmware retains configurable ultrasonic filtering, threshold/hysteresis, minimum occupied/clear intervals, RFID cooldown, and bounded queue/retry settings. A starting same-UID absence/cooldown is 3 seconds; refresh its last-seen time on every successful raw read. The implemented defaults live in `firmware/config.example.py`: 140 cm enter / 150 cm exit hysteresis, 3 occupied and 4 clear samples, 120 ms minimum passage, 30 s maximum open passage, 3 s UID cooldown, 2000-event queue, batches of 25.

There is **no required RFID-to-ultrasonic pairing window**. Maintain clear gaps for reliable interruption counts and keep evidence within the intended movement bucket; do not reintroduce per-unit correlation as a prerequisite for validation or inventory.
