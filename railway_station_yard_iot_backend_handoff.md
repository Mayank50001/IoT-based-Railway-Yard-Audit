# Railway Yard IoT — Hardware Progress and Backend Session Handoff

**Updated:** 2026-10-26  
**Next task:** CP-09 — the final ESP32 `main.py` against the stable ingestion contract. CP-08 (the Django-served dashboard) is **VERIFIED**, and the simulator has been **dropped by user decision**: the demo uses real sensors plus seeded master data. CP-07a (session authentication, station scoping, roles, lane mode) and CP-07b (frozen inventory transitions, station-scoped read APIs) are also **VERIFIED**. Progress is tracked in the [Backend Implementation Log](railway_station_yard_iot_backend_implementation_log.md).

> Read this handoff first, then [RFID-Primary Specification — v4](railway_station_yard_iot_project_context_v4.md). Use v3 only for compatible background details, not its superseded mandatory-pairing/paired-inventory rules. Progress, decisions, and verification steps are recorded in the [Backend Implementation Log](railway_station_yard_iot_backend_implementation_log.md) — check it before starting work. This handoff records reported hardware results and the latest implementation direction; it is not a claim that the reported hardware results were independently measured by the assistant.

## 1. Current outcome and user preferences

- The user has previous Python/Django experience and has made multiple Django projects.
- The user already has an ESP32 running MicroPython, Thonny, an ultrasonic sensor, and an RC522 reader/writer with tags/keyfobs.
- The user performed hardware tests with Gemini's help and reported successful basic bring-up and concurrent sensing.
- The user subsequently confirmed that the requested acceptance tests pass with the RFID-first, physically separated sensor arrangement described below. Individual test logs and numerical run results have not been supplied to this repository.
- The backend event contract and dashboard are ready. The next code deliverable is the complete MicroPython **`main.py`** for the ESP32, including any necessary driver dependency rather than unexplained imports.
- The user approved moving forward with **distinct RFID UID counts as primary validation**, ultrasonic counts as supporting evidence, and coach-based lookup of expected locomotive/plan information when the locomotive RFID is absent.
- Follow `AGENTS.md`: provide code in chat unless code-file edits are explicitly authorized, and ask before accessing files outside the project. Code-file edits were authorized for the backend work; the implementation log records what was delivered and verified.

## 2. Hardware and environment reported by the user

| Item | Reported finding |
|---|---|
| Board | ESP32 DevKit WROOM, 30-pin |
| Firmware | MicroPython v1.29.0 |
| CPU frequency | 160 MHz |
| Development tool | Thonny; stable REPL connection |
| Restart behaviour | Clean soft-reboot reliability |
| ESP32 filesystem | Existing `boot.py` and `main.py`; their contents have not been provided |
| RFID interface | VSPI successfully initialized |
| RC522 version register | `0x92` |
| RFID UID length observed | Complete 4-byte UIDs |
| Example UID 1 | Reported `FA37E63F`; canonical representation `FA:37:E6:3F` |
| Example UID 2 | Reported `0E78E13F`; canonical representation `0E:78:E1:3F` |
| Ultrasonic empty baseline | Approximately 156 cm against a background reflector |
| Ultrasonic occupied readings | Approximately 1.5–5 cm |
| Current blockage threshold | 140 cm |

Keep UIDs as strings, preserve leading zeros, and use one canonical representation consistently in events and database records. The example UIDs have **not** yet been assigned business identifiers such as `LOCO-1847` or `COACH-001`.

### How many RFID tags a demo needs

Counting is **per distinct UID**, so a consist demonstration needs one physical tag per distinct unit. Two tags can only demonstrate two identified units, no matter how many times they pass.

| Demo level | Tags | Composition |
|---|---|---|
| Minimum meaningful | 3 | 1 loco + 1 coach + 1 unregistered tag for `UNKNOWN_RFID` |
| Recommended for this project | 6 | 1 loco + 4 coaches + 1 unknown tag |
| Full v4 scenario | 10 | 1 loco + 8 coaches + 1 unknown tag |
| Extended (2 locos, wagons) | 12–14 | Adds a second locomotive and two wagons |

Practical notes:

- Cheap 13.56 MHz Mifare Classic 1K tags/stickers work with the RC522 and are the easiest to source in small quantities. NTAG213/215/216 stickers also work but report **7-byte UIDs**, which appear as 14 hex characters; the backend accepts 4–32 hex characters, so both are supported.
- UIDs are factory-set. Rewriting a tag's UID requires special "magic" cards and is not reliable; buy distinct tags instead.
- The current bench has two tags, so `seed_demo` includes **`NDLS-IN-BENCH`** (1 loco + 1 coach) mapped exactly to `FA37E63F` and `0E78E13F`. The 4-coach plans remain seeded for when more tags are available; they are **not** simulated.

### Demo rehearsal step

Run `python manage.py seed_demo --reset` immediately before a demonstration. Plans are matched within 60 minutes of `expected_start`, which the seed writes at seed time, so a rehearsal an hour after seeding correctly produces `UNPLANNED_MOVEMENT`. `--reset` also returns the plans to `SCHEDULED`: a plan that has already carried a movement is `COMPLETED` and cannot bind again.

The exact GPIO mapping, ultrasonic model, tag model, Echo level-shifting circuit, and current test-script source remain undocumented. Do not infer that every wiring detail or production-firmware feature has been verified just because the reader version register and basic sensing work.

## 3. Completed test report

| Test | User-reported status | Findings / interpretation |
|---|---|---|
| 1. Environment and firmware | PASSED | Stable MicroPython/Thonny connection and soft reboots. |
| 2. RC522 SPI communication | PASSED | VSPI communication works; version register returns `0x92`; reader reported powered at 3.3 V. |
| 3. UID reading and stability | PASSED | Complete 4-byte UIDs read consistently, with zero false UID triggers when idle. |
| 4. RFID memory access | Read/authentication VERIFIED; writing unsuccessful | Block 4 read successfully; authentication reported successful with Key A and Key B `FF FF FF FF FF FF`. User/Gemini concluded the commercial tag/keyfob was factory-locked read-only; see the qualification below. |
| 5. RFID range and mounting | COMPLETED | RC522 short-range limitations acknowledged. A close presentation position is used; exact measured reliable mounting distance is not recorded. |
| 6. Ultrasonic calibration | PASSED | Stable approximately 156 cm empty baseline; objects produce approximately 1.5–5 cm readings; current threshold 140 cm. |
| 7. Physical-passage state machine | PASSED | IDLE/OCCUPIED state machine implemented; entry timestamps retained and passage duration calculated on exit. |
| 8. Combined streams and correlation | PASSED | RFID and ultrasonic sensing run concurrently in one script; streams are independent; a 2-second correlation window was tested. |

### RFID write qualification

Authentication/read success plus a failed write does **not alone prove permanent factory locking**. A permissions conclusion requires correctly decoding the relevant access bits or equivalent reliable evidence; protocol/driver errors and interrupted communication are other possible causes.

For a confirmed MIFARE Classic 1K tag, block 4 belongs to sector 1, whose trailer is block 7. Reading the trailer to inspect access bits is permissible; **do not write block 0, trailers, keys, or access bits** during these tests. The actual decoded access-bit evidence has not been shared.

For project planning, the established requirement is simply **stable UID reading**. Tag data writing is optional and is not a blocker. Do not buy replacement writable tags or implement UID-changing operations just to progress the backend.

### Ultrasonic qualification

The current baseline and threshold are **reported bench settings**, not universal recommended settings. A 140 cm threshold against a 156 cm reflector can detect unrelated objects in the foreground unless the physical detection zone is controlled.

If the sensor is a standard HC-SR04, approximately 1.5 cm is below its usual specified minimum range of approximately 2 cm. Keep the actual card position comfortably inside the sensor's usable range. Confirm the sensor model before assuming its electrical or range specifications.

The future firmware must explicitly implement hysteresis, consecutive-reading confirmation, minimum occupied/clear durations, and missing-echo handling. A missing echo must not automatically be interpreted as a clear gate. These implementation details cannot be audited without the current script.

## 4. Acceptance tests and evidence status

The user reports that acceptance tests pass with the separated-sensor workflow. Preserve that progress; do not require restarting basic tests merely to begin the backend. The acceptance targets discussed were:

- Empty gate for 60 seconds: zero physical passages.
- 20 separately passed tagged units with clear gaps: 20 passages and 20 emitted RFID observations.
- Two touching cards with no detectable clear gap: one passage, an expected limitation.
- An untagged card: physical evidence only.
- A tag near the reader but outside the ultrasonic zone: RFID evidence only.
- A stationary tag: repeated raw reads are normal; implemented cooldown should prevent repeated emitted observations while successful reads continue.
- The same tag removed for more than 3 seconds and presented again: a new RFID observation.
- Reliable reading at the actual demonstration position, without needing physical contact with the antenna.

No raw logs, timing distributions, or per-test counts were attached. Treat the report as user-confirmed acceptance, not independently measured data. Final integrated firmware should rerun these checks after its sampling, cooldown, and transport code are introduced.

## 5. Accepted physical workflow: RFID first, then passage

### User constraint

The user **cannot physically mount the RFID reader and ultrasonic sensor together**. The intended tabletop operation is therefore:

1. Present the tag associated with one cardboard unit to the reader.
2. Remove it from the reader's field and pass **that same cardboard unit** through the ultrasonic detection zone.
3. Complete the passage and leave a clear gap before beginning the next unit's cycle.

This is an accepted prototype workflow. Co-locating the sensors is a convenience for timing, **not an absolute requirement**.

### Timing and spacing rules

- Both streams remain independent; RFID-first is an operator workflow, not a firmware dependency.
- There is no mandatory RFID-to-ultrasonic pairing window under v4. Both streams contribute independently to the same device/gate movement bucket.
- A passage is emitted on exit, but its timestamp is the **entry time**, not exit or transmission time.
- Either stream may arrive first at the backend. Distinct UID/type counts and the independent passage total are finalized at movement closure; no per-unit pairing is performed.
- Complete each physical passage and leave a clear gap so the ultrasonic state machine can distinguish interruptions. Sensor spacing need not satisfy a nearest-time matching algorithm.
- Reusing the same UID requires the configured absence/cooldown, initially 3 seconds. Within one movement it still represents only one distinct unit, regardless of how many new logical observations are emitted.
- Keep normal inter-unit evidence gaps below the backend's 20-second movement boundary when demonstrating one batch. Do not implement that boundary in ESP32 firmware.

### What changed from the earlier correlation design

The earlier handoff required controlled spacing for v3's greedy nearest-time pairing. That policy is superseded. Under v4, RFID composition can pass even if ultrasonic undercounts or overcounts; the disagreement is a stored warning. Physical gaps still matter for reliable interruption counts, and the 20-second backend boundary still determines movement buckets.

Reported bench tests of 20 observations/passages do not establish 20 distinct rolling-stock identities if tags were reused. A consist with eight distinct coaches needs eight distinct registered coach UIDs; more physical tags are the only way to demonstrate that with real sensing. Seeded master data cannot create sensor evidence.

### Real railway limitation

Fixed track constrains physical order, but does not guarantee successful RFID reads or ultrasonic separation between coupled units. This project remains a **hand-operated tabletop monitoring/auditing prototype**, not a validated real-train consist counter or railway safety system. Do not describe the bench results as proof of real-railway reliability.

## 6. Event semantics and responsibility split

### ESP32: evidence and transport only

- Read RFID and distance independently.
- Convert repeated raw RFID reads into debounced/cooldown-controlled `RFID_DETECTED` observations.
- Reset each UID's last-seen time on **every successful read**, so a continuously visible tag does not emit another observation every 3 seconds.
- Convert completed occupied intervals into `PHYSICAL_PASSAGE` events.
- Preserve both streams even if their counts disagree; do not emit only correlated evidence.
- Retain passage entry/exit timestamps and duration metadata.
- Generate unique event IDs in the contract `<device_id>-<boot_id>-<sequence>`, with `boot_id` persisted across reboots.
- Connect to Wi-Fi, authenticate, queue events, and retry without blocking sensing.

The eventual `main.py` will control sampling, filtering, cooldown, and transport. It must **not fabricate events, force the two counts to agree, infer business validity, or discard discrepancies**.

### Backend: interpretation

- Authenticate the registered device.
- Store raw logical events and server `received_at`.
- Deduplicate transport retries by unique `event_id`.
- Construct/close movements using server receive time and the 20-second rule.
- Resolve distinct UIDs and their types; compare known composition against the expected plan without per-unit pairing.
- Match plans through observed locomotive RFID or unambiguous current coach-to-plan assignments. Keep expected/inferred locomotive information separate from actual observations.
- Store aggregate ultrasonic warnings, primary validation findings, RFID-based inventory updates, and deduplicated alerts.

Equal totals alone are not proof of correct composition. For a planned N-unit demonstration, count distinct known units by type, not raw RFID observations. Complete, otherwise valid RFID composition can remain `VALID` with an ultrasonic warning. Unknown tags, an absent expected locomotive RFID, wrong composition, or no usable plan can still prevent `VALID`.

For a bench test, the manually known input is ground truth. During operation, the backend has observed evidence and expected plan data; it does not possess an independent measurement of the actual number of units.

### Ingestion contract (implemented in CP-04)

```text
POST /api/ingest/events
X-Device-ID: gate-1
X-Api-Key: <device key>
```

Body: one event object or `{"events": [...]}` (max 50). One batch shares a single server receive instant.

```text
event_id   = <device_id>-<boot_id>-<sequence>     e.g. gate-1-7f3a2c-000042
event_type = RFID_DETECTED | PHYSICAL_PASSAGE     ("kind" accepted as an alias)
timestamp  = device clock, optional audit data
sequence   = per-boot counter
rfid_uid   = required for RFID_DETECTED, forbidden for PHYSICAL_PASSAGE
metadata   = passage started_at/ended_at/duration_ms and distance readings
```

`boot_id` is a short random string generated at firmware start and **persisted with the queue**, so an event queued before a reboot keeps its identifier. The server enforces global `event_id` uniqueness; the same id from a different device is a 409 conflict, not a retry.

Retry rules for firmware:

- `accepted` or `duplicate` — delete the event from the queue.
- `rejected` with an `error.code` — delete it and log; the payload will never succeed.
- HTTP 409 `LANE_MODE_NOT_SET` — keep the queue and retry after an operator selects the lane mode.
- HTTP 401/403 — stop and fix provisioning; retrying will not help.
- HTTP 400 — fix the firmware payload.

Unknown UIDs are accepted and stored as evidence; the backend decides at closure that they are `UNKNOWN_RFID`.

### Clock and retry requirements for later firmware

- Follow the UTC timestamp contract; do not label an unsynchronised/local clock as UTC. RTC/NTP handling has not yet been established by the shared test report.
- Retain sensing timestamps and passage entry/exit metadata for audit, even though v4 does not require per-unit pairing.
- Generate event IDs once and reuse them on retries. Use a boot/session-unique component plus sequence, or another genuinely unique scheme; `device_id + resettable_sequence` alone can collide after reboot.
- Use a bounded queue and preserve sequence order. Log overflow/data loss rather than hiding it.
- Device timestamps are audit data; `received_at` is for movement segmentation/closure. Delayed queue replay can distort receive-time segmentation, an inherited prototype limitation.

## 7. Location, station, yard, and direction decisions

The user asked how locations are distinguished. The mapping is:

```text
device_id -> registered station_id
device_id -> physical lane (not entry-only or exit-only)
movement -> direction chosen by the authenticated operator (lane mode)
```

- A registered ESP32 represents one physical lane. Devices are **not** classified as entry gates or exit gates; one lane can serve both directions at different times.
- Device identity and its API key identify the sender; the backend obtains the station from the device record.
- Direction is an explicit operator decision (`lane_mode`), captured on the movement when its bucket opens. Every change is audited with the user and timestamp.
- Do not trust payload `station_id` or `direction` to override the lane registration and lane mode.
- Inventory state must never be used to infer direction; per-unit flipping was analysed and rejected (see implementation log D-19). It can be used only to raise a `DIRECTION_CONFLICT` warning.
- One RFID reader/ultrasonic pair does not automatically infer ENTRY versus EXIT from hand movement.
- **Current v4 retains station-level inventory and no `yard_id`.** Multiple physical yard areas within a station are consolidated.
- No request was made to add separate per-yard inventory. If that becomes a requirement later, introduce an explicit yard model/device mapping and scope movements/inventory accordingly; ESP32 identity alone does not add those database semantics.
- The user currently reports one ESP32, not two installed gates. One physical lane can be switched between ENTRY and EXIT through the audited `lane_mode`; a physical two-gate deployment is a later hardware matter.

## 8. Backend implementation direction

**Working recommendation discussed:** Django + Django REST Framework + Django ORM/migrations + SQLite. Use automated tests, for example pytest with pytest-django. The user already knows Django; learning FastAPI is not necessary to demonstrate this architecture.

This is the implementation recommendation, **not an already completed backend**. The existing scaffold uses Django; DRF and domain apps are not yet configured. Adapt v3's framework-specific details to Django and apply v4's RFID-primary validation, coach-based plan lookup, and inventory policy.

Use a modular monolith. Do not introduce microservices, railway control, long-range RFID assumptions, or a separate yard model without an explicit requirement.

### Current repository state

The project contains the v2/v3/v4 specifications, this handoff, `AGENTS.md`, the existing `env/`, the Django project at `backend/`, and the ESP32 firmware at `firmware/`. The `yard` app now contains the domain models, admin, lane-mode audit, demo seed command, device-authenticated ingestion, movement buckets, RFID-primary validation, frozen inventory transitions, yard-master session authentication with station-scoped lane control, station-scoped read APIs, and the Django-served dashboard; migrations `0001`, `0002`, and `0003` are applied. The suite is **169 tests** (166 in `yard` plus 3 health tests) and passes. The firmware adds 29 hardware-free logic tests. The simulator was **dropped by user decision**; no simulator command remains. See the implementation log for the verified state and the CP-04/CP-06/CP-07a/CP-07b/CP-08/CP-09 file inventories.

Investigate the existing environment before using or replacing it. Preserve unfamiliar files and any user work. The current hardware scripts live outside the supplied repository context; do not claim to have inspected them.

### Dashboard accounts (added in CP-07a)

Authentication is a Django **session cookie plus CSRF**, not a per-user API token. Every account is bound to exactly one station through `StaffProfile`; `ADMIN` is station-scoped and there is no cross-station administrator. A dashboard request for another station's object returns 404, identical to a missing object.

Create a demo account with:

```bash
cd backend
python manage.py create_staff --username yard1 --station NDLS --role YARD_MASTER
```

`create_staff` creates a *dashboard* account and deliberately does **not** grant Django admin access. The admin site is a separate superuser console and is not station-scoped (D-43). The command requires `--password` when `--noinput` is used with a new account; an existing user can be updated without resetting the password.

Session surface:

```text
GET  /api/auth/csrf                 -> CSRF token and cookie
POST /api/auth/login                -> {"username", "password"} -> identity
POST /api/auth/logout               -> 204
GET  /api/auth/me                   -> identity
GET  /api/lanes                     -> lanes at the account's station
GET  /api/lanes/<device_id>         -> lane, open movement, recent mode changes
POST /api/lanes/<device_id>/mode    -> {"direction": "ENTRY"|"EXIT", "note": "..."}
```

A mode change while a movement is open is accepted: the open movement keeps its direction and its authorising `lane_mode_change`, and the new mode applies to the next movement (`applies_to: "NEXT_MOVEMENT"`). Device credentials and dashboard sessions are mutually exclusive; neither authenticates the other surface.

### Inventory and read APIs (added in CP-07b)

At movement closure the backend records a `MovementInventoryEffect` for every distinct UID actually read, freezing `status_before` **before** applying the transition. Revalidation judges anomalies against that snapshot, never replays a transition, and never overwrites a later movement's state. Known UIDs in a `VALID` movement become `VERIFIED`; known UIDs in `FLAGGED`/`UNVERIFIED` movements and unknown UIDs are `UNVERIFIED`. A planned locomotive that was never read receives no inventory transition.

```text
GET  /api/overview                     -> lanes, inventory totals, open movements, open alerts
GET  /api/movements                    -> state, direction, result, device_id, limit, offset
GET  /api/movements/<id>               -> movement, findings, alerts, events, inventory effects, observed counts
GET  /api/findings                     -> severity, code, movement
GET  /api/alerts                       -> severity, code, acknowledged
POST /api/alerts/<id>/acknowledge      -> idempotent; records acknowledged_by
GET  /api/inventory                    -> status, verification, q
```

Movement, inventory, and overview reads close idle buckets first (D-49), so the dashboard is correct even without a running sweep scheduler. All reads are station-scoped; another station's row is a 404.

### Dashboard (added in CP-08)

The dashboard is served by Django at the site root and consumes the read APIs with the session cookie. Pages are thin shells; the API remains the single implementation of scoping and business rules (implementation log D-50).

```text
GET  /                  -> overview: lanes, inventory totals, open alerts, open/latest movement
GET  /lanes/            -> lane mode control and current open movement
GET  /movements/        -> filterable history
GET  /movements/<id>/   -> counts, findings, alerts, events, inventory effects
GET  /inventory/        -> status/verification/UID filters
GET  /alerts/           -> severity/open filters and acknowledgement
GET  /login/            -> sign in
POST /logout/           -> sign out
```

Anonymous page requests redirect to `/login/?next=<path>`. An account without an active station profile gets an explanatory 403 page. A page for another station's movement is a 404 before any data is fetched. All displayed times are IST while storage stays UTC. Live pages poll every 5–10 seconds and stop polling a closed movement; a `401` during polling sends the browser to login.

Demo rehearsal:

```bash
cd backend
python manage.py seed_demo --reset
python manage.py create_staff --username yard1 --station NDLS --role YARD_MASTER
python manage.py runserver
# open http://127.0.0.1:8000/ and sign in as yard1
```

For another device on the LAN, run `python manage.py runserver 0.0.0.0:8000` and set `DJANGO_ALLOWED_HOSTS` to the server's LAN address.

A dashboard write is limited to lane mode and alert acknowledgement, both role-checked and audited. Plan reassignment remains a Django-admin operation (D-55).

### Firmware (added in CP-09)

`firmware/main.py` runs on the ESP32 and emits two independent event types: `RFID_DETECTED` and `PHYSICAL_PASSAGE`. It never pairs them (D-57). `firmware/logic.py` holds the rules that are hard to debug on hardware — hysteresis, confirmation samples, missing-echo hold, per-UID cooldown, tick wraparound, event ids, backoff, and the on-flash queue — and has 29 CPython tests. `firmware/README.md` carries wiring, provisioning, upload, tuning, and troubleshooting.

Bench-confirmed wiring: SPI bus 2 at 1 MHz with SCK 18, MOSI 23, MISO 19, CS 5, RST 22; HC-SR04 TRIG 26 and ECHO 25.

Provisioning:

```bash
cp firmware/config.example.py firmware/config.py   # edit: Wi-Fi, API_BASE, DEVICE_ID, API_KEY
cd backend
DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,<laptop-lan-ip>" \
  ../env/bin/python manage.py seed_demo --reset --rotate-key
../env/bin/python manage.py runserver 0.0.0.0:8000
```

`seed_demo --rotate-key` prints the lane's API key once; it is hashed on the server. `firmware/config.py` is gitignored and must never be committed.

The device keeps events in `/queue`, one JSON file per event, up to 2000, dropping the oldest on overflow. It deletes an event only when the server reports it `accepted`, `duplicate`, or `rejected`, and it retries `409` after 5 s, `401/403` after 60 s, other `4xx`/`429` after 30 s, and `5xx`/transport with exponential backoff. Until NTP answers, events carry no `timestamp` at all rather than a wrong one (D-59).

## 9. Recommended backend continuation order

Use v4 for the current policy, model changes, scenarios, and implementation order. Use compatible v3 API/authentication/transport details only where they do not conflict. Provide implementation code in chat unless code-file edits are explicitly authorized.

1. **Django foundation (adapt M0):** inspect the environment, establish project/configuration, timezone-aware UTC storage, SQLite, tests, and `GET /health` with database status and movement-gap configuration.
2. **Domain model and seed data (M1):** stations, users, registered devices, rolling stock, plans/current plan assignments, movements, events, findings, inventory, and alerts. Enforce the retained unique/index constraints; no pairing observation table is required.
3. **Ingestion (M2) — done in CP-04:** `POST /api/ingest/events`, `X-Device-ID` + `X-Api-Key`, single-event/small-batch support (50 max), UID normalization, per-event validation results, transactional storage, and idempotent duplicate handling. Duplicate events return HTTP 200 with the original movement and are not assigned or counted again.
4. **Movement construction (M3) — done in CP-04:** one OPEN movement per device, gap <= 20 seconds remains the same movement, gap > 20 seconds starts another, idempotent `sweep_movements` command. Direction is captured from the lane mode at bucket open. Closing currently marks the movement and adds an INFO `VALIDATION_PENDING` finding; validation fills the result in M4.
5. **RFID-primary validation (M4) — done in CP-06:** direct/fallback plan lookup, distinct known composition by type, aggregate ultrasonic warning, stored findings and alerts, and idempotent revalidation. A movement is validated inside its closure transaction and the outcome (`VALID` / `FLAGGED` / `UNVERIFIED`) is visible immediately. No per-unit pairing anywhere. `python manage.py revalidate_movements` backfills anything closed before validation existed.
6. **Authentication and lane control (M5a) — done in CP-07a:** Django-session login/logout/me, a fail-closed global `IsAuthenticated` default, station scoping from `StaffProfile`, role checks (`VIEWER` read-only), audited `set_by` on every lane-mode change, and `create_staff` for station-bound demo accounts. Device keys and sessions are mutually exclusive.
7. **Inventory and read APIs (M5b) — done in CP-07b:** one inventory transition per distinct UID actually read, applied at closure against a frozen pre-movement snapshot, so revalidation never repeats or rewinds a transition. Unknown UIDs are retained as unverified, unexpected exits are unverified `OUT` rows, and station-scoped movement, finding, alert, and inventory read endpoints are live. A planned locomotive that was not read receives no transition.
8. **Dashboard (M6) — done in CP-08:** Django-served pages over the read APIs, with server-side session/station guards, lane-mode control, alert acknowledgement, IST display, and live refresh. The simulator was removed from scope (D-52); the demo uses real sensors and seeded master data.
9. **ESP32 firmware (M7) — done in CP-09, hardware run pending:** complete `main.py` plus `logic.py`, `mfrc522.py`, a committed config template, 29 logic tests, and a README. Independent RFID and passage events, off-line queue with persisted event ids, clock that stays unset until NTP, and an explicit per-status retry schedule. Verified against the live API with hardware modules stubbed; not yet run on the ESP32.
10. **Integrated rehearsal (M8):** rerun sensing acceptance with actual firmware, HTTP ingestion, retry/reconnection, the separated-sensor card workflow, and the dashboard open on the same network.

### Important backend test coverage

- Duplicate retries return 200 and create no extra logical observations or inventory changes.
- Unknown/disabled/mismatched devices are rejected; keys are never logged or returned.
- Station comes from the device record; direction comes from the audited lane mode captured at bucket open, not from spoofable request fields.
- Movement closure occurs without needing another event; exact 20-second boundaries are tested.
- Distinct UID/type counts are independent of event arrival order within the same movement; repeated UID observations count once.
- Complete RFID composition can remain `VALID` with fewer, extra, or zero ultrasonic passages, while retaining the discrepancy warning.
- A higher physical count supports possible missed identification but must not supply an invented UID.
- Coach-based fallback binds only an unambiguous eligible plan, flags the absent locomotive RFID, and does not conceal a conflicting observed locomotive.
- Unknown tags, passages-only evidence, RFID-only evidence, wrong/unplanned composition, unexpected exits, and repeated validation follow v4.
- Actually read UIDs update inventory without pairing. Retries, repeated UIDs, and revalidation never repeat transitions or overwrite a later movement's state.
- A dashboard session cannot authenticate ingestion, and a device key cannot authenticate a dashboard request.
- Another station's lane is a 404 byte-identical to a missing lane; a `VIEWER` is refused a lane-mode change with `403 ROLE_NOT_PERMITTED`; every accepted change records the authenticated `set_by`.
- Dashboard page tests cover anonymous redirect with `next`, missing/inactive profile 403 codes, inactive station, foreign movement 404, viewer read-only rendering, POST-only logout, and static asset discovery.

### Clarifications to retain while implementing

- v4 makes the outcome procedure explicit: zero known RFID observations -> `UNVERIFIED`; otherwise any primary FAIL -> `FLAGGED`; otherwise `VALID`, potentially with ultrasonic warnings. Missing ultrasonic evidence alone does not make a complete RFID movement unverified.
- Plans validate counts plus expected lead locomotive identity. Current coach-to-plan assignments support fallback lookup; they do not automatically require exact-roster validation or prove which unobserved unit physically passed.
- Devices are lanes, not entry/exit gates. `lane_mode` is an explicit operator decision with an append-only audit trail; an unset mode rejects ingestion with `LANE_MODE_NOT_SET`. Inventory state is never used to infer direction — only to raise a `DIRECTION_CONFLICT` warning.

Keep these distinctions visible without adding per-unit sensor matching or expanding the railway-control scope.

## 10. Hardware details: confirmed and outstanding

CP-09 was written against the user's working bench build, so the wiring questions that used to block firmware work are answered.

Confirmed:

- **RC522:** SPI bus 2 at 1 MHz, SCK 18, MOSI 23, MISO 19, CS 5, RST 22, 3.3 V supply.
- **HC-SR04:** TRIG 26, ECHO 25, 5 V supply. Echo is wired **directly** to GPIO 25, which is outside the pin's 3.3 V specification. This is the user's choice after roughly two years of service; the README documents a 1 kΩ series plus 2 kΩ to ground divider as the containment. It needs no firmware change.
- **Event semantics:** independent `RFID_DETECTED` and `PHYSICAL_PASSAGE`, no pairing.
- **Clock:** no `timestamp` until NTP answers; ordering is the server's `received_at`.
- **Queue:** one JSON file per event, atomic rename, 2000 bound, oldest dropped, `event_id` persisted with the event.
- **Provisioning:** `DEVICE_ID` and `API_KEY` in gitignored `config.py`; `seed_demo --rotate-key` prints the key once.

Still to be observed on hardware (nothing in the repository depends on the answers):

- Whether this bench's Wi-Fi reaches an NTP server, and how quickly after boot. A failure is harmless; it only means no device timestamps.
- The real resting distance at the lane, which decides whether the 140/150 cm hysteresis band is right. Tune `PASSAGE_ENTER_CM` and `PASSAGE_EXIT_CM` from a measured value.
- Reliable tag presentation distance and how long a tag stays in the RC522 field.
- Whether anything in the ultrasonic cone (a person, a bag, a passing trolley) triggers phantom passages.
- Wi-Fi reconnect behaviour after the access point drops. Credentials must not be written into project documentation or logs.

Do not force card detections into pairs in firmware. Preserve the actual independent observations so the backend can expose count/identity discrepancies under v4.

## 11. Suggested prompt for the next session

> Read `AGENTS.md`, `railway_station_yard_iot_backend_handoff.md`, `railway_station_yard_iot_project_context_v4.md`, and `railway_station_yard_iot_backend_implementation_log.md`. The Django backend is implemented and tested (169 tests): RFID-primary distinct UID/type counts, aggregate ultrasonic warnings, coach-based plan lookup, RFID-primary inventory, session authentication with station-scoped lane control, station-scoped read APIs, and the dashboard at `/`. The ESP32 firmware is written and verified off-hardware (29 logic tests plus a live HTTP check against the real endpoint); it has not yet run on the board. Devices are physical lanes with an audited operator-selected `lane_mode`; do not infer direction from inventory state, do not implement per-unit pairing, and never fabricate a missed locomotive read. The simulator is out of scope: the demo uses real sensors plus seeded master data. Next is CP-10 — the integrated rehearsal: run `seed_demo --reset --rotate-key`, create the demo account, start `runserver 0.0.0.0:8000` with the laptop's LAN address in `DJANGO_ALLOWED_HOSTS`, copy `firmware/main.py`, `logic.py`, `mfrc522.py` and an edited `config.py` to the board, then present the two bench tags (`FA37E63F`, `0E78E13F`) at lane `gate-1` in `ENTRY` mode and confirm the movement closes after the 20-second gap with the expected findings, inventory, and alerts. Report bugs to me; do not change backend policy without asking, and treat `firmware/config.py` as secret.
