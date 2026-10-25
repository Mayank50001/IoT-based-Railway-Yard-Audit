# Railway Yard IoT — Hardware Progress and Backend Session Handoff

**Updated:** 2026-10-25  
**Next task:** Build CP-04 authenticated ingestion (`POST` single/batch events, idempotent `event_id`, canonical UIDs, lane-mode check). Then movement buckets, validation, inventory, and simulation. Progress is tracked in the [Backend Implementation Log](railway_station_yard_iot_backend_implementation_log.md).

> Read this handoff first, then [RFID-Primary Specification — v4](railway_station_yard_iot_project_context_v4.md). Use v3 only for compatible background details, not its superseded mandatory-pairing/paired-inventory rules. Progress, decisions, and verification steps are recorded in the [Backend Implementation Log](railway_station_yard_iot_backend_implementation_log.md) — check it before starting work. This handoff records reported hardware results and the latest implementation direction; it is not a claim that the assistant independently ran the hardware tests or implemented the backend.

## 1. Current outcome and user preferences

- The user has previous Python/Django experience and has made multiple Django projects.
- The user already has an ESP32 running MicroPython, Thonny, an ultrasonic sensor, and an RC522 reader/writer with tags/keyfobs.
- The user performed hardware tests with Gemini's help and reported successful basic bring-up and concurrent sensing.
- The user subsequently confirmed that the requested acceptance tests pass with the RFID-first, physically separated sensor arrangement described below. Individual test logs and numerical run results have not been supplied to this repository.
- The immediate goal is to continue the **backend**, not to generate ESP32 firmware yet.
- After the backend/event contract is ready, the user expects complete MicroPython **`main.py` code for the ESP32**, including any necessary driver dependency rather than unexplained imports.
- The user approved moving forward with **distinct RFID UID counts as primary validation**, ultrasonic counts as supporting evidence, and coach-based lookup of expected locomotive/plan information when the locomotive RFID is absent.
- Follow `AGENTS.md`: provide code in chat unless code-file edits are explicitly authorized, and ask before accessing files outside the project. This revision changes documentation only.

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
- The current bench has two tags, so `seed_demo` includes **`NDLS-IN-BENCH`** (1 loco + 1 coach) mapped exactly to `FA37E63F` and `0E78E13F`. The 4-coach scenarios are demonstrated with the software simulator until more tags are available.

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

Reported bench tests of 20 observations/passages do not establish 20 distinct rolling-stock identities if tags were reused. A consist with eight distinct coaches needs eight distinct registered coach UIDs; the simulator can supply these before hardware integration.

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
- Generate unique event IDs and per-boot monotonic sequence numbers.
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

For a bench test or simulator, the manually known/generated input is ground truth. During operation, the backend has observed evidence and expected plan data; it does not possess an independent measurement of the actual number of units.

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
- The user currently reports one ESP32, not two installed gates. Backend simulation can cover ENTRY and EXIT immediately; a physical two-gate deployment is a later hardware matter.

## 8. Backend implementation direction

**Working recommendation discussed:** Django + Django REST Framework + Django ORM/migrations + SQLite. Use automated tests, for example pytest with pytest-django. The user already knows Django; learning FastAPI is not necessary to demonstrate this architecture.

This is the implementation recommendation, **not an already completed backend**. The existing scaffold uses Django; DRF and domain apps are not yet configured. Adapt v3's framework-specific details to Django and apply v4's RFID-primary validation, coach-based plan lookup, and inventory policy.

Use a modular monolith. Do not introduce microservices, railway control, long-range RFID assumptions, or a separate yard model without an explicit requirement.

### Current repository state

The project contains the v2/v3/v4 specifications, this handoff, `AGENTS.md`, the existing `env/`, and the Django project at `backend/`. The `yard` app now contains the domain models, admin, lane-mode audit, demo seed command, and tests; migrations `0001` and `0002` are applied. Ingestion endpoints, movement construction, and validation services are **not** implemented yet. See the implementation log for the verified state.

Investigate the existing environment before using or replacing it. Preserve unfamiliar files and any user work. The current hardware scripts live outside the supplied repository context; do not claim to have inspected them.

## 9. Recommended backend continuation order

Use v4 for the current policy, model changes, scenarios, and implementation order. Use compatible v3 API/authentication/transport details only where they do not conflict. Provide implementation code in chat unless code-file edits are explicitly authorized.

1. **Django foundation (adapt M0):** inspect the environment, establish project/configuration, timezone-aware UTC storage, SQLite, tests, and `GET /health` with database status and movement-gap configuration.
2. **Domain model and seed data (M1):** stations, users, registered devices, rolling stock, plans/current plan assignments, movements, events, findings, inventory, and alerts. Enforce the retained unique/index constraints; no pairing observation table is required.
3. **Ingestion (M2):** `POST /api/ingest/events`, device-key authentication, single-event/small-batch support, UID normalization, event validation, transactional storage, and idempotent duplicate handling. A duplicate event returns HTTP 200 without assigning/counting it again.
4. **Movement construction (M3):** one OPEN movement per device, gap <= 20 seconds remains the same movement, gap > 20 seconds starts another, and idempotent sweep plus lazy closure. Direction is captured from the lane mode at bucket open.
5. **RFID-primary validation (M4):** direct/fallback plan lookup, distinct known composition, aggregate ultrasonic warnings, stored findings, alerts, and idempotent revalidation. Do not implement greedy or order-preserving sensor pairing.
6. **Inventory and APIs (M5):** update distinct actually read UIDs at closure without requiring ultrasonic pairs, retain unknown UIDs as unverified, handle unexpected exits, and scope dashboard/admin access appropriately. Missing/inferred locomotive UIDs must not generate observations or new inventory transitions.
7. **Simulator/dashboard (M6 and supporting work):** use the same ingestion endpoint and event schema for the v4 acceptance scenarios. Adapt older scenarios to the revised warning/inventory policy. The backend must be fully demonstrable without physical hardware.
8. **Final ESP32 firmware (M7):** provide the requested complete `main.py` against the stable API contract; confirm GPIOs/electrical details and preserve existing boot files before deployment.
9. **Integrated rehearsal (M8):** rerun sensing acceptance with actual firmware, HTTP ingestion, retry/reconnection, and the separated-sensor card workflow.

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

### Clarifications to retain while implementing

- v4 makes the outcome procedure explicit: zero known RFID observations -> `UNVERIFIED`; otherwise any primary FAIL -> `FLAGGED`; otherwise `VALID`, potentially with ultrasonic warnings. Missing ultrasonic evidence alone does not make a complete RFID movement unverified.
- Plans validate counts plus expected lead locomotive identity. Current coach-to-plan assignments support fallback lookup; they do not automatically require exact-roster validation or prove which unobserved unit physically passed.
- Devices are lanes, not entry/exit gates. `lane_mode` is an explicit operator decision with an append-only audit trail; an unset mode rejects ingestion with `LANE_MODE_NOT_SET`. Inventory state is never used to infer direction — only to raise a `DIRECTION_CONFLICT` warning.

Keep these distinctions visible without adding per-unit sensor matching or expanding the railway-control scope.

## 10. Information needed before final `main.py`

Obtain these later; they do not block simulator-based backend development:

- Exact SCK/MOSI/MISO/CS/RST/TRIG/ECHO GPIO mapping.
- Exact ultrasonic and tag models; verified safe Echo level shifting where applicable.
- Current combined test script, or a clear statement that it will be replaced after backup.
- Actual sensor placement, reliable RFID presentation distance, and physical clear-gap behaviour. There is no mandatory per-unit pairing-window measurement under v4.
- Final detection/clear thresholds, filtering, occupied/clear durations, timeout policy, and cooldown behaviour.
- Wi-Fi connection/reconnection results; credentials should not be put in project documentation or logs.
- Registered device ID, API URL, and secure provisioning of its key.
- UTC clock strategy, bounded queue/overflow behaviour, and reboot/event-ID handling.

Do not force card detections into pairs in firmware. Preserve the actual independent observations so the backend can expose count/identity discrepancies under v4.

## 11. Suggested prompt for the next session

> Read `AGENTS.md`, `railway_station_yard_iot_backend_handoff.md`, `railway_station_yard_iot_project_context_v4.md`, and `railway_station_yard_iot_backend_implementation_log.md`. My ESP32/RC522/ultrasonic bench tests are reported passing with separated sensors. Continue the existing Django project using RFID-primary distinct UID/type counts, aggregate ultrasonic warnings, coach-based plan lookup, and RFID-primary inventory. Devices are physical lanes with an audited operator-selected `lane_mode`; do not infer direction from inventory state. Do not implement per-unit pairing or fabricate a missed locomotive read. Preserve compatible v3 authentication, transport, station, and movement-gap rules. Next: seed data, then tested ingestion, movement construction, validation, inventory, and simulation. Do not generate ESP32 firmware yet.
