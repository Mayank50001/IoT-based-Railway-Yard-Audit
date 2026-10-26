# Railway Yard IoT — Backend Implementation Log

**Started:** 2026-10-25
**Authoritative policy:** [RFID-Primary Specification — v4](railway_station_yard_iot_project_context_v4.md)
**Companion documents:** [Backend Handoff](railway_station_yard_iot_backend_handoff.md) · [v3 (historical)](railway_station_yard_iot_project_context_v3.md)

This log is the running record of **what was built, why it was built that way, how it was verified, and what is still open**. It is written so the project report, a future session, or another developer can reconstruct the backend without reading the chat transcript.

Following `AGENTS.md`, application code is delivered in chat unless code-file edits are explicitly authorized. This log records each delivery, its purpose, and its verification steps.

---

## 1. How to read this log

- **CP-xx** = one checkpoint of delivered code.
- Status values: `DELIVERED` (code given, not yet applied), `APPLIED` (code in project, not yet verified), `VERIFIED` (checked by the user with the stated command).
- **D-xx** = an engineering decision with its rationale and the alternative that was rejected.
- Nothing in this log claims that the assistant ran the user's project. Verification is attributed to the user where that is the case.

---

## 2. Status board

| Milestone | Scope | Status |
|---|---|---|
| M0 — Foundation | Settings, UTC storage, `/health` endpoint, health tests | **VERIFIED** (2026-10-25, user-reported) |
| M1 — Domain models | Stations, staff, devices, rolling stock, plans, assignments, movements, events, findings, inventory, alerts | **VERIFIED** (2026-10-25, applied and tested at the user's request) |
| M1b — Seed data | Repeatable demo station, shared lane, fleet, three plans, initial lane mode | **VERIFIED** (2026-10-25, CP-03) |
| M2 — Ingestion | Authenticated single/batch event endpoint, UID canonicalisation, idempotency | **VERIFIED** (2026-10-25, CP-04) |
| M3 — Movement bucket | 20-second receive-time gap, idempotent closure, sweep command | **VERIFIED** (CP-04; closure validates since CP-06) |
| M4 — Validation | Direct/fallback plan match, distinct type counts, ultrasonic warning, findings | **VERIFIED** (2026-10-25, CP-06) |
| M5a — Authentication & lane control | Session login/logout/me, station scoping, roles, audited lane mode, `create_staff` | **VERIFIED** (2026-10-25, CP-07a; 122 tests then) |
| M5b — Inventory & read APIs | Frozen pre-movement inventory effects, station-scoped movement/finding/alert/inventory reads | **VERIFIED** (2026-10-25, CP-07b; 154 tests) |
| M6 — Dashboard | Django-served pages over the read APIs: overview, lanes, movements, movement detail, inventory, alerts; IST display and live refresh | **VERIFIED** (2026-10-26, CP-08; 169 tests, assistant-run) |
| M7 — Firmware | ESP32 `main.py`: independent RFID/passage events, off-line queue, NTP-optional clock, documented retry policy | **APPLIED** (2026-10-26, CP-09; 29 logic tests and a live HTTP check pass, hardware run pending) |
| M8 — Rehearsal | Real sensors plus seeded master data, no simulator | Planned |

---

## 3. Verification evidence

### M0 — Foundation

Delivered: `/health` returning `status`, `database`, `movement_gap_seconds`, `validation_policy`;
503 on database failure; GET-only; three tests.

User ran the tests and reported them passing. Health payload is expected to be:

```json
{
  "status": "ok",
  "database": "connected",
  "movement_gap_seconds": 20,
  "validation_policy": "RFID_PRIMARY_COUNTS_V4"
}
```

### M1 — Domain models

Applied to the project on 2026-10-25 after the user reviewed the code, then verified locally by the assistant:

```text
$ python manage.py makemigrations yard
  yard/migrations/0001_initial.py  (11 models, 8 unique/check constraints, 10 indexes)

$ python manage.py migrate
  Applying yard.0001_initial... OK

$ python manage.py test yard
  Ran 15 tests in 0.246s
  OK

$ python manage.py test backend
  Ran 3 tests
  OK

$ python manage.py check
  System check identified no issues (0 silenced).
```

Two findings from actually running it:

1. **Installed Django is 6.1.2, not 4.2.** The scaffold header still says 4.2.16. In Django 6, `CheckConstraint(check=...)` was replaced by `condition=...`; the models use `condition=`.
2. **PBKDF2 hashing costs ~1.3 seconds per call on this machine** (Django 6 default: 1.2M iterations). The first test run took 50 seconds purely from hashing. Tests now override `PASSWORD_HASHERS` to MD5, bringing the suite to 0.25 seconds. Production code still uses the strong default; the cost is an ingestion-design input, see D-17.

### M1 revision — lanes instead of entry/exit devices (CP-02b)

The user rejected classifying devices as entry-only or exit-only: a single physical gate must serve both directions without duplicate device records. Per-unit inventory inference was analysed and rejected (see D-18/D-19). Applied as migration `0002_lanemodechange_and_more`:

```text
$ python manage.py makemigrations yard
  yard/migrations/0002_lanemodechange_and_more.py

$ python manage.py migrate
  Applying yard.0002_lanemodechange_and_more... OK

$ python manage.py test yard
  Ran 20 tests in 0.290s
  OK

$ python manage.py test backend
  Ran 3 tests
  OK
```

Included in the revision: `Device.direction` removed, `Device.lane_mode` added, append-only `LaneModeChange` history, `Movement.direction_source` and `Movement.lane_mode_change`, and the open-movement uniqueness moved from `(station, direction, device)` to `(device,)`. The admin audit path is covered by a test that changes the mode through the real admin form.

### M1b — Demo seed data (CP-03)

`python manage.py seed_demo` creates or refreshes the NDLS demo. Verified:

```text
$ python manage.py seed_demo --api-key bench-demo-key
Demo seed complete.
  Station: NDLS - New Delhi
  Lane: gate-1 (lane_mode=ENTRY)
  Fleet: 2 locos, 6 coaches, 2 wagons
  Plans: NDLS-IN-BENCH, NDLS-IN-FULL, NDLS-OUT-FULL
  Device API key (set from --api-key, shown once): bench-demo-key

$ python manage.py test yard
  Ran 23 tests in 0.607s
  OK
```

Seeded plans:

| Plan | Direction | Lead loco | Expected | Assigned coaches |
|---|---|---|---|---|
| `NDLS-IN-BENCH` | `ENTRY` | `WDM3A-1001` (`FA37E63F`) | 1 loco + 1 coach | `NR-C-2201` (`0E78E13F`) |
| `NDLS-IN-FULL` | `ENTRY` | `WAP7-3020` (`A0A00002`) | 1 loco + 4 coaches | `NR-C-2202`–`2205` |
| `NDLS-OUT-FULL` | `EXIT` | `WAP7-3020` | 1 loco + 4 coaches | `NR-C-2202`–`2205` |

The bench plan is bound to the two tags the user physically owns, so a real two-unit demonstration is possible today. The 4-coach plans are for a fuller consist when more coach tags are available; they are not required for the two-tag bench. Plan status is intentionally **not** reset on re-seed, because a plan already used by a movement must keep its lifecycle. `--reset` deletes demo movements, inventory, and plans for NDLS only; master data and the lane key survive.

### M2/M3 — Ingestion and movement buckets (CP-04)

The `event_id` contract was confirmed by the user's instruction to continue with the recommendation, so it is now fixed as `<device_id>-<boot_id>-<sequence>`.

```text
$ python manage.py test yard backend
  Ran 50 tests in 1.451s
  OK

$ python manage.py check
  System check identified no issues (0 silenced).

$ python manage.py makemigrations --check --dry-run
  No changes detected
```

An end-to-end smoke test ran through the real URL configuration against the seeded dev database, inside a rolled-back transaction (so the demo data stayed untouched). Batch of two events:

```text
batch 200
{
  "device_id": "gate-1",
  "lane_mode": "ENTRY",
  "accepted": 2,
  "duplicates": 0,
  "rejected": 0,
  "results": [
    {"status": "accepted", "rfid_uid": "FA37E63F", "rolling_stock": "WDM3A-1001",
     "movement_id": 1, "movement_created": true},
    {"status": "accepted", "kind": "PHYSICAL_PASSAGE", "movement_id": 1,
     "movement_created": false}
  ]
}
retry 200 {'status': 'duplicate', 'movement_id': 1, ...}
wrong key 401 {'detail': 'Device credentials were not accepted.', 'code': 'DEVICE_AUTH_FAILED'}
```

Note the canonicalisation in that run: the request sent `fa:37:e6:3f` and the response echoed `FA37E63F`, resolved to `WDM3A-1001`.

New tests (24 in `yard/test_ingest.py`) cover: missing/wrong credentials, unknown and disabled devices, the auth cache and key rotation, unset lane mode, spoofed `device_id`, canonicalisation, unknown UIDs, the `kind` alias, passage metadata, duplicate retries, cross-device `event_id` conflict, per-event batch outcomes, oversized and malformed envelopes, the exact 20-second boundary, closure over 20 seconds, mode changes mid-consist, and the idle sweep.

---

## 4. Decision register

| ID | Decision | Why | Rejected alternative |
|---|---|---|---|
| D-01 | Distinct registered UID/type counts are the primary validation evidence | RFID answers *which* units were seen; ultrasonic only answers *how many* interruptions occurred. Comparing those two questions at movement level is defensible without per-unit guessing. | One-to-one RFID↔ultrasonic pairing (v3); fails on missed reads and merged passages. |
| D-02 | No pairing table, no pairing window, no greedy/order matching | Pairing adds a correctness requirement the hardware cannot guarantee and hides the real uncertainty. | v3 `paired_passage_event_id` and 2-second window. |
| D-03 | Ultrasonic disagreement is a movement-level `WARN`, not a validation failure | A complete, otherwise valid RFID composition should not be failed by an advisory sensor. | Pairing-dependent validation. |
| D-04 | Plan lookup: lead-locomotive RFID first, unambiguous coach-to-plan assignment second | A coach can carry its plan context when the loco tag is missed, without inventing a loco read. | Requiring the loco read; or guessing among conflicting assignments. |
| D-05 | Expected/inferred locomotive data is stored separately from observed evidence | A database lookup is not a detection. Conflating them would create fake observations and fake inventory transitions. | Treating the expected loco as observed. |
| D-06 | One `OPEN` movement per lane, enforced by a partial unique constraint | Concurrency-safe invariant: two simultaneous ingestion requests cannot open two buckets. Declared in the database, not only in Python. The key started as `(station, direction, device)` and was narrowed to `(device,)` by D-21. | Application-only check (race window). |
| D-07 | One movement per bound plan, enforced by a conditional unique constraint | v4 rule; prevents one plan being consumed twice. | Application-only check. |
| D-08 | `event_id` is `CharField(max_length=64)`, not `UUIDField` | The firmware event-id format is not fixed yet. A UUID field would reject valid device ids and force firmware changes. Same string on retry still gives idempotency. | `UUIDField` (premature contract). |
| D-09 | Device API keys stored with Django's password hasher (`make_password` / `check_password`) | A leaked database should not reveal working gate credentials. | Plaintext key column. |
| D-10 | Fleet data is national; station ownership applies to devices, plans, movements, inventory, alerts | Rolling stock moves between stations, so a station FK on `RollingStock` would be wrong. Yard presence comes from inventory rows, not from editable flags. | `RollingStock.station` + `entered`/`exited` booleans. |
| D-11 | `PROTECT` on station/device/plan owners, `SET_NULL` on historical references | Deleting a station or device must not silently erase audit history, but deleting a plan should not destroy closed movements. | `CASCADE` everywhere (data loss). |
| D-12 | Findings and alerts deduplicated by `(movement, code, subject)` | Revalidation must replace/refresh findings without duplicating alerts. `subject` (usually a UID or unit number) allows one finding per offending unit. | Free-text-only findings (duplicates on retry). |
| D-13 | Movement stores an expected-count snapshot (`expected_locos/coaches/wagons`, `expected_lead_loco`) | Plans can be edited later; a closed movement must keep the expectation it was judged against. | Reading expectations back from the live plan. |
| D-14 | Inventory rows keyed by `(station, rfid_uid)`, unknown UIDs retained | Unknown tags are real observations; discarding them loses evidence. One row per tag per station gives idempotent presence updates. | Rows only for known stock; or one row per event. |
| D-15 | `validation_policy` recorded per movement | Historical movements stay explainable after the policy changes. | Global policy only. |
| D-16 | App label `yard`; models split by domain area inside one `models.py` | Small team, one bounded context; a package split adds import indirection with no benefit at this size. | One app per entity. |
| D-17 | Device-key authentication will be cached per request, not re-hashed per event | PBKDF2 verification measured at ~1.3 s. A batch of 50 events would cost 65 s if the key were checked per event. One check per HTTP request plus a short-lived verification cache keeps ingestion responsive without weakening storage. | Hashing per event; or replacing the hasher with a fast digest. |
| D-18 | A device is a physical lane; direction belongs to the movement, not the device | One gate must serve entry and exit without registering two devices for the same hardware. Keeps station-level inventory and avoids the operational overhead the user objected to. | Fixed `Device.direction` (needs duplicate device records per gate). |
| D-19 | Direction is an explicit, audited operator choice — never inferred from inventory state | Inference creates a feedback loop: direction comes from inventory, inventory is updated by direction. One missed read then inverts a unit permanently, and a consist with mixed/stale states splits into two movements. | Per-unit OUT→IN / IN→OUT flip on each read. |
| D-20 | `lane_mode_changes` is append-only; each movement links to the exact change that authorised it | Answers "who declared this an entry?" without trusting the device payload. Mode history cannot be rewritten from the admin. | Storing only the current mode on the device. |
| D-21 | Open-movement uniqueness is per device, not per `(station, direction, device)` | A mode change mid-consist must not open a second bucket on the same lane. The open movement keeps its direction; the new mode starts with the next movement. | Including direction in the lane key. |
| D-22 | One transaction per event; per-event results; whole-request rejection only for credentials, envelope, identity claims, or lane mode | A single malformed event must not poison a queue: rejecting the batch would make the firmware retry it forever. Conversely, a spoofed `device_id` or an unset lane is not a per-event accident, and must store nothing at all. | All-or-nothing batches (poison batch); best-effort batches without a spoof check (half-applied spoof). |
| D-23 | Device-key verification cached for 60 s, keyed by a SHA-256 of `(device_id, key)` and fingerprinted with the stored hash | PBKDF2 costs ~1.3 s. Caching the *outcome* keeps a 50-event batch fast; storing the hash fingerprint makes key rotation invalidate the cache immediately without a purge. | Re-hashing per event; caching the raw key. |
| D-24 | `event_id` contract is `<device_id>-<boot_id>-<sequence>`; the server enforces global uniqueness and treats cross-device reuse as 409 | `boot_id` survives a reboot only if the firmware persists it with the queue, which is exactly what makes a retried event keep its id. Global uniqueness also catches two boards provisioned with the same sequence space. | Server-assigned ids (breaks offline queueing); timestamp-only ids (need a synced clock). |
| D-25 | Closure mechanics are separate from validation; a closed movement keeps `validation_result = NULL` plus an INFO `VALIDATION_PENDING` finding until CP-06 | Closing a bucket must not require the validation service to exist, but the gap must be visible. NULL honestly means "not validated", and CP-06 can find every such movement and replace the findings atomically. | Marking closed movements `VALID` by default (lies); `UNVERIFIED` by default (wrong meaning). |
| D-26 | A batch shares one server receive instant, and `last_evidence_at` never moves backwards | A firmware queue drain arrives as one request; giving each event its own increasing timestamp would let a drain straddle a bucket boundary artificially. The backwards guard absorbs clock corrections and late replay. | Per-event wall clock inside a batch; overwriting `last_evidence_at` unconditionally. |
| D-27 | Findings are upserted by `(movement, code, subject)` and stale ones pruned; alerts are created once and never deleted | Revalidation must correct the current picture without losing when a finding was first seen, and without erasing an acknowledgement, which is an audit fact. Deletion would reset `created_at` and could delete a notification someone already acted on. | Delete-and-recreate findings (loses first-seen time); delete-and-recreate alerts (loses acknowledgements). |
| D-28 | Validation runs inline at closure, inside the closure transaction | A closed movement's evidence is final at that instant, so the outcome should exist the moment the dashboard can see it. If validation raises, the closure rolls back and the sweep retries, so an unvalidated closed movement cannot be left behind by a crash. | Async/background validation (visible window with `NULL` outcome); a placeholder finding (what CP-04 did). |
| D-29 | Plan lifecycle is `SCHEDULED → ACTIVE` at bind, `COMPLETED` at closure, and a movement's own plan is re-admitted on revalidation | Follows v4 §5 literally. Because v4 resolves plans at closure, the two status writes happen in one transaction; that is expected, not a bug. Re-admission is required because the plan is already `COMPLETED` when revalidation runs, and dropping it would unbind the plan. | Leaving the plan `COMPLETED` and excluding it from candidates (revalidation would unbind); binding straight to `COMPLETED` (deviates from the stated lifecycle). |
| D-30 | `LOCOMOTIVE_MISMATCH` is raised only when the plan names **every** expected locomotive; a partial roster is count-only, and `UNAUTHORIZED_MOVEMENT` stays reserved | A freight may legitimately run with two or three locomotives while the plan names only the lead; failing the unnamed extra would reject a normal consist. But a plan that names all of them has claimed their identities, and a different one must not pass just because the count matches. `COUNT_MISMATCH` only proves the number, so identity needs its own signal. `UNAUTHORIZED_MOVEMENT` remains unused because it is too vague to distinguish these cases. | Treating any non-lead locomotive as a mismatch (breaks multi-loco freight); never checking locomotive identity (a wrong second loco passes with a correct count). |
| D-34 | Locomotive identity is stored as plan-scoped `PlanAssignment` rows, never as a boolean on `RollingStock` | The same unit hauls different rakes on different days, so "is hauling" is a per-movement fact. A static flag would be permanently wrong for some movement and could not say *which* rake, reproducing the `entered`/`exited`-on-master-data mistake the project already rejected. Plan assignments are dynamic, historical, and already carry coaches and wagons. | A `hauls_trains`/`is_hauling` boolean on `RollingStock`; inferring the hauling unit from inventory or from the first locomotive read. |
| D-35 | Yard masters authenticate with a Django session cookie plus CSRF, not per-user API tokens | The dashboard is same-origin and Django-served, so the browser already has the machinery. A token in storage would be a second credential to protect, could not be revoked as simply as deactivating the account, and would make CSRF protection optional rather than automatic. | JWT/DRF token authentication for humans; cookies without CSRF. |
| D-36 | Every account is bound to exactly one station through `StaffProfile`; `ADMIN` is station-scoped, with no cross-station exception | "Administrator" in a station yard means full rights *there*, not visibility into every station. A cross-station admin would make the station boundary unprovable and is not needed for the prototype. | A global administrator role; leaving scoping to each view's discipline. |
| D-37 | A foreign station's object returns 404, never 403 | A 403 confirms that the object exists at another station. A 404 is indistinguishable from a missing id, so the API does not leak the shape of the network. | 403 with a "wrong station" message. |
| D-38 | The global DRF default is `IsAuthenticated`; only `/health` and ingestion opt out | Failing closed means a forgotten permission on a future view exposes nothing. Ingestion must opt out explicitly because it uses device keys; `/health` is a plain Django view and is public by construction. | Default `AllowAny` and per-view protection (a forgotten view becomes public). |
| D-39 | `VIEWER` is read-only; `YARD_MASTER` and station `ADMIN` may change lane mode, and every change records `set_by` | A viewer is an observer, not an operator. The audit trail is only useful if the actor is the authenticated account, never a request-supplied name. | Letting viewers annotate; accepting `set_by` from the payload. |
| D-40 | A lane-mode change while a movement is open is accepted; the open movement keeps its direction and the new mode applies to the next movement | This is v4 §3.4 and the acceptance table. Rejecting the change (`409 LANE_BUSY`) would be stricter than the specification and would block a correction mid-consist; the response makes the distinction explicit with `applies_to: "NEXT_MOVEMENT"` and the still-open movement. | Rejecting while open; rewriting the open movement's direction. |
| D-41 | Repeating the current lane mode is a no-op with no audit row; a disabled lane returns `409 LANE_INACTIVE` | An audit row should record a change, not a confirmation. A disabled lane cannot accept events anyway, so silently accepting a mode would suggest it is ready. | Writing an audit row for every submission; allowing a disabled lane to look configured. |
| D-42 | Login is throttled per socket IP (`REMOTE_ADDR`), not per `X-Forwarded-For`; 10 attempts per minute | DRF's default trusts a client-controlled header, so a spoofed value resets the budget and defeats the throttle. The prototype is reached directly on the LAN. A reverse proxy would require a validated forwarded header instead. | Trusting `X-Forwarded-For` blindly; per-username throttling (enables spraying one password across accounts). |
| D-43 | The Django admin site remains a superuser operations console and is not station-scoped; `create_staff` does not grant it | Scoping every admin model would be a large change for a surface the dashboard does not use. The honest boundary is explicit: dashboard roles do not grant admin access, and anyone given `is_staff` knowingly has global access. | Pretending the `ADMIN` app role also secures the admin site; silently granting `is_staff` to make the demo convenient. |
| D-44 | CSRF failures are normalised to `403 CSRF_FAILED`, and a logged-out request is `401 NOT_AUTHENTICATED` | The dashboard needs to distinguish "fetch a new token" from "log in" from "your role forbids this". DRF's defaults collapse the first two into `403 PERMISSION_DENIED` or `403` for a missing session. | Leaving DRF's generic codes; treating a missing session as 403. |
| D-45 | Inventory freezes `status_before` per distinct UID in a `MovementInventoryEffect` row before applying any transition | v4 requires anomalies to be judged against the pre-movement state, and revalidation must not mistake its own write for a new entry/exit. An explicit ledger makes "applied once" a database fact and gives the report a per-movement transition history. | Reading the live inventory row during validation; a JSON snapshot on the movement that cannot be queried or audited. |
| D-46 | `last_movement` and `since` name only the movement that changed presence, not every movement that observed the unit | If a no-change observation claimed the row, a later revalidation could not tell who established the status, and an already-present entry would rewrite the provenance of a clean earlier entry. Presence provenance is the useful fact. | Updating `last_movement` on every observation; leaving `last_movement` null forever. |
| D-47 | Known UID verification follows the movement that established presence and is `VERIFIED` only for a `VALID` movement | A later flagged movement should not strip verification from stock that entered cleanly, and a corrected revalidation should fix its own row. Tying verification to presence provenance gives both. | Downgrading the row on every flagged observation; never refreshing verification after a corrected revalidation. |
| D-48 | Validation resolves rolling-stock identity from the current UID registry, with the event's stored snapshot as fallback | The observed UID is the evidence; the registry is the current truth. A late registration then corrects an earlier `UNKNOWN_RFID` on revalidation instead of freezing a mistake. `RollingStock.rfid_uid` is unique, so the lookup is unambiguous. | Trusting the event snapshot forever; rewriting historical events when master data changes. |
| D-49 | Movement reads close idle buckets first, accepting a write inside some GETs | Without a scheduler the dashboard would show an `OPEN` movement minutes after its last event, and its inventory would not reflect the closure. The sweep remains the normal path; lazy closure is the demo-safe fallback. | Requiring a scheduler before the dashboard is honest; showing unvalidated open movements indefinitely. |
| D-31 | `DIRECTION_CONFLICT` is only raised for `ENTRY`; exit anomalies use per-unit `UNEXPECTED_EXIT` | On an exit, "not recorded inside" is already a per-unit failure, so an additional aggregate warning would say the same thing twice. The v4 scenario that motivates `DIRECTION_CONFLICT` is stock already in the yard being "entered" again. | Raising `DIRECTION_CONFLICT` for both directions (double signalling). |
| D-32 | Inventory anomaly checks read the current inventory row as the pre-movement state | At CP-06 that was correct because no inventory transitions were applied. The requirement it established — freeze the pre-movement state before the first transition — was implemented in CP-07b as `MovementInventoryEffect` (D-45). | Guessing the pre-state from `last_movement` (ambiguous when a UID moves twice). |
| D-33 | Retired (`is_active = False`) stock is excluded from observed counts and reported as `RETIRED_ROLLING_STOCK` | A retired unit must not silently satisfy an expected count, but its tag being read is still evidence worth surfacing. | Counting retired stock normally; silently ignoring it. |
| D-50 | Dashboard pages are thin Django shells; browser code reads the existing station-scoped API instead of server-side views duplicating its queries | The read API already owns scoping, pagination, lazy closure, and serialisation. Rendering a second queryset path in templates would create two places to keep the station boundary correct, and a mistake in the quieter one would be invisible in API tests. One contract also means the firmware, dashboard, and any future client see identical facts. | Duplicating the ORM queries in `TemplateView`s; adopting a separate SPA build toolchain for a prototype. |
| D-51 | Page access mirrors the API: anonymous → login redirect with `next`; missing/inactive profile → an explanatory HTML 403 with the API code; foreign movement → 404 before JavaScript runs | The dashboard must not become a weaker access path than the JSON surface. A denied page that says `NO_STAFF_PROFILE` is more useful than a shell that later fills with API errors, and resolving the movement server-side prevents a foreign id from rendering even briefly. | Client-only guards (a page that flashes before redirecting); redirecting every error to login. |
| D-52 | The simulator is dropped. The demonstration uses real sensors and seeded master data; CP-08 became the dashboard, and firmware remains the next code checkpoint | The user explicitly needs to demonstrate the physical sensing path, not a scripted replay. Keeping an unverified simulator would add a command that can stall during the demo and would create non-physical rows in the yard database. Seeded master data supplies plans and stock identities without pretending to be sensor evidence. | An acceptance-scenario simulator; seeding fake movements and calling them sensor data. |
| D-53 | The browser formats timestamps explicitly in `Asia/Kolkata`; storage and API stay UTC | v4 requires "store UTC, display IST". Relying on the laptop's timezone makes the same page show different times on different machines, and labelling an unsynchronised clock as UTC would be false. An explicit formatter keeps the display policy in one place. | Browser-local time; formatting dates on the server in a fixed offset while storage stays UTC. |
| D-54 | Live pages poll the read API every 5 s (overview, lanes, movements, open movement detail) or 10 s (inventory, alerts), paused while the tab is hidden | A websocket adds a channel layer, reconnect handling, and a second update path for a single-yard prototype. Polling reuses the authenticated API, and the movement-detail poll stops once the movement is `CLOSED`, so final data costs nothing. The trade-off is up to 20 requests per minute per open tab. | WebSockets/SSE; manual refresh only (the audience would watch a stale screen while sensors fire). |
| D-55 | Dashboard writes remain limited to lane mode and alert acknowledgement; plan reassignment stays an admin operation | Both dashboard writes already have audited endpoints and role checks. Adding inline plan editing would create a new mutation path with no acceptance scenario, while the admin remains the deliberate manual workaround for a wrong bind. | A dashboard plan editor; hiding the wrong-bind problem. |
| D-56 | `GET /api/movements` and detail expose `direction_source` so the UI can show whether direction came from the lane mode, a plan, or inventory inference | v4 makes direction an audited operator fact. Showing only `ENTRY`/`EXIT` loses that provenance, and a future `INFERRED` value would be indistinguishable from an operator decision. This is a read-only field with no migration. | Leaving the source out and asking the operator to trust the label. |
| D-57 | The firmware emits `RFID_DETECTED` and `PHYSICAL_PASSAGE` as independent events and never pairs them | v4 validates on distinct registered UIDs and treats passage counts as advisory. If the device paired a passage with a card read, a missed locomotive could be filled in by the device and `MISSING_LOCOMOTIVE_RFID` would stop being observable. Keeping the streams separate also lets the server apply the lane direction and plan window, which the device does not know. | Pairing on the device; one composite "vehicle passed with UID" event. |
| D-58 | Queued events are one JSON file per event, written to `.tmp` and renamed, bounded at 2000 with oldest-first loss | Rename is the only atomicity primitive MicroPython offers, and a single index file is a single point of corruption. Per-file records survive a power cut mid-write and let `event_id` be fixed at enqueue, so a reboot does not rename pending events. Dropping the oldest on overflow keeps the train currently at the gate, which is what a demo needs. | One JSON-lines log; an in-RAM queue; dropping the newest on overflow. |
| D-59 | Events carry no `timestamp` until NTP has synchronised; the server's `received_at` orders everything | A cold-boot clock reads 2000-01-01, and a wrong but plausible-looking device timestamp is worse than none in an audit trail. Movements are bucketed by server receive time by decision, so the device clock is optional metadata. | Setting the clock from `time.time()` at boot; refusing to emit until synced. |
| D-60 | Retry delays depend on the failure: 409 → 5 s, 401/403 → 60 s, other 4xx/429 → 30 s, 5xx/transport → exponential to 30 s | A 409 is one operator click away from working, so five seconds is right. A 401 will not fix itself in five seconds and hammering it wastes radio time and fills the log. Rejected events are deleted rather than retried, because a malformed event never becomes well-formed. | One fixed retry interval for everything; dropping the queue on any error. |
| D-61 | Ultrasonic detection uses hysteresis (enter 140 cm, exit 150 cm) with sample confirmation, and treats a missing echo as "unknown", never "clear" | Readings hovering at a single threshold would chatter and produce several passages per vehicle. Requiring four clear samples rejects a single dropout, and holding an open passage through missing echoes prevents a dead sensor from fabricating an exit and splitting one movement into two. | A single distance threshold; treating a timeout as clear. |
| D-62 | The HC-SR04 Echo line stays wired directly to GPIO 25, with the out-of-spec 5 V condition documented rather than blocked | The pin is not 5 V tolerant, but this exact wiring has run on the bench for roughly two years without a failure, and the user needs the rig working rather than rewired. A 1 kΩ series plus 2 kΩ to ground divider is documented in the firmware README as the containment. | Refusing to ship until a divider is fitted; a level shifter on a breadboard. |
| D-63 | The firmware reads 4-byte UIDs only, and the backend's 4–32 hex character rule is unchanged | `anticoll` at cascade level 1 covers the bench tags. Adding cascade level 2 would be untested code in the demo path, and the server already accepts longer UIDs if a future driver produces them. | Implementing both cascade levels now; narrowing the API to 4-byte UIDs. |

---

## 5. Data model map (current)

| Model | Table | Purpose | Key rules |
|---|---|---|---|
| `Station` | `yard_station` | Station master data; optional division/zone | `code` unique |
| `StaffProfile` | `yard_staffprofile` | Links a Django user to a station and role | One profile per user |
| `Device` | `yard_device` | Registered physical lane (ESP32) | `device_id` unique; hashed API key; `lane_mode` set by an operator |
| `LaneModeChange` | `yard_lanemodechange` | Append-only audit of lane direction decisions | Records direction, `set_by`, time, note |
| `RollingStock` | `yard_rollingstock` | Fleet master data: loco/coach/wagon | `rfid_uid` canonical + unique; `number` unique |
| `MovementPlan` | `yard_movementplan` | Expected movement: lead loco + counts + window | `plan_code` unique; at least one expected unit |
| `PlanAssignment` | `yard_planassignment` | Current coach/wagon/loco ↔ plan context | Unique `(plan, rolling_stock)` |
| `Movement` | `yard_movement` | One passage of a consist through one lane | One `OPEN` per device; one movement per plan; outcome separate from state |
| `MovementEvent` | `yard_movementevent` | Immutable accepted observation (`RFID_DETECTED` / `PHYSICAL_PASSAGE`) | `event_id` globally unique; kind/UID consistency check |
| `MovementFinding` | `yard_movementfinding` | Stored explanation produced at closure/revalidation | Unique `(movement, code, subject)` |
| `InventoryRecord` | `yard_inventoryrecord` | Current yard presence per station + UID | Unique `(station, rfid_uid)`; `VERIFIED`/`UNVERIFIED`; `last_movement` names the movement that set the status |
| `MovementInventoryEffect` | `yard_movementinventoryeffect` | Frozen pre-movement state and the one transition authorised for each distinct read UID | Unique `(movement, rfid_uid)`; `status_before` immutable; `applied_at` set once |
| `Alert` | `yard_alert` | Operator-facing alert raised from a finding | Unique `(movement, code, subject)`; acknowledgement fields; never deleted |

Derived, not stored: `rfid_observation_count`, `unique_rfid_count`, per-type observed counts, and `physical_passage_count`. They are computed from `MovementEvent` rows at closure, so no denormalised counters can drift.

---

## 6. Checkpoint CP-02 — code inventory

| File | Change | Purpose |
|---|---|---|
| `backend/yard/uid.py` | new | `canonical_uid()` + validator: strips separators, uppercases, rejects non-hex/odd-length UIDs, preserves leading zeros |
| `backend/yard/models.py` | new (replaces stub) | All domain models, choices, constraints, indexes |
| `backend/yard/admin.py` | new (replaces stub) | Admin registration; write-only API-key field for devices |
| `backend/yard/tests.py` | new (replaces stub) | UID normalisation + constraint tests |
| `backend/backend/settings.py` | edit | Add `rest_framework`, `yard`; env-driven `ALLOWED_HOSTS`; closure/window settings |
| `backend/yard/migrations/0001_initial.py` | generated | Schema for the eleven models |

On 2026-10-25 the user reviewed CP-02 and authorized direct file edits, so the files above were applied to the project rather than only pasted. This is the only blanket authorization so far; confirm before editing code in future checkpoints.

## 6a. Checkpoint CP-02b — lane mode revision

| File | Change | Purpose |
|---|---|---|
| `backend/yard/models.py` | edit | `Device.direction` removed; `Device.lane_mode` added; `LaneModeChange` model; `Movement.direction_source` + `lane_mode_change`; open-movement constraint now per device |
| `backend/yard/services.py` | new | `set_lane_mode()` — validates the direction, updates the lane, appends the audit row |
| `backend/yard/admin.py` | edit | Device form uses `lane_mode`; mode changes from admin are audited; `LaneModeChange` is read-only |
| `backend/yard/tests.py` | edit | Lane-mode tests plus an admin-path audit test (20 tests total) |
| `backend/yard/migrations/0002_lanemodechange_and_more.py` | generated | Schema revision |

The user selected "lane mode + operator selection" from three options after the inventory-inference analysis. `Movement.direction_source` also reserves `PLAN` and `INFERRED`, but only `OPERATOR` is implemented.

## 6b. Checkpoint CP-03 — seed command

| File | Change | Purpose |
|---|---|---|
| `backend/yard/management/commands/seed_demo.py` | new | Idempotent demo seed: station, lane + initial mode audit, 10 fleet units, 3 plans, 9 coach assignments |
| `backend/yard/management/__init__.py`, `.../commands/__init__.py` | new | Command package |
| `backend/yard/tests.py` | edit | Idempotency, key rotation, and `--reset` tests |

Command options: `--api-key` (set a known key), `--rotate-key` (generate and print a new one), `--reset` (delete demo movements/inventory/plans for NDLS). Re-running without `--reset` never duplicates rows.

## 6c. Checkpoint CP-04 — ingestion and buckets

| File | Change | Purpose |
|---|---|---|
| `backend/yard/auth.py` | new | `authenticate_device()`: header parsing, hashed-key verification, 60 s success cache invalidated by hash rotation |
| `backend/yard/exceptions.py` | new | Typed API errors with stable codes (`LANE_MODE_NOT_SET`, `DEVICE_INACTIVE`, `EVENT_ID_DEVICE_MISMATCH`, …) |
| `backend/yard/serializers.py` | new | Event wire format: `event_type`/`kind`, `event_id` pattern, UID canonicalisation, passage metadata, per-field error codes |
| `backend/yard/ingest.py` | new | Per-event transaction: duplicate resolution, rolling-stock lookup, storage, typed result |
| `backend/yard/movements.py` | new | Bucket assignment (gap, mode capture, monotonic evidence), idempotent `close_movement()`, `close_idle_movements()` |
| `backend/yard/views.py` | replaced stub | `POST /api/ingest/events`: envelope handling, identity-claim pre-check, per-event results, `last_seen_at` |
| `backend/yard/urls.py`, `backend/backend/urls.py` | new/edit | `/api/ingest/events` (with and without trailing slash) |
| `backend/yard/api.py` | new | DRF exception handler that exposes `code` in the response body |
| `backend/yard/clock.py` | new | `server_now()` so tests control receive time without sleeping |
| `backend/yard/management/commands/sweep_movements.py` | new | Periodic closure of idle movements |
| `backend/yard/test_ingest.py` | new | 24 ingestion/bucket tests |
| `backend/backend/settings.py` | edit | `DEVICE_AUTH_CACHE_SECONDS`, `INGEST_MAX_BATCH_SIZE`, DRF exception handler |

No migration was needed: the CP-02 schema already carried every field ingestion writes.

A captured batch shares one receive instant. If the clock reports an event older than the lane's last evidence, `last_evidence_at` is left alone rather than moved backwards.

## 6d. Checkpoint CP-06 — RFID-primary validation

| File | Change | Purpose |
|---|---|---|
| `backend/yard/validation.py` | new | Composition, plan resolution, findings, outcome, persistence, alerts |
| `backend/yard/movements.py` | edit | `close_movement()` now validates inside the closure transaction; the `VALIDATION_PENDING` placeholder is gone |
| `backend/yard/management/commands/revalidate_movements.py` | new | Backfills movements closed under CP-04, and revalidates on demand |
| `backend/yard/admin.py` | edit | Derived validation fields are read-only (`plan` stays editable as the manual rebind workaround) |
| `backend/yard/test_validation.py` | new | 34 validation tests |
| `backend/yard/test_ingest.py` | edit | Closure expectations updated: an outcome now exists immediately |

No migration: every field validation writes already existed.

**Composition** is a distinct-identity view of stored events. Repeated logical observations are kept as evidence and counted once. Retired stock is excluded from counts (D-33). Unknown UIDs are retained and reported.

**Plan resolution** first tries the expected lead locomotive, taking the nearest `expected_start` when several plans match and recording `PLAN_AMBIGUOUS` when it has to break a tie. Only if that fails does it use plan assignments, and only when every usable assignment points at one eligible plan (D-29, D-30). No read means no fabricated observation.

**Locomotive identity** is claimed per unit named by the plan: the lead, plus any locomotive in `PlanAssignment`. When the roster covers `expected_locos`, an observed locomotive outside it is `LOCOMOTIVE_MISMATCH`; each named locomotive that was not read is `MISSING_LOCOMOTIVE_RFID`. A partial roster (lead only, two locomotives expected) is counted but not identified, so an ordinary two- or three-loco freight stays valid (D-30, D-34).

**Findings** stored per movement:

| Code | Severity | Meaning |
|---|---|---|
| `UNKNOWN_RFID` | FAIL | UID not registered |
| `RETIRED_ROLLING_STOCK` | FAIL | Registered but inactive |
| `UNPLANNED_MOVEMENT` | FAIL | No eligible plan bound |
| `PLAN_AMBIGUOUS` | WARN | Several plans matched; none was guessed |
| `LOCOMOTIVE_MISMATCH` | FAIL | A single-loco plan saw a different locomotive |
| `MISSING_LOCOMOTIVE_RFID` | FAIL | Expected lead locomotive was not read |
| `COUNT_MISMATCH` | FAIL | Distinct known composition differs from the plan |
| `DUPLICATE_OBSERVATION` | WARN | Same UID read more than once; counted once |
| `ULTRASONIC_COUNT_MISMATCH` | WARN | Passages disagree with distinct UIDs; advisory only |
| `DIRECTION_CONFLICT` | WARN | Most observed units were already in the yard on an `ENTRY` |
| `ENTRY_ALREADY_PRESENT` | WARN | That UID was already recorded inside |
| `UNEXPECTED_EXIT` | FAIL | That UID was never recorded inside |

Outcome: no known UID → `UNVERIFIED`; otherwise any FAIL → `FLAGGED`; otherwise `VALID`.

End-to-end smoke test through the real URL configuration, inside a rolled-back transaction against the seeded database (plan `NDLS-IN-FULL`, five UIDs, five passages, one repeated UID):

```text
batch 200 accepted 11
movement 1 CLOSED -> VALID | plan: NDLS-IN-FULL | source: LOCOMOTIVE_RFID
  WARN DUPLICATE_OBSERVATION C0A00002 | UID C0A00002 was read 2 times...
plan status COMPLETED
after rollback: movements 0 | plan status SCHEDULED
```

The CP-04 backfill was exercised the same way:

```text
would validate #1 ENTRY gate-1 at 2026-10-25 16:47:14
1 movement(s) would be validated.
Validated 1 movement(s): VALID 1.
findings ['ULTRASONIC_COUNT_MISMATCH']
pending left 0
```

### Plan window vs demo timing

`expected_start` is written by `seed_demo` at seed time and plans are matched within `PLAN_MATCH_WINDOW_MINUTES` (60). A demo an hour after seeding therefore produces `UNPLANNED_MOVEMENT` — correct behaviour, surprising in rehearsal. Rehearsal step: `python manage.py seed_demo --reset`, which both refreshes `expected_start` and returns the plans to `SCHEDULED` (a used plan is `COMPLETED` and cannot bind again).

### Concurrent plan binding

Two lanes closing against the same plan at the same instant would violate `one_movement_per_plan`. The `IntegrityError` surfaces inside the ingestion transaction, whose retry loop then re-runs closure; by then the plan is bound elsewhere, so the second movement validates as `UNPLANNED_MOVEMENT`. The unique constraint is the arbiter, and the event is still stored exactly once. This is a consequence of D-22's retry, not a special case in validation.

---

## 6e. Checkpoint CP-07a — dashboard authentication and lane control

| File | Change | Purpose |
|---|---|---|
| `backend/yard/access.py` | new | Session authenticator, `staff_profile_for()` / `station_for()`, station-scoped 404 helper, `IsStationStaff` / `IsYardOperator`, CSRF helper |
| `backend/yard/auth_views.py` | new | `csrf`, `login`, `logout`, `me` |
| `backend/yard/lane_views.py` | new | Station-scoped lane list/detail and the audited mode change |
| `backend/yard/throttles.py` | new | Per-IP login throttle keyed on the socket address |
| `backend/yard/serializers.py` | edit | Login and lane serializers; key material is never serialised |
| `backend/yard/exceptions.py` | edit | Typed 401/403/409 codes for the session and lane surfaces |
| `backend/yard/urls.py` | edit | Auth and lane routes, with optional trailing slash |
| `backend/backend/settings.py` | edit | Session authentication, fail-closed permission default, session age, login rate |
| `backend/yard/views.py` | edit | Ingestion explicitly opts out of session auth and `IsAuthenticated` |
| `backend/backend/views.py` | edit | `/health` documented as a plain, public Django view |
| `backend/yard/management/commands/create_staff.py` | new | Create or update a station-bound dashboard account |
| `backend/yard/test_auth.py` | new | 38 authentication, scoping, role, lane, and command tests |

No migration: authentication reuses `StaffProfile` and lane reads reuse existing fields.

### Contract

```text
GET  /api/auth/csrf                 -> {"csrfToken": "..."} plus the CSRF cookie
POST /api/auth/login                -> {"username", "password"} -> identity
POST /api/auth/logout               -> 204, session destroyed
GET  /api/auth/me                   -> identity
GET  /api/lanes                     -> {"lanes": [...]} at the account's station
GET  /api/lanes/<device_id>         -> lane, open movement, 10 recent mode changes
POST /api/lanes/<device_id>/mode    -> {"direction": "ENTRY"|"EXIT", "note": "..."}
```

`identity` is `{user: {username, first_name, last_name}, station: {code, name}, role, can_write}`.

| Role | Read | Change lane mode |
|---|---|---|
| `VIEWER` | yes | no (`403 ROLE_NOT_PERMITTED`) |
| `YARD_MASTER` | yes | yes |
| `ADMIN` | yes | yes, at the same station only |

Errors are stable codes: `401 NOT_AUTHENTICATED`, `401 INVALID_CREDENTIALS`, `403 CSRF_FAILED`, `403 NO_STAFF_PROFILE`, `403 STAFF_INACTIVE`, `403 STATION_INACTIVE`, `403 ROLE_NOT_PERMITTED`, `404 NOT_FOUND`, `409 LANE_INACTIVE`, `429 THROTTLED`.

### Decisions visible in the implementation

- **Session, not token.** The dashboard is same-origin and Django-served, so a session cookie plus CSRF is enough. Tokens in browser storage would add a second credential to protect and could not be revoked as simply as deactivating the account.
- **Login refusal happens before the session is created.** An account with no profile, an inactive profile, or an inactive station receives 403 and no cookie. A session that becomes invalid later is refused on every request, because `staff_profile_for()` runs on each one.
- **Foreign objects are 404.** A different station's lane returns exactly the same body as a nonexistent one, so the API does not confirm that another station's lane exists.
- **Mode change while a movement is open is accepted.** This follows v4 §3.4 and its acceptance table: the open movement keeps its direction and its authorising `lane_mode_change`; the new mode applies to the next movement. The response states `applies_to: "NEXT_MOVEMENT"` and includes the open movement, because "the lane changed" and "the consist changed" are different facts. My first implementation returned `409 LANE_BUSY` here; that was stricter than the specification and was removed.
- **Repeating the current mode writes no audit row.** Nothing changed, so recording a change would be false history.
- **Ingestion and the dashboard cannot borrow each other's credentials.** Ingestion sets `authentication_classes = ()` and `permission_classes = (AllowAny,)`; the dashboard's default is `SessionAuthentication` + `IsAuthenticated`. Tests cover both directions.

### Verification

```text
$ python manage.py test yard backend
Ran 122 tests in 4.746s
OK

$ python manage.py check                    -> no issues
$ python manage.py makemigrations --check   -> No changes detected
```

End-to-end through the real URLs against the seeded database, inside a rolled-back transaction:

```text
login 200 {'user': {'username': 'smoke-master', ...}, 'station': {'code': 'NDLS', ...}, 'role': 'YARD_MASTER', 'can_write': True}
me 200 NDLS
lanes 200 ['gate-1']
mode 200 {'device_id': 'gate-1', 'lane_mode': 'EXIT', 'changed': True,
          'applies_to': 'NEXT_MOVEMENT', 'open_movement': None,
          'change': {'id': 2, 'direction': 'EXIT', 'set_by': 'smoke-master', ...}}
detail EXIT smoke-master | open: None
logout 204 | after logout: 401
after rollback: user 0 | lane mode ENTRY
```

The 38 new tests cover: public health; session/device credential separation in both directions; 401 for a missing session; login success and session creation; wrong password; deactivated Django user; CSRF on login, logout, and lane-mode POST; missing profile with no session created; inactive profile; inactive station; login throttling and spoofed `X-Forwarded-For`; logout; `/me`; the scoping helper; station-filtered lane list; foreign lane 404 indistinguishable from missing; foreign lane write; no API-key leak; viewer read and write refusal; master mode change with `set_by`; same-mode no-op; mode change with a live movement applying to the next bucket; idle-bucket closure; disabled lane; invalid direction; and six `create_staff` cases.

### What CP-07a deliberately does not include

Inventory transitions, movement/finding/alert read APIs, and the dashboard templates. Those are CP-07b and later. The lane read API exists now because it is the smallest real surface on which station scoping, roles, and `set_by` could be proven end to end.

---

## 6f. Checkpoint CP-07b — inventory transitions and read APIs

| File | Change | Purpose |
|---|---|---|
| `backend/yard/models.py` | edit | `MovementInventoryEffect`: frozen pre-movement snapshot and one-time applied marker per distinct UID |
| `backend/yard/migrations/0003_movementinventoryeffect.py` | new | Schema for the effect ledger |
| `backend/yard/validation.py` | edit | Freeze/apply/reconcile inventory; identity resolved from the current registry; `composition_for()` shared with reads |
| `backend/yard/read_views.py` | new | Station-scoped overview, movement, finding, alert, inventory reads; alert acknowledgement; lazy closure |
| `backend/yard/serializers.py` | edit | Read serializers, filters, and pagination; movement detail exposes observed counts and the effect ledger |
| `backend/yard/urls.py` | edit | Read routes |
| `backend/yard/admin.py` | edit | Read-only inventory-effect inline on a movement |
| `backend/yard/test_inventory.py` | new | 15 transition, revalidation, and reconciliation tests |
| `backend/yard/test_read_api.py` | new | 17 scoping, filter, pagination, lazy-closure, and acknowledgement tests |

### Inventory application

At first validation the backend freezes `status_before` for every distinct UID actually read, then applies one transition per UID:

| Direction | Target status |
|---|---|
| `ENTRY` | `IN_YARD` |
| `EXIT` | `OUT` |

- A **known** UID in a `VALID` movement is `VERIFIED`; known UIDs in `FLAGGED`/`UNVERIFIED` movements and unknown UIDs are `UNVERIFIED`.
- An ultrasonic warning alone never downgrades the row.
- Unknown UIDs are retained as unidentified inventory.
- A planned locomotive with no RFID read receives no transition.
- Presence changes only when the target differs from the frozen state. `since` and `last_movement` then name the movement that established the current status. An entry of stock already inside therefore does not rewrite them.
- A revalidation recomputes findings from the frozen snapshot, never replays a transition, and only refreshes rows whose `last_movement` is still this movement.

### Read contract

```text
GET  /api/overview                     -> lanes, inventory totals, open movements, open alerts
GET  /api/movements                    -> state, direction, result, device_id, limit, offset
GET  /api/movements/<id>               -> movement + findings + alerts + events + effects + observed counts
GET  /api/findings                     -> severity, code, movement, limit, offset
GET  /api/alerts                       -> severity, code, acknowledged, limit, offset
POST /api/alerts/<id>/acknowledge      -> 200; idempotent; records acknowledged_by
GET  /api/inventory                    -> status, verification, q, limit, offset
```

All routes require a session and are filtered by the account's station. Pagination is `limit` (1–200, default 50) and `offset`; responses return `count`, `limit`, `offset`, and the named collection.

### Decisions visible in the implementation

- **An explicit effect ledger, not a JSON snapshot.** `MovementInventoryEffect` is queryable, was inspected in the admin, and makes "applied once" a database fact rather than a convention. A JSON blob on the movement would hide the transition history the report needs.
- **Anomalies read the snapshot, never the live row.** `ENTRY_ALREADY_PRESENT`, `DIRECTION_CONFLICT`, and `UNEXPECTED_EXIT` are evaluated against `status_before`. Reading the live row would make an entry look "already present" on its own revalidation and could judge an old movement against a later exit.
- **No-change observations do not claim ownership.** `last_movement` names the movement that produced the current status, not the last movement that merely saw the unit. That is what lets a revalidation skip rows a later movement owns.
- **Identity is resolved from the current registry.** The observed UID is evidence; the rolling-stock identity is looked up at validation time with the event's snapshot as fallback. Registering a previously unknown UID and revalidating corrects the classification instead of freezing an old mistake.
- **Reads settle idle buckets.** A GET may close an idle movement. The scheduled sweep is the normal path, but a demo without a scheduler would otherwise show a movement that should already have closed. The trade-off is a write inside some GETs.
- **Acknowledging an alert is idempotent and never changes the finding.** It records who saw the notification; the movement's evidence is unchanged.

### Verification

```text
$ python manage.py test yard backend
Ran 154 tests in 10.2s
OK

$ python manage.py check                    -> no issues
$ python manage.py makemigrations --check   -> No changes detected
$ python manage.py migrate                  -> yard.0003_movementinventoryeffect applied
```

End-to-end through the real ingestion endpoint against a temporary station, inside a rolled-back transaction:

```text
ingest 200 ['accepted', 'accepted', 'accepted', 'accepted']
closed 1 | movement 1 CLOSED VALID | plan SMOKE-IN
effect snapshot [('AAAA1111', None, 'IN_YARD'), ('BBBB2222', None, 'IN_YARD')]
login 200
overview 200 {"lanes": {"total": 1, "by_mode": {"ENTRY": 1, ...}},
              "inventory": {"in_yard": 2, "out": 0, "unverified": 0},
              "alerts": {"unacknowledged": 0}, "open_movements": [],
              "latest_movement": {"validation_result": "VALID", "plan_code": "SMOKE-IN", ...}}
inventory 200 [{'rfid_uid': 'AAAA1111', 'status': 'IN_YARD', 'verification': 'VERIFIED', ...},
               {'rfid_uid': 'BBBB2222', 'status': 'IN_YARD', 'verification': 'VERIFIED', ...}]
detail 200 observed {'locos': 1, 'coaches': 1, 'wagons': 0, 'distinct_uids': 2, ...}
           agree True findings [] alerts [] effects 2
rollback -> stations 0
```

The transition tests cover: one row per distinct UID despite repeats; entry and exit transitions; an entry of stock already present preserving `since` and owner; an entry after a previous exit changing both; unexpected exits creating unverified `OUT` rows; unknown UIDs retained; a flagged movement still updating observed units; an unread planned locomotive receiving nothing; ultrasonic-only disagreement not downgrading; revalidation not repeating a transition; revalidation not overwriting a later exit; and a late registration reconciling the row the movement still owns.

The read tests cover: 401 on every route without a session; station-filtered lists; foreign movement and alert 404; filters and pagination; invalid filter 400; detail composition, findings, alerts, events, and effects; lazy closure of an idle movement without touching another station; acknowledgement identity and idempotency; viewer read-only refusal; inventory filters; and the overview summary.

### What CP-07b deliberately does not include

Dashboard templates, the simulator, and any write endpoint other than alert acknowledgement and lane mode. Those were CP-08 and later at the time of this checkpoint; the simulator was subsequently dropped by user decision (D-52), and the dashboard shipped as CP-08. Movements validated before this checkpoint did not apply inventory; on an existing database, only a deliberate `revalidate_movements --all` (in chronological order) would reconstruct it. `seed_demo --reset` avoids the question for a demo.

---

## 6g. Checkpoint CP-08 — dashboard and real-sensor demo scope

**Status:** applied and verified by the assistant on 2026-10-26.

The user removed the simulator from scope: the demonstration will use the physical ESP32/RC522/ultrasonic bench, and any missing fleet or plan data is seeded. CP-08 therefore delivered the operator dashboard, and CP-09 is the firmware.

### Pages and access

```text
GET  /                  -> overview (cards, open movements, latest movement)
GET  /lanes/            -> lane list, mode control, open-movement link
GET  /movements/        -> filterable history (state, direction, result, lane)
GET  /movements/<id>/   -> findings, alerts, events, inventory effects, counts
GET  /inventory/        -> status, verification, UID search, last movement
GET  /alerts/           -> severity/open filters, acknowledgement
GET  /login/            -> sign-in form
POST /logout/           -> CSRF-protected session end
```

The page guard runs before any data is fetched:

- anonymous → `302` to `/login/?next=<full path>`;
- authenticated but no profile / inactive profile / inactive station → HTML `403` carrying `NO_STAFF_PROFILE`, `STAFF_INACTIVE`, or `STATION_INACTIVE`;
- another station's movement → `404` from `get_station_object_or_404`, identical to a missing id.

Once the shell loads, the browser calls the existing JSON API with the session cookie. Unsafe calls send `X-CSRFToken`. A `401` during polling redirects to login with the current path, so an expired shift does not leave a silently stale screen.

### Files

| File | Change | Purpose |
|---|---|---|
| `backend/yard/dashboard_views.py` | new | Page guard, login/logout pages, seven dashboard pages |
| `backend/yard/dashboard_urls.py` | new | HTML routes at the site root |
| `backend/backend/urls.py` | modified | Mount the dashboard after `/api/`, `/admin/`, and `/health` |
| `backend/yard/templates/yard/base.html` | new | Authenticated shell, navigation, CSRF meta, identity, sign-out |
| `backend/yard/templates/yard/base_public.html` | new | Login/denied shell |
| `backend/yard/templates/yard/login.html` | new | Sign-in form |
| `backend/yard/templates/yard/denied.html` | new | Explanatory 403 with sign-out |
| `backend/yard/templates/yard/overview.html` | new | Station totals, open movements, latest movement |
| `backend/yard/templates/yard/lanes.html` | new | Lane table and mode control |
| `backend/yard/templates/yard/movements.html` | new | Filter form and paginated history |
| `backend/yard/templates/yard/movement_detail.html` | new | Counts, findings, alerts, events, effects |
| `backend/yard/templates/yard/inventory.html` | new | Inventory filters and table |
| `backend/yard/templates/yard/alerts.html` | new | Alert filters, acknowledgement |
| `backend/yard/static/yard/dashboard.css` | new | Small dependency-free layout and badge styles |
| `backend/yard/static/yard/dashboard.js` | new | API loaders, CSRF, IST formatting, polling, ack/mode actions |
| `backend/yard/serializers.py` | modified | Expose `direction_source` on movement summaries (read-only) |
| `backend/yard/test_dashboard.py` | new | 15 page/access/asset tests |

### Verification

```text
$ python manage.py test yard backend
Ran 169 tests in 11.0s
OK

$ python manage.py check
System check identified no issues (0 silenced).

$ python manage.py makemigrations --check --dry-run
No changes detected
```

Browser smoke against the dev database (assistant-run, then cleaned up):

- Login succeeded through `/api/auth/login`; the overview loaded live totals and `Updated … IST` with no console errors.
- `ENTRY → EXIT → ENTRY` on `gate-1` produced the expected toast and `applies_to` message; the temporary audit rows were removed afterwards.
- One real `RFID_DETECTED` event on a temporary `ui-lane`, bound to a temporary `UI-SMOKE` plan, closed after the 20-second gap. The detail page showed `CLOSED`/`FLAGGED`, 1 loco against 1 expected, `COUNT_MISMATCH` (FAIL) and `ULTRASONIC_COUNT_MISMATCH` (WARN), one event, and one `absent → IN_YARD` effect.
- The alert acknowledgement endpoint recorded `ui-smoke` and the detail re-rendered without a reload.
- Inventory showed the UID as `IN_YARD` / `UNVERIFIED` with a link to movement 77; movement filters and alert filters reflected their URL values.
- Deleting the smoke account while a tab polled produced `401` on the next read and redirected to `/login/?next=…`; this was observed in the server log, not inferred.
- Cleanup restored `NDLS` to one lane (`gate-1`, `ENTRY`), 3 plans, 10 registered stock, 0 movements/events/findings/alerts/effects/inventory, and the original seed `LaneModeChange`. No `SIM` station or rows remain.

### What CP-08 deliberately does not include

- ESP32 firmware (CP-09), a scheduler, or a websocket/SSE transport.
- Plan reassignment from the dashboard; the Django admin remains the manual workaround.
- A no-JavaScript fallback. The pages are shells by design (D-50); with JavaScript disabled they show their loading placeholders.
- A favicon. The server logs a harmless `404 /favicon.ico` in the browser.

---

## 6h. Checkpoint CP-09 — ESP32 lane firmware

**Status:** applied; logic and HTTP transport verified by the assistant on 2026-10-26. A hardware run on the bench is the remaining step.

The firmware is deliberately dumb. It senses, timestamps if it can, and reports. Direction, bucket boundaries, plan matching, distinct-UID counts, validation, inventory, and alerts all stay on the server, so a device with a stale or wrong idea cannot corrupt the yard record.

### Files

| File | Purpose |
|---|---|
| `firmware/main.py` | Wi-Fi association, NTP clock, HTTP client, queue drain, sensor loop |
| `firmware/logic.py` | Portable rules: hysteresis detector, per-UID gate, tick arithmetic, event ids, backoff, queue |
| `firmware/mfrc522.py` | Minimal RC522 driver (`request`, `anticoll`, `read_uid`) |
| `firmware/config.example.py` | Committed template: pins, thresholds, API and Wi-Fi placeholders |
| `firmware/tests/test_logic.py` | 29 CPython tests over `logic.py` |
| `firmware/README.md` | Wiring, provisioning, upload, tuning, troubleshooting, limits |

`firmware/config.py` is gitignored and holds the Wi-Fi password and device key.

### Two independent event streams

| Event | Emitted | Payload |
|---|---|---|
| `RFID_DETECTED` | A UID is read and its per-UID cooldown has elapsed | `rfid_uid`, optional `timestamp` |
| `PHYSICAL_PASSAGE` | The zone becomes occupied and then clears | `timestamp` = zone entry, `metadata.started_at/ended_at/duration_ms/min_distance_cm/peak_distance_cm` |

The two are never paired at the device. v4 requires a correct miss to look like a miss: if the firmware invented a card read to fill a passage, `MISSING_LOCOMOTIVE_RFID` could never fire. Passage counts stay advisory, so their only job is `ULTRASONIC_COUNT_MISMATCH` as a warning.

### Sensor rules

- Ultrasonic hysteresis: occupied below 140 cm, clear above 150 cm, 3 occupied samples, 4 clear samples, 120 ms minimum passage, 30 s maximum open passage.
- A **missing echo never clears an occupied zone.** A dead or blocked sensor must not split one vehicle into two passages; it can only fail to report one at all.
- The passage timestamp is zone entry, per v4, even though the event is written on exit.
- UID state is per UID, not "last UID": alternating two tags refreshes both timers instead of making the first look new. Cooldown 3 s, refreshed on every successful raw read, bounded to 32 tracked UIDs.
- Entry time is the first sub-threshold sample, not the confirming one, so the passage timestamp does not drift by the confirmation window.

### Transport and durability

- One JSON file per event in `/queue`, written to `NNNNNNNN.json.tmp` then renamed, so a power cut cannot leave a half-record behind. `event_id` is assigned at enqueue and stored in the file, so a reboot with a new boot id does not rename queued events.
- Bound of 2000 events, dropping the oldest and counting the loss: the newest events describe the train at the gate, which is what the tester needs.
- Batches of 25. `accepted`, `duplicate`, and `rejected` all delete the file — `duplicate` is the normal outcome of retrying a batch whose 200 the device never saw.
- Kept and retried: `409` after 5 s (lane mode unset), `401/403` after 60 s, other `4xx` and `429` after 30 s, `5xx`/transport with exponential backoff to 30 s.
- Rejections are logged with the server's code and message; they are not retried, because a malformed event will never become well-formed.

### Clock policy

Until NTP answers, events carry **no** `timestamp` field. The server buckets by `received_at`, so an unsynced clock costs nothing and a wrong clock is impossible. This was a deliberate choice: a device clock set from `time.time()` after a cold boot returns 2000-01-01, which would look like real evidence in the audit trail.

The clock's `iso(offset)` helper allows the passage event to report its entry time even though it is written later.

### Verification

```text
$ cd firmware && ../env/bin/python -m unittest discover -s tests -v
Ran 29 tests in 0.013s
OK

$ ../env/bin/python -m py_compile logic.py mfrc522.py main.py config.example.py
compile ok
```

Live HTTP integration, run by the assistant against the real Django server with `machine`, `network`, `mfrc522`, and `config` stubbed, using the firmware's own transport code:

- unsynced clock omits `timestamp`; synced clock produces `...Z` and a passage whose `timestamp` equals `metadata.started_at`;
- real `POST /api/ingest/events` → `200`, `accepted: 2`, events deleted from the queue;
- an event missing `rfid_uid` → `rejected` with `MISSING_RFID_UID`, file deleted;
- wrong key → `401 DEVICE_AUTH_FAILED`, file **retained**;
- `409` schedules 5 s and `500` schedules its backoff step.

That harness found a real defect before any hardware was involved: `_read_response` called `recv` but never appended the chunk, so every response was discarded and the device reported `connection closed before response headers` while the server log showed `200`. One missing `data += chunk`. It is exactly the class of bug that would have looked like a network problem on the bench.

### Decisions visible in the implementation

- **Independent events (D-57).** The firmware emits raw evidence only; any grouping decision belongs to the server.
- **File-per-event queue (D-58).** No database, no single index file to corrupt: rename is the only atomicity primitive MicroPython offers.
- **No timestamps before NTP (D-59).**
- **Per-status retry schedule (D-60).** A 409 is an operator action away; a 401 is a credential problem that will not fix itself in five seconds.
- **Ultrasonic hysteresis with missing-echo hold (D-61).**
- **Echo wired directly to GPIO 25 (D-62).** Out of spec, retained at the user's direction after roughly two years of service; a 1 kΩ/2 kΩ divider is documented as the fix.

### What CP-09 deliberately does not include

- 7-byte UID support (cascade level 2). Only 4-byte tags are read, which matches the bench.
- RC522 anticollision for more than one tag in the field at once.
- Config editing from the dashboard or a provisioning hotspot; the key is pasted into `config.py`.
- TLS certificate validation, local clock persistence, and over-the-air updates.
- Any interpretation of the data. The firmware never counts, matches, or judges.

---

## 7. Verification status (current)

```bash
cd backend
python manage.py makemigrations --check --dry-run
python manage.py test yard backend
python manage.py check
```

Result: migrations `0001_initial`, `0002_lanemodechange_and_more`, and `0003_movementinventoryeffect` are the whole schema; `yard` (166 tests) plus the 3 pre-existing health tests all pass (169 total); `check` reports no issues and no migrations are pending.

Firmware logic is verified separately, without hardware:

```bash
cd firmware
../env/bin/python -m unittest discover -s tests -v
```

Result: 29 tests pass. The real HTTP path was exercised against the running Django server with the hardware modules stubbed; see CP-09.

Then create a dashboard account and open the dashboard:

```bash
python manage.py migrate
python manage.py seed_demo --reset
python manage.py create_staff --username yard1 --station NDLS --role YARD_MASTER
python manage.py runserver
# open http://127.0.0.1:8000/ and sign in as yard1
```

For a phone or another laptop on the LAN, run `python manage.py runserver 0.0.0.0:8000` and set `DJANGO_ALLOWED_HOSTS` to the server's LAN address (comma-separated if needed). The JSON surface remains at `/api/`; the dashboard consumes it.

A superuser for the Django admin site is separate and still created with `createsuperuser`. The admin site is not station-scoped; see D-43.

---

## 8. Known gaps and open decisions

| Item | State |
|---|---|
| Plan reassignment after a wrong bind | Open. A plan may bind to only one movement; the yard master currently has no UI/endpoint to release it. `Movement.plan` stays editable in admin as the manual workaround. |
| Firmware `event_id` format | **Closed (D-24):** `<device_id>-<boot_id>-<sequence>`. The firmware must persist `boot_id` alongside the queue so a retried event keeps its id. |
| API-key rotation | Hashing and cache invalidation are in place; `seed_demo --rotate-key` and the admin form both work. A dedicated dashboard action is not built. |
| `ALLOWED_HOSTS` for ESP32 on LAN | Configured via `DJANGO_ALLOWED_HOSTS`; the concrete LAN IP is not yet fixed. |
| Closure sweep runner | `python manage.py sweep_movements` exists and is idempotent. A scheduler (cron, systemd timer, or a thread in the demo server) still has to run it. Movement/inventory/overview reads now close idle buckets themselves (D-49), so the dashboard is correct without one. |
| SQLite concurrency | Prototype-acceptable. Enable WAL if ingestion and the dashboard run together under load. |
| Device key verification cost | Mitigated: one PBKDF2 check per key per 60 s, then cached (D-23). |
| Auth cache across processes | The default locmem cache is per process. With multiple workers, each pays one verification per key per minute; that is acceptable here, and a shared cache fixes it if workers multiply. |
| Scaffold/version drift | Settings header says Django 4.2.16; the environment actually runs 6.1.2. Code now targets the installed version. Re-check on any environment rebuild. |
| UID length variety | Canonical form accepts 4–32 hex characters (2–16 bytes) in whole bytes; RC522 4-byte tags are the current case. |
| Closed movements awaiting validation | **Closed:** `python manage.py revalidate_movements` validates every closed movement with `validated_at IS NULL` and replaces the `VALIDATION_PENDING` placeholder. Verified against a synthetic legacy movement. |
| Revalidation of a closed movement | **Implemented:** findings are upserted and pruned, alerts are created once, plan binding is stable, and inventory is applied once through the frozen effect ledger (D-45/D-46). |
| Inventory-inferred direction | **Rejected by decision D-19.** Never implemented. Only a `DIRECTION_CONFLICT` warning will consult inventory state. |
| Lane mode unset | **Implemented:** HTTP 409 `LANE_MODE_NOT_SET`, nothing stored. The firmware queue must retain and retry. |
| Mode change during a backlog drain | A late batch can be ingested after the operator changed the lane mode, giving it the new direction. Same class of problem as delayed replay; mitigate by closing the movement before switching modes. |
| Operator ergonomics | **Closed for the demo:** lane mode and alert acknowledgement work from the dashboard with role checks and `set_by`; plan reassignment remains admin-only (D-55). |
| Lazy closure on read | **Implemented:** movement detail/list, inventory, and overview settle idle buckets before answering (D-49). |
| Validation outcome and alerts are stored, nothing reads them yet | **Closed:** the station-scoped read APIs and the dashboard read stored findings, alerts, counts, events, and effects. |
| Inventory transitions | **Implemented:** one transition per distinct read UID, frozen pre-movement state, no replay on revalidation (D-45…D-48). |
| Inventory for movements validated before CP-07b | Those movements never applied inventory, because the effect ledger did not exist. A deliberate chronological `revalidate_movements --all` would reconstruct it; `seed_demo --reset` is simpler for a demo. |
| Dashboard pages | **Closed:** seven station-scoped pages at the site root, with a server-side page guard and live API loading (D-50/D-51). |
| Dashboard requires JavaScript | Accepted prototype limitation. Pages are shells so the read contract has one implementation; with JavaScript disabled they remain at their loading state. A server-rendered fallback would duplicate every query and scoping rule. |
| Dashboard polling load | Two requests every 5 s on overview/lanes/movements (one on detail), and one every 10 s on inventory/alerts, per open tab. Fine for a single-yard demo; a tablet left open all day should be closed or the interval raised. |
| Simulator | **Dropped by user decision (D-52).** The demo uses physical sensors plus seeded master data. CP-08 is the dashboard; no simulator command remains in the repository. |
| Firmware queue durability | **Implemented:** one file per event, atomic rename, corrupt and `.tmp` records discarded at load, `event_id` persisted with the event (D-58). Verified by tests across a simulated restart and by a live queue drain and 401 retention. |
| Firmware clock | **Settled (D-59):** no `timestamp` until NTP answers. The server's `received_at` is the ordering fact, so an unsynced device is harmless. What is still unknown is how reliably this bench's Wi-Fi reaches an NTP server; the first hardware run will show it in the start log. |
| Firmware 7-byte UIDs | Open, low risk. `mfrc522.py` implements cascade level 1 only, so NTAG-style 7-byte tags read as no tag. Bench tags are 4-byte. The server would accept a longer UID unchanged. |
| Firmware: two tags in the field at once | Open. No anticollision loop, so two simultaneous tags may read as one or as neither. The demo presents tags one at a time. |
| HC-SR04 Echo level | **Accepted risk (D-62).** Echo drives GPIO 25 at ~5 V against a 3.3 V pin. It has worked on this bench for about two years; a 1 kΩ/2 kΩ divider is the documented fix and needs no firmware change. |
| Firmware HTTPS | Untested. `https://` is supported only if the flashed build has `ssl`, and no certificate validation is performed. The demo runs plain HTTP on the LAN. |
| Firmware run on real hardware | **Pending.** Logic rules and the HTTP contract are verified, and a real defect in response reading was found and fixed before flashing. Nothing in CP-09 has yet been run on the ESP32. |
| Plan window vs rehearsal timing | Plans are matched within 60 minutes of `expected_start`, which `seed_demo` writes at seed time. Run `python manage.py seed_demo --reset` before a demo rehearsal. |
| Plan without a lead locomotive | Open by data quality, not by code: `expected_lead_loco` is nullable, so a plan that names no locomotive identity and has no locomotive assignment can only be count-checked. The seed always sets a lead. |
| Multi-locomotive demo data | No demo plan expects two locomotives because the bench has one locomotive tag. The behaviour is covered by tests only; it is not part of the physical demo. The former simulator reference is withdrawn with D-52. |
| Banker/helper locomotive attached en route | **Deferred by the user (2026-10-25) — not a demo concern.** Current behaviour: a complete plan roster plus an unplanned banker yields `LOCOMOTIVE_MISMATCH` + `COUNT_MISMATCH`, i.e. `FLAGGED`. The open question is whether an extra locomotive should stay `FAIL` or become a `WARN`; flipping it is a one-line severity change plus one test. Revisit after the demo. |

---

## 9. Next checkpoints

1. **CP-07a — authentication:** **done and verified.** Django-session login/logout/me for yard masters, global `IsAuthenticated` default, station scoping from `StaffProfile`, role checks, and `set_by` on lane-mode changes. Decisions taken with the user: Django-served dashboard (session cookie, not a token), and every account is station-bound with no administrator exception.
2. **CP-07b — inventory & read APIs:** **done and verified.** One RFID-primary transition per distinct UID actually read, a frozen pre-movement snapshot, and station-scoped movement/finding/alert/inventory read APIs, including alert acknowledgement.
3. **CP-08 — dashboard:** **done and verified.** Seven Django-served pages over the read APIs, with server-side session/station guards, lane-mode control, alert acknowledgement, IST display, and live refresh. The simulator was dropped by user decision; the demo uses real sensors plus seeded master data (D-52).
4. **CP-09 — firmware:** **applied, hardware run pending.** ESP32 `main.py` plus `logic.py`, `mfrc522.py`, a committed config template, 29 logic tests, and a README with wiring, provisioning, tuning, and troubleshooting. Verified against the live API; the remaining step is the bench run.
5. **CP-10 — integrated rehearsal:** run `seed_demo --reset`, create the demo account, start the server, and exercise the physical lane through entry and exit while the dashboard is open. Confirm the 20-second gap, plan binding, findings, inventory, and alert acknowledgement with the real hardware.
