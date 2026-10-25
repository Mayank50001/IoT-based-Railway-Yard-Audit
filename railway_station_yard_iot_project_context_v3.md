# IoT-Based Railway Station Yard Entry/Exit Inspection and Monitoring System
## Project Context & Technical Specification — v3

> **What changed in v3:** This revision is adapted to the actual demonstration hardware: **paper/cardboard rolling-stock units with RFID tags, hand-passed through a tabletop gate. No model train is required.** The ultrasonic speed/gap analysis from v2 has been replaced by explicit hand-operated demonstration rules, and the four backend algorithms that were previously left implicit (evidence pairing, plan matching, inventory derivation, and status/findings storage) are now specified.

> **Superseded validation policy (2026-10-25):** Read [RFID-Primary Specification — v4](railway_station_yard_iot_project_context_v4.md) and [Hardware Progress and Backend Session Handoff](railway_station_yard_iot_backend_handoff.md) first. The user approved distinct RFID UID counting as primary evidence, ultrasonic counts as advisory evidence, coach-based plan lookup, and RFID-primary inventory. The mandatory pairing and paired-only inventory rules below are historical and must not be implemented as the current policy. Compatible authentication, transport, station-scoping, and API details remain useful. A basic Django scaffold exists; the domain backend and final ESP32 `main.py` are not implemented.

---

## 0. Plain-Language Summary (read this first)

The system is a **gate with a notebook**.

| Real world | This project |
|---|---|
| A guard sees someone walk through | The ultrasonic sensor reports `PHYSICAL_PASSAGE` |
| The person shows an ID badge | The RFID reader reports `RFID_DETECTED` with a tag UID |
| The guard writes both facts in a register | The backend stores raw events |
| The guard checks the expected visitor list | The backend matches the movement against a `movement_plan` |
| The guard raises a complaint if something is wrong | The backend creates an `alert` |

The guard **does not stop anyone** and **does not close the gate**. The system records and flags. That is the entire project.

### The four rules that do all the real work

1. **Pairing.** The sensor says "something passed"; the reader says "tag X was here". If a tag was read within a short window of a passage, they are treated as the same unit. A passage with no tag is flagged; a tag with no passage is flagged.
2. **Plan matching.** Before a movement, the system may hold an expected list ("LOCO-1847 with 4 coaches"). If the observed movement matches it, compare counts. If no plan exists, the movement is recorded but flagged as unplanned.
3. **Inventory.** A register of what is currently inside the yard. Entry adds, exit removes. Unknown tags still appear, but as unidentified units.
4. **Findings.** Every check produces a stored result and a human-readable reason, so the dashboard can explain *why* a movement was flagged.

### Words used in this document

| Term | Plain meaning |
|---|---|
| `movement` | One batch of units passing one gate in one direction |
| `plan` | The expected list for a movement |
| `VALID` | All checks passed |
| `FLAGGED` | At least one check failed; a human should look |
| `UNVERIFIED` | Evidence exists but is insufficient to confirm identity |
| `inventory` | What is currently inside the yard |
| `gate` / `device` | One ESP32 with one RFID reader and one ultrasonic sensor |

---

## 1. Project Overview

This project is an IoT-based monitoring and auditing system for rolling stock entering and exiting a railway station's monitored yard.

The system is **passive and audit-oriented**. It observes physical movement and RFID identity evidence, sends that evidence to a central backend, correlates the evidence into movements, validates observed rolling stock against expected movement information, maintains current yard inventory, and raises alerts for inconsistencies.

The system **does not control trains or railway infrastructure**.

It does not:
- control signalling
- operate points/switches
- control barriers
- apply brakes
- stop or start trains
- authorize train movement
- replace railway safety systems

The purpose of the prototype is to demonstrate reliable **entry/exit observation, identification, movement construction, validation, inventory tracking, and alerting**.

---

## 2. Scope

### In scope
- Railway station-level yard monitoring
- Rolling stock entering and exiting the monitored yard
- RFID-based rolling-stock identification
- Ultrasonic-based physical passage detection
- ESP32-based IoT devices
- Wi-Fi communication
- Backend event ingestion
- Movement construction
- Expected-vs-observed validation
- Current rolling-stock inventory
- Alerts and discrepancies
- Yard-master dashboard
- Simulated events for backend/frontend development
- Audit history and evidence preservation
- Tabletop demonstration using paper/cardboard rolling-stock units with RFID tags, passed by hand through a gate

### Out of scope
- Train control
- Signalling
- Track switching
- Automatic barrier control
- Braking
- Safety-critical railway operation
- Exact physical track/location inside the yard
- Railway signalling-system integration
- Industrial long-range RFID infrastructure
- Production railway deployment

---

## 3. Core System Concept

The system uses two independent sources of evidence:

1. **RFID** — identifies which rolling-stock unit was detected.
2. **Ultrasonic sensor** — identifies that a physical rolling-stock unit passed the detection point.

The backend combines both.

```text
RFID + Ultrasonic
       |
      ESP32
       |
     Wi-Fi
       |
    Backend
       |
  +----+----+
  |    |    |
  v    v    v
Movement Validation Inventory
Construction    |       |
  |             v       v
  +----------> Alerts Dashboard
```

The ESP32 is responsible for sensing and transmitting evidence.

The backend is responsible for interpretation and business logic.

---

## 4. Station and Yard Model

The organizational entity is the **station**, not an individual physical yard.

A station may contain multiple physical yard areas, but they are managed under the same station-level yard-master account.

Therefore the primary organizational identifier is:

```text
station_id
```

There is intentionally no `yard_id` in the core model.

---

## 5. Rolling Stock Types

The prototype supports:

- `LOCO`
- `COACH`
- `WAGON`

Each identifiable unit can have an RFID UID.

---

# 6. Hardware Architecture

## 6.1 ESP32

Responsibilities:
- Wi-Fi connection
- RC522 communication
- RFID reading
- ultrasonic sensing
- physical-passage detection
- passage entry/exit timestamps and duration metadata
- RFID debounce/cooldown
- event IDs and sequence numbers
- event transmission and retry
- device identity

The ESP32 does **not** decide movement authorization, validation, inventory truth, alerts, or the 20-second movement boundary.

## 6.2 RC522 / MFRC522 RFID Reader

The prototype uses an RC522/MFRC522 reader with compatible MIFARE Classic S50/1K tags.

The reader communicates with ESP32 through SPI.

The RFID UID is used as the hardware-level identity.

Limitations:
- short read range
- tag must pass close to the reader
- not an industrial railway long-range RFID reader
- UID-based identification is not resistant to cloning
- therefore suitable for this college prototype, not a security-critical railway deployment

The tag is mounted at a predictable position so it passes within the reader's usable range.

For the tabletop demonstration:

- Tape the tag flat to the top edge of each paper/cardboard unit.
- Mount the reader facing the slot, with the tag passing 1–2 cm from the antenna.
- Slide the unit through at a comfortable hand pace (roughly 0.3 m/s). There is no minimum speed, but do not stop with the tag inside the field, or the cooldown in §8 may suppress a later legitimate read.
- Do not rely on reading through a thick cardboard box wall; cut an opening or mount the reader inside the slot.

---

# 7. Ultrasonic Physical-Passage Detection

## 7.1 Role

The ultrasonic sensor is the physical-passage evidence source. It answers only one question:

```text
Did a physical object pass the gate?
```

It does not identify the object and does not count coaches in a real train. The backend combines its output with RFID evidence.

## 7.2 Demonstration Arrangement (paper units, hand-passed)

There is **no model train** in this prototype. The rolling-stock units are paper or cardboard cards, each labelled with a unit identifier and carrying an RFID tag. They are passed through a tabletop gate by hand.

Recommended gate construction:

```text
            RC522 reader (facing the slot)
                     |
        +------------+-------------+
        |          [ slot ]        |
        |            |             |
        |      ultrasonic beam     |   <- beam crosses the slot
        |      (emitter -> fixed   |
        |       reflector plate)   |
        +--------------------------+
              cardboard box
```

Operating rules:

1. **One unit at a time.** Insert fully, withdraw fully, then pause briefly before the next unit.
2. **Leave a visible gap between units** — roughly 30 cm / one card length. The gap is what produces two passages instead of one.
3. **Keep the hand out of the detection zone.** This is the main practical trap: a hand is an object and will generate its own passage. Use a narrow slot that the hand cannot enter, hold the card with a long stick or thread, or mount the sensor deep enough inside the box that only the card reaches the beam.
4. **Do not stop with the card inside the beam.** Complete the pass so exactly one interval is produced.
5. **Move at a comfortable hand pace** (roughly 0.3 m/s). There is no speed requirement to satisfy; the operator controls the timing.
6. **Keep the reader and the beam close together** (a few centimetres apart) so the tag read and the passage entry happen inside the pairing window (§17.1). If they are far apart, a slow hand pass can exceed the window and produce a false "tag without passage".

The old model-train speed and gap calculations are intentionally removed. They do not apply to a hand-operated tabletop demonstration and were the source of unnecessary complexity.

## 7.3 Ultrasonic State Detection

The ESP32 converts raw distance readings into logical intervals with a small state machine:

```text
CLEAR
  |
  | object enters detection zone
  v
OBJECT_PRESENT
  |
  | object leaves / gap detected
  v
CLEAR
```

A completed `OBJECT_PRESENT` interval becomes exactly one:

```text
PHYSICAL_PASSAGE
```

Filtering rules (all configurable):

- require **N consecutive readings** inside the detection threshold before entering `OBJECT_PRESENT` (prevents noise from starting a passage);
- require **M consecutive readings** beyond the clear threshold before returning to `CLEAR` (prevents a brief reflection dropout from splitting one unit into two);
- use **hysteresis**: detection distance and clear distance are different values;
- enforce a **minimum object duration** and a **minimum clear duration**;
- ignore a completed interval shorter than the minimum duration as noise.

Raw readings must not be sent to the backend. Only logical `PHYSICAL_PASSAGE` events are transmitted.

## 7.4 Passage Timestamp and Metadata

**The passage timestamp must be the moment the object entered the detection zone**, even though the event can only be emitted when the object leaves.

Reason: RFID reads happen while the unit is in front of the reader. If the passage were timestamped at exit, unit *n*'s passage could be emitted after unit *n+1*'s RFID read, and time-based pairing would associate the wrong unit. Timestamping at entry preserves the true order of units.

Each `PHYSICAL_PASSAGE` event should carry:

```json
{
  "event_id": "ENTRY-01-000001285",
  "device_id": "ENTRY-01",
  "event_type": "PHYSICAL_PASSAGE",
  "timestamp": "2026-10-21T09:30:09.400Z",
  "sequence": 1285,
  "metadata": {
    "started_at": "2026-10-21T09:30:09.400Z",
    "ended_at": "2026-10-21T09:30:09.850Z",
    "duration_ms": 450,
    "min_distance_cm": 8,
    "peak_distance_cm": 41
  }
}
```

The backend uses this **entry timestamp from the device clock** as the pairing key, because both the passage and the RFID read come from the same device and share the same clock. Server `received_at` is used for movement segmentation and closure (§10, §11), not for pairing.

## 7.5 Sensor Geometry

A sensor aimed sideways at a passing train sees a continuous wall and cannot see gaps. Gap detection therefore requires **occlusion geometry**:

- emitter and receiver on opposite sides of the slot (or emitter facing a fixed reflector plate across the slot), so the unit **breaks the beam**; or
- a sensor mounted so the unit passes directly through its line of sight.

With a standard HC-SR04, use one sensor facing a fixed flat reflector across the slot and treat "distance changes from the reflector baseline" as the detection signal. The beam must not be blocked permanently by the box itself.

## 7.6 Ultrasonic Validation Before the Demo

Before building the backend around the sensor, run a short bench test:

1. Pass 20 units with gaps, and confirm the firmware emits exactly 20 `PHYSICAL_PASSAGE` events.
2. Pass units with no gap and confirm they are counted as one unit (this is expected and should be explained in the demo).
3. Move a hand through the gate and confirm the behaviour, then adjust the slot or mounting to prevent it.
4. Repeat at the actual demonstration pace.
5. Record the chosen thresholds in configuration.

If the bench test cannot reliably produce one passage per unit, change the mechanical arrangement before changing the software. The physical layout is the variable that matters.

## 7.7 Limitations of This Approach

- Counts depend on the operator leaving a gap; a continuous strip of paper will count as one unit.
- Reflective or irregular paper surfaces can produce noisy distances.
- Hand intrusion creates false passages unless the gate is designed to prevent it.
- The sensor reports a passage, not an identity; all identification comes from RFID.

---

# 8. RFID Repeated-Read Handling

A tag remains in the RC522 field for multiple read cycles, so one paper unit can generate many identical detections.

Use a configurable **same-UID cooldown per device**, for example:

```text
rfid_cooldown_seconds = 3
```

Rules:

- key the cooldown by `(device_id, rfid_uid)`, not by UID alone;
- reset the timer on each successful read of that UID;
- after the cooldown expires, a new read of the same UID is a new observation.

This is an **edge-side optimisation only**. The backend must deduplicate independently, and it must handle two different kinds of duplication:

| Duplicate kind | Cause | Backend rule |
|---|---|---|
| **Transport duplicate** | ESP32 retried because the HTTP response was lost | Reject by unique `event_id`; return the original result |
| **Repeated observation** | Same tag read again while still in the field, or the operator passed the same unit twice | Keep the event, but flag `DUPLICATE_OBSERVATION` if the same UID appears twice in one movement outside the cooldown |

Do not silently delete repeated observations: they are evidence, and a second appearance of the same unit is exactly the kind of anomaly an audit system should surface.

---

# 9. Entry and Exit

Use one movement model with:

```text
ENTRY
EXIT
```

Example:

```text
ENTRY-01 -> ENTRY
EXIT-01  -> EXIT
```

The backend should not duplicate business logic for entry and exit.

Entry and exit movements may overlap, so active movement should be scoped at least by:

```text
station + direction + device/gate
```

For this prototype:

```text
one active movement per
(station_id, direction, device_id)
```

**Direction is a property of the registered device, not of the event payload.** The backend reads it from the `devices` record. If a payload contains a `direction` field, it is ignored for business logic and kept only as diagnostic metadata. This prevents a misconfigured or spoofed device from flipping entry into exit.

The same rule applies to `station_id`: it comes from the device record, never from the request body.

---

# 10. Movement Construction

A movement is a backend construct, not an individual RFID or ultrasonic event.

Multiple observations/passages can belong to one movement.

## 10.1 20-Second Rule

Prototype rule:

```text
gap <= 20 seconds  -> same movement
gap > 20 seconds   -> new movement
```

The 20-second value is a prototype assumption, not a universal railway rule.

The gap is measured from the **last evidence** assigned to the movement (passage or RFID), using server `received_at` time. It is not measured only from the last passage.

It should be configurable in the backend and not hardcoded into ESP32 firmware.

## 10.2 Movement Closure

A movement must close even if no future event arrives:

```text
movement starts
      |
last evidence (passage or RFID)
      |
no more events
      |
20 seconds pass
      |
movement closes
```

Implementation requirements:

- Each movement stores `last_evidence_at`, updated by **every** event assigned to it (passage or RFID), not only passages.
- A movement is closable when `now - last_evidence_at > movement_gap_seconds`.
- Closing is performed by **both**:
  1. a periodic sweep (for example, every 5 seconds), and
  2. a lazy check whenever an API read or a new event arrives.
- Both paths must be idempotent: closing an already-closed movement must do nothing.
- On closure: set `ended_at = last_evidence_at`, `state = CLOSED`, run validation once, store findings, update inventory, and generate alerts.
- `movement_gap_seconds` is backend configuration. It is never sent to, or decided by, the ESP32.

This guarantees that the last movement of a demonstration does not stay `OPEN` forever on the dashboard.

---

# 11. Event Model

Core event types:

```text
RFID_DETECTED
PHYSICAL_PASSAGE
```

Example:

```json
{
  "event_id": "ENTRY-01-000001284",
  "device_id": "ENTRY-01",
  "event_type": "RFID_DETECTED",
  "timestamp": "2026-10-21T09:30:08.120Z",
  "rfid_uid": "7F:32:AA:91",
  "sequence": 1284
}
```

The backend adds:

```text
received_at
```

Server receive time is authoritative for movement segmentation, closure, and dashboard ordering. The ESP32 has no reliable real-time clock in this prototype, so device timestamps must not be compared across devices or used for segmentation. They are still the correct relative clock for pairing events produced by the same device.

Time rules:

- store all timestamps in **UTC** (ISO 8601 with `Z`);
- render local Indian time (IST) only in the dashboard;
- use `received_at` to order events on the dashboard, to segment movements, and to update `last_evidence_at`;
- use device timestamps to pair an RFID observation with a passage, because both come from the same device clock;
- keep the device timestamp to diagnose clock drift and offline buffering;
- do not compare a device timestamp from one device with a server timestamp from another without normalisation.

Known prototype limitation: if a device queues events and replays them much later, `received_at`-based segmentation may place them in the movement that is active at replay time. Replay the queue as soon as connectivity returns, and document this in the demonstration notes.

---

# 12. Event Idempotency and Ordering

Every event must contain:

- `event_id`
- `device_id`
- `sequence`
- device timestamp

`event_id` is generated by the device and must be unique.

Retries must not create duplicate logical observations. Therefore:

```text
UNIQUE (event_id)
INDEX  (device_id, sequence)
```

`sequence` is **not** unique across reboots: if the device restarts and the counter resets, a lower sequence is treated as the start of a new device session, not as a duplicate. Idempotency is guaranteed by `event_id` alone.

Ingestion behaviour for a duplicate:

- do **not** create a second event row;
- do **not** re-run movement assignment or validation;
- return **HTTP 200** with the original result (not `409`), so the device stops retrying;
- optionally record a counter or log line for diagnostics.

`sequence` is a monotonically increasing per-device counter. It is used to detect:

- missing events (`sequence` gaps), and
- out-of-order delivery.

A sequence gap does **not** invalidate a movement; it is recorded as diagnostic evidence and may raise `IMPLAUSIBLE_EVENT_RATE` if the gap pattern looks like data loss rather than a lost packet.

Ingestion must be transactional: store the event and update the movement in one database transaction, so a crash cannot leave an event stored but unassigned.

---

# 13. Device Model and Authentication

Recommended:

```text
devices
-------
id
device_id
station_id
direction
device_key_hash
last_seen_at
enabled
```

For this project, a device API key is sufficient.

Example header:

```text
X-Device-Key
```

This is intentionally simpler than introducing HMAC unless stronger authentication is required.

Authentication rules:

- The request supplies `device_id` and the key in `X-Device-Key`.
- The server looks up the device, hashes the supplied key, and compares using a **constant-time comparison**.
- Reject unknown, disabled, or mismatched devices with `401`, and record `UNREGISTERED_DEVICE` for audit purposes when a plausible device id is presented.
- Update `last_seen_at` on every accepted request.
- Disabling a device (`enabled = false`) immediately revokes its key without deleting its history.
- The key is never logged, and never returned by any API.

Device authentication is separate from yard-master authentication: `/api/ingest/*` accepts a device key, while dashboard endpoints accept a user token.

---

# 14. Backend Architecture

Recommended stack:

- Python
- FastAPI
- SQLAlchemy
- SQLite
- Alembic
- pytest

Use a modular monolith rather than microservices.

Backend responsibilities:
1. authentication
2. station association
3. device authentication
4. event ingestion
5. event storage
6. movement construction
7. RFID resolution
8. evidence pairing
9. movement-plan matching and validation
10. inventory
11. alerts and findings
12. dashboard APIs
13. development event simulation

---

# 15. Suggested Database Model

## users

```text
id
name
username
password_hash
role
station_id
```

Role:

```text
YARD_MASTER
```

## stations

```text
id
name
code
city
```

## devices

```text
id
device_id          -- unique, e.g. ENTRY-01
station_id         -- FK stations
direction          -- ENTRY | EXIT
device_key_hash
last_seen_at
enabled
```

Constraints and rules:

- `UNIQUE (device_id)`
- index on `(station_id, direction)`
- `direction` is `ENTRY` or `EXIT`; it is authoritative for all events from this device
- a device belongs to exactly one station

## rolling_stock

```text
id
rfid_uid
identifier
type
home_shed (optional)
active
```

Do not store movement-specific origin/destination on an individual coach/wagon.

Constraints:

- `UNIQUE (rfid_uid)`
- `UNIQUE (identifier)`
- `type` is `LOCO`, `COACH`, or `WAGON`
- `active = false` hides retired stock from new validation but keeps history intact

## movement_plans

```text
id
station_id
locomotive_id
direction
expected_coaches
expected_wagons
expected_locos
origin
destination
expected_start_time
status
created_at
```

Plan status values:

```text
SCHEDULED
ACTIVE
COMPLETED
CANCELLED
```

Rules:

- a plan belongs to one station and one direction;
- `locomotive_id` references `rolling_stock.identifier` (the business identifier, not the database id);
- only `SCHEDULED` and `ACTIVE` plans are candidates for matching;
- a plan may authorize at most one movement (see the partial unique index in §16).

## movements

```text
id
station_id
direction
device_id
plan_id (nullable)
locomotive_id (nullable)
started_at
ended_at (nullable)
last_evidence_at
state                    -- OPEN | CLOSED
validation_result        -- NULL while OPEN, then VALID | FLAGGED | UNVERIFIED
physical_passage_count
rfid_observation_count
segmentation_threshold_seconds
created_at
```

Rules:

- `state` describes the lifecycle; `validation_result` describes the outcome. Do not mix them in one column.
- `validation_result` is `NULL` until the movement closes.
- `last_evidence_at` is updated by every assigned event and drives closure.
- `locomotive_id` stores the business identifier (for example `LOCO-1847`), consistent with `movement_plans`; resolve it through `rolling_stock.identifier`.
- Partial unique index: at most one `OPEN` movement per `(station_id, direction, device_id)`.
- Partial unique index: a non-null `plan_id` may appear on at most one movement.
- Index on `(station_id, started_at DESC)` for history queries.

## events

```text
id
event_id
device_id
movement_id (nullable)
event_type
rfid_uid (nullable)
device_timestamp
received_at
sequence
metadata
```

Constraints and indexes:

- `UNIQUE (event_id)`
- index on `(device_id, sequence)` — not unique, because the counter may reset on reboot
- index on `(movement_id, received_at)`
- index on `(device_id, received_at DESC)`
- `event_type` is `RFID_DETECTED` or `PHYSICAL_PASSAGE` for the prototype
- `rfid_uid` is required for `RFID_DETECTED` and null for `PHYSICAL_PASSAGE`
- `metadata` is JSON (SQLite `TEXT`), used for passage duration, distance readings, and diagnostics

## movement_observations

```text
id
movement_id
rolling_stock_id (nullable)
rfid_uid
event_id                  -- FK events, the RFID_DETECTED event
paired_passage_event_id   -- nullable FK events, the matched PHYSICAL_PASSAGE
observed_at
evidence_status
```

Possible evidence status:

```text
VERIFIED     -- known UID, paired with a physical passage
UNVERIFIED   -- known UID, but no paired passage
UNKNOWN      -- UID not present in rolling_stock
```

Constraints and rules:

- `UNIQUE (event_id)` — one observation row per RFID event, so pairing is idempotent
- index on `(movement_id, observed_at)`
- `rolling_stock_id` is the database id; `rfid_uid` is retained so unknown tags remain traceable
- a passage event is paired to at most one observation (enforce in the pairing service and, where possible, with a partial unique index on `paired_passage_event_id`)
- an unknown UID is still stored; it is evidence, not an error

## movement_findings

Stores the reason behind every validation result, so the dashboard can explain a flag without re-deriving it.

```text
id
movement_id
check            -- RFID_EXISTS | PLAN_MATCH | TYPE_VALID | COMPOSITION |
                 -- PHYSICAL_VS_RFID | DUPLICATE | DEVICE | EVENT_RATE
outcome          -- PASS | FAIL | WARN | SKIP
detail           -- JSON or human-readable text
created_at
```

Rules:

- index on `(movement_id, check)`
- one finding per check per validation run; re-validation replaces the previous findings for that movement
- `detail` must contain the numbers that produced the outcome, for example `{"expected": 4, "observed": 3}`

## inventory

The register of what is currently inside the monitored yard.

```text
id
station_id
rolling_stock_id (nullable)
rfid_uid
presence          -- IN_YARD | OUT
status            -- VERIFIED | UNVERIFIED
since             -- when the current presence began
last_movement_id
```

Constraints and rules:

- `UNIQUE (station_id, rfid_uid)` — one row per physical tag per station
- `rolling_stock_id` is nullable so an unknown tag can still appear as an unidentified unit
- an unknown tag is stored with `status = UNVERIFIED`
- inventory rows are never deleted on exit; `presence` becomes `OUT` so history is preserved
- `since` is updated only when `presence` actually changes

## alerts

```text
id
movement_id (nullable)
type
severity
message
created_at
resolved_at (nullable)
resolved_by (nullable)
resolution_note (nullable)
```

Alert types:

```text
UNKNOWN_RFID
UNAUTHORIZED_MOVEMENT
UNPLANNED_MOVEMENT
COUNT_MISMATCH
DUPLICATE_OBSERVATION
PHYSICAL_WITHOUT_RFID
RFID_WITHOUT_PHYSICAL
UNREGISTERED_DEVICE
IMPLAUSIBLE_EVENT_RATE
UNEXPECTED_EXIT
MISSING_EXPECTED_ROLLING_STOCK
```

Suggested severity:

| Alert | Severity |
|---|---|
| `UNAUTHORIZED_MOVEMENT` | HIGH |
| `UNPLANNED_MOVEMENT` | HIGH |
| `UNEXPECTED_EXIT` | HIGH |
| `UNKNOWN_RFID` | MEDIUM |
| `COUNT_MISMATCH` | MEDIUM |
| `PHYSICAL_WITHOUT_RFID` | MEDIUM |
| `RFID_WITHOUT_PHYSICAL` | MEDIUM |
| `MISSING_EXPECTED_ROLLING_STOCK` | MEDIUM |
| `DUPLICATE_OBSERVATION` | LOW |
| `IMPLAUSIBLE_EVENT_RATE` | LOW |
| `UNREGISTERED_DEVICE` | LOW |

Deduplication:

- `UNIQUE (movement_id, type)` for movement-scoped alerts.
- SQLite treats `NULL` values as distinct, so movement-less alerts need a partial unique index or a sentinel `movement_id`; do not rely on a plain unique constraint.
- Re-validation must not create a second copy of an existing unresolved alert.
- If a later validation pass resolves the cause, close the alert automatically and record the reason in `resolution_note`.

---

# 16. Movement Plans and Authorization

A movement plan represents expected movement information, like a delivery note. Plans may be created through seed data or a protected backend/admin API.

## 16.1 Matching Algorithm

A plan is matched deterministically:

1. **Candidates:** same `station_id`, same `direction`, `status IN (SCHEDULED, ACTIVE)`, and `expected_start_time` within `plan_match_window_minutes` of the movement's `started_at` (default 60 minutes).
2. **Bind on locomotive identification:** when an `RFID_DETECTED` observation resolves to a `LOCO`, select the candidate whose `locomotive_id` equals the observed `identifier`.
3. **Ambiguity:** if more than one candidate matches, choose the one with the nearest `expected_start_time`; record the ambiguity in a `PLAN_MATCH` finding with `outcome = WARN`.
4. **No locomotive observed:** leave `plan_id` null and keep the movement as unplanned. Do not guess from coach counts.
5. **No candidate:** leave `plan_id` null and raise `UNPLANNED_MOVEMENT`.
6. **Wrong locomotive:** if a candidate exists for the station/direction/time window but its `locomotive_id` differs from the observed one, raise `UNAUTHORIZED_MOVEMENT`.
7. **Prevent double binding:** the partial unique index on `movements.plan_id` guarantees that one plan cannot authorize two movements.
8. **Mark the plan used:** set the plan to `ACTIVE` when bound, and to `COMPLETED` when the movement closes.

## 16.2 Retroactive Revalidation

RFID evidence can arrive out of order. For example, coaches may be observed before the locomotive's tag is read. The movement may therefore be created as unplanned and later match a plan.

When `plan_id` changes from null to a plan:

- re-run the full validation;
- replace the previous findings;
- re-run the inventory update so verification statuses reflect the new result;
- auto-resolve plan-dependent alerts (`UNPLANNED_MOVEMENT`, `UNAUTHORIZED_MOVEMENT`) that no longer apply, with `resolution_note = "plan matched after later evidence"`;
- keep all raw events and observations unchanged.

A movement without a matching plan is still recorded as physical evidence. It is marked:

```text
UNPLANNED / FLAGGED
```

The system must never silently treat an unplanned movement as authorized.

## 16.3 Plan Validation

A plan is valid when:

- `expected_locos`, `expected_coaches`, `expected_wagons` are non-negative integers;
- `expected_start_time` is a timezone-aware timestamp stored in UTC;
- the referenced `locomotive_id` exists in `rolling_stock` and has `type = LOCO`.

Invalid plans are rejected at creation time, not at movement time.

---

# 17. Validation Logic

Validation runs when a movement closes. It is re-run if the plan binding changes later (§16.2).

## 17.1 Evidence Pairing (the core algorithm)

The two evidence streams arrive independently, and either may arrive first. Pairing decides which RFID observation belongs to which physical passage.

Inputs: all `PHYSICAL_PASSAGE` events and all `RFID_DETECTED` observations assigned to the movement.

Algorithm:

1. Build every candidate pair `(observation, passage)` whose time difference is within `pairing_window_seconds` (default 2.0 s). Use the passage's **entry** timestamp (§7.4) and the RFID read's device timestamp. Both events come from the same device, so the comparison is valid even though the absolute clock is unreliable.
2. Sort candidate pairs by absolute time difference, ascending.
3. Walk the sorted list and accept a pair only if **both** sides are still unpaired. This makes pairing strictly one-to-one.
4. Break exact ties by lower event `id`, so the result is deterministic.
5. Record the accepted link in `movement_observations.paired_passage_event_id`.

Consequences:

- an observation with no accepted pair has `evidence_status = UNVERIFIED` (if the UID is known) and produces `RFID_WITHOUT_PHYSICAL`;
- an observation whose UID is not in `rolling_stock` has `evidence_status = UNKNOWN` regardless of pairing, and also produces `UNKNOWN_RFID`;
- a passage with no accepted observation produces `PHYSICAL_WITHOUT_RFID`;
- pairing never crosses movement boundaries;
- arrival order must not change the result;
- re-running pairing on the same data must produce the same links (idempotent).

The dashboard may show a best-effort pairing while the movement is `OPEN`, but the authoritative pairing is computed at closure.

## 17.2 Counting Rules

These rules must be applied consistently, because "count" means different things for different checks.

| Quantity | Definition |
|---|---|
| `physical_passage_count` | Number of `PHYSICAL_PASSAGE` events in the movement |
| `rfid_observation_count` | Number of `RFID_DETECTED` events in the movement |
| `unique_rfid_count` | Distinct `rfid_uid` values among those events |
| observed composition | Distinct **known** units grouped by `type` |

Rules:

- A repeated read of the same UID counts as one unit for composition, but still raises `DUPLICATE_OBSERVATION` and remains stored as a separate event.
- An unknown UID counts as physical evidence but cannot be assigned a type. It is reported separately as "unidentified" and never silently added to a coach/wagon count.
- Expected-vs-observed composition compares distinct known units, not event counts.
- `COUNT_MISMATCH` is raised when observed known units differ from the plan, or when an unidentified unit is present in a planned movement.
- `PHYSICAL_WITHOUT_RFID` is raised when the number of unpaired passages is greater than zero. It is not raised merely because counts differ.

## 17.3 Individual Checks

The backend checks:

### 1. RFID exists

Unknown UID:

```text
UNKNOWN_RFID
```

### 2. Movement authorization

No matching plan:

```text
UNPLANNED_MOVEMENT
```

Conflict with expected plan:

```text
UNAUTHORIZED_MOVEMENT
```

### 3. Rolling-stock type

Must be:

```text
LOCO | COACH | WAGON
```

### 4. Expected vs observed composition

Example:

```text
expected: 1 LOCO, 8 COACH
observed: 1 LOCO, 7 COACH
```

Result:

```text
COUNT_MISMATCH
```

### 5. Physical vs RFID evidence

After pairing (§17.1), compare the unpaired counts:

```text
unpaired passages = 1
unpaired observations = 0
```

That is one unit that physically passed with no identification, so raise `PHYSICAL_WITHOUT_RFID`.

A raw difference in totals is not sufficient evidence on its own; use the pairing result. This is why pairing must be deterministic and stored.

### 6. Duplicate RFID within movement

Unexpected repeated UID:

```text
DUPLICATE_OBSERVATION
```

### 7. RFID without physical evidence

```text
RFID_WITHOUT_PHYSICAL
```

### 8. Physical passage without RFID

```text
PHYSICAL_WITHOUT_RFID
```

### 9. Device validity

Unknown/disabled device:

```text
UNREGISTERED_DEVICE
```

### 10. Implausible event rate

Flag suspicious bursts caused by sensor faults, firmware bugs, retries, or test-data errors.

---

# 18. Validation Status

Lifecycle and outcome are separate fields.

## 18.1 Lifecycle

```text
state = OPEN | CLOSED
```

## 18.2 Outcome

```text
validation_result = VALID | FLAGGED | UNVERIFIED
```

`validation_result` is `NULL` while the movement is `OPEN`.

Decision procedure at closure:

1. If **no unit could be identified** (zero known RFID observations) → `UNVERIFIED`.
2. Else if any check produces `FAIL` → `FLAGGED`.
3. Else → `VALID`.

`UNVERIFIED` therefore takes precedence over `FLAGGED` when identity is entirely missing, but the alerts are still raised. For example, a movement with four passages and no RFID is `UNVERIFIED` **and** carries `PHYSICAL_WITHOUT_RFID`.

`UNVERIFIED` applies when, for example:

- passages exist but no RFID was ever read;
- the only identifications are unknown tags;
- the movement closed with zero evidence of one of the two kinds.

`UNVERIFIED` is not a failure. It means "we saw something and cannot confirm what it was."

## 18.3 Reasons

Every outcome must retain the findings that produced it in `movement_findings` (§15). The dashboard reads those findings; it must not recompute validation.

Example:

```text
state = CLOSED
validation_result = FLAGGED

findings:
PLAN_MATCH            PASS   plan_id = 12
COMPOSITION           FAIL   expected 1 LOCO + 8 COACH, observed 1 LOCO + 7 COACH
PHYSICAL_VS_RFID      FAIL   unpaired passages = 1
DUPLICATE             PASS
```

Contradictory evidence must never be silently marked valid. Findings are replaced atomically on re-validation, so a stale reason can never survive a later pass.

---

# 19. Inventory Semantics

The dashboard represents rolling stock currently considered inside the station's monitored yard. Inventory is the register described in §15.

## 19.1 Derivation

Inventory is updated **when a movement closes**, after validation and pairing.

Only observations with a **paired physical passage** update presence. An RFID read with no passage is evidence of a tag, not proof that the unit entered or left, so it does not move inventory.

```text
paired observation + ENTRY movement  -> presence = IN_YARD
paired observation + EXIT movement   -> presence = OUT
```

Rules:

- Update by `(station_id, rfid_uid)`, not by display name.
- `status = VERIFIED` when the UID is known **and** the movement is `VALID`.
- `status = UNVERIFIED` when the UID is unknown, or the movement is `FLAGGED` or `UNVERIFIED`.
- A flagged movement still updates inventory. Physical evidence is not discarded because a count was wrong.
- An unknown UID creates an inventory row with `rolling_stock_id = NULL`, `status = UNVERIFIED`, and is displayed as "Unidentified unit (UID …)".
- A passage with **no RFID at all** has no identity and cannot create an inventory row. It is reported as an unpaired passage / `PHYSICAL_WITHOUT_RFID`, so the missing unit is visible without inventing a fake tag.
- If the same unit is observed twice in one movement, update it once; the duplicate is an alert, not a second inventory item.
- `since` changes only when `presence` changes.

## 19.2 Anomalies

- **EXIT of a unit that is not `IN_YARD`:** still record `presence = OUT`, raise `UNEXPECTED_EXIT`, and mark it `UNVERIFIED`.
- **ENTRY of a unit already `IN_YARD`:** keep `IN_YARD`, raise `DUPLICATE_OBSERVATION`, and keep the earliest `since`.
- **Planned unit never observed during an ENTRY:** raise `MISSING_EXPECTED_ROLLING_STOCK`.
- **Movement flagged for count mismatch:** the units that *were* observed still enter inventory, but they are visible with their verification status.

## 19.3 Display

Example:

```text
COACH-001
presence = IN_YARD
status = VERIFIED

COACH-009
presence = IN_YARD
status = UNVERIFIED

Unidentified unit (UID 7F:32:AA:91)
presence = IN_YARD
status = UNVERIFIED
```

This distinguishes physically observed and validated stock from physically observed but disputed stock, and ensures nothing that physically entered disappears from the register.

Counts on the dashboard must show both totals and a breakdown, for example:

```text
Inside yard: 14 units
  13 verified
   1 unverified / unidentified
```

---

# 20. Example Valid Movement

```text
Movement #1001

Station:
    Mumbai Central

Direction:
    ENTRY

Started:
    09:30:01

Ended:
    09:30:47

Lead locomotive:
    LOCO-1847

RFID observations:
    LOCO-1847
    COACH-001
    COACH-002
    COACH-003

Physical passages:
    4

Expected:
    1 locomotive
    3 coaches

Result:
    VALID
```

---

# 21. Example Discrepancy

```text
Movement #1002

Direction:
    ENTRY

Expected:
    1 LOCO
    8 COACH

Observed:
    1 LOCO
    7 COACH

Physical passages:
    8

RFID observations:
    7

Result:
    FLAGGED

Findings:
    COMPOSITION        FAIL   expected 1 LOCO + 8 COACH, observed 1 LOCO + 7 COACH
    PHYSICAL_VS_RFID   FAIL   unpaired passages = 1

Alerts:
    COUNT_MISMATCH
    PHYSICAL_WITHOUT_RFID
```

All raw evidence remains stored.

---

# 22. API Design

## 22.1 Device Ingestion

```text
POST /api/ingest/events        X-Device-Key
```

Accepts a single event, or a small batch of events, in the format of §11. Duplicate `event_id` returns `200` with the original result.

## 22.2 Yard-Master API

```text
POST /api/auth/login
POST /api/auth/logout

GET  /api/station
GET  /api/station/inventory
GET  /api/station/active-movements

GET  /api/movements
GET  /api/movements/{movement_id}

GET  /api/alerts
GET  /api/alerts/{alert_id}
POST /api/alerts/{alert_id}/resolve

GET  /api/rolling-stock
```

Rules:

- all yard-master endpoints are scoped to the authenticated user's `station_id`;
- list endpoints support `limit`/`offset` and at least `from`/`to` date filters;
- movement and alert lists support a `status` filter;
- `POST /api/alerts/{id}/resolve` sets `resolved_at`, `resolved_by`, and `resolution_note`;
- responses never include `device_key_hash` or password hashes.

## 22.3 Administration (protected)

```text
POST   /api/admin/plans
GET    /api/admin/plans
PATCH  /api/admin/plans/{id}
POST   /api/admin/devices
PATCH  /api/admin/devices/{id}
POST   /api/admin/rolling-stock
```

Administration may be seed-data-only for the prototype, but the endpoints or scripts must exist so the demonstration can create plans without editing the database by hand.

## 22.4 Development-Only

```text
POST /api/dev/simulate
POST /api/dev/reset
```

These endpoints must be disabled unless `ENV=development`, and must require a separate development token.

## 22.5 Operational

```text
GET /health
```

Returns database connectivity and the configured `movement_gap_seconds`.

---

# 23. Development Event Simulation

Backend development must not depend on the ESP32 being ready.

The simulator must generate **the same event format** as a real device, including `event_id`, `sequence`, `device_id`, `timestamp`, and passage `metadata`. It should support:

```text
RFID_DETECTED
PHYSICAL_PASSAGE
```

Required scenarios, each runnable in one command:

| Scenario | Events produced | Expected result |
|---|---|---|
| Valid entry | 5 passages, 5 RFID (1 LOCO + 4 COACH) | `VALID`, inventory +5 |
| Unknown tag | 3 passages, 3 RFID, one unknown UID | `FLAGGED`, `UNKNOWN_RFID` |
| Count mismatch | 4 passages, 4 RFID, two units sharing one UID | `FLAGGED`, `COUNT_MISMATCH` + `DUPLICATE_OBSERVATION` |
| Physical without RFID | 4 passages, 3 RFID (one untagged unit) | `FLAGGED`, `PHYSICAL_WITHOUT_RFID` (+ `COUNT_MISMATCH` if planned) |
| RFID without physical | 4 passages, 5 RFID | `FLAGGED`, `RFID_WITHOUT_PHYSICAL` |
| Unplanned movement | 4 passages, 4 RFID, no plan | `FLAGGED`, `UNPLANNED_MOVEMENT` |
| Two movements | 4 passages, 25 s pause, 4 passages | two movements |
| Duplicate retry | same `event_id` twice | one event, `200`, no double count |
| Unexpected exit | EXIT for stock not in inventory | `FLAGGED`, `UNEXPECTED_EXIT` |

Requirements:

- timestamps must be controllable so the 20-second rule can be tested without sleeping;
- the simulator must not bypass ingestion or validation — it must post through the same endpoint and code path;
- `POST /api/dev/reset` clears data for repeatable demos;
- the frontend can be built entirely against these scenarios.

---

# 24. Dashboard

The yard-master dashboard should provide:

## Station Overview
- station name
- current rolling-stock count, broken down into verified / unverified / unidentified
- active movements
- today's entries
- today's exits
- unresolved alerts

## Current Rolling Stock
- identifier (or "Unidentified unit" plus UID)
- type (or "Unknown" when the UID is unresolved)
- presence
- last movement
- last entry/exit time
- verification status

## Movement History
- movement ID
- direction
- locomotive
- start/end time
- observed counts
- validation result

## Movement Details
- expected consist
- observed consist
- physical passage count
- RFID observations
- authorization
- discrepancies
- alerts
- final status

## Alerts
- alert type
- severity
- movement
- timestamp
- description
- resolution status

The frontend consumes backend APIs and does not duplicate business logic.

---

# 25. ESP32 Firmware Responsibilities

Implement:
1. Wi-Fi
2. RC522 initialization
3. RFID reading
4. RFID cooldown
5. ultrasonic reading
6. ultrasonic filtering
7. physical-passage state machine
8. event generation
9. event ID generation
10. sequence numbering
11. HTTP transmission
12. retry handling
13. device authentication
14. basic diagnostics

Additional firmware rules:

- A `PHYSICAL_PASSAGE` is timestamped at the moment the object **entered** the detection zone, and emitted when the zone becomes clear.
- The event carries `started_at`, `ended_at`, `duration_ms`, and the min/peak distance in `metadata`.
- `event_id` must be stable across retries. Generate it once per logical event and reuse it for every retry.
- `sequence` must not reset while events are still queued; persist the counter if flash wear is acceptable, otherwise restart it at boot and let the backend treat a reset as a new session.
- A small in-memory retry queue is sufficient for the prototype; document its maximum size and the behaviour when it overflows (oldest dropped, warning logged).
- The firmware must not compute counts, validity, authorization, inventory, or movement boundaries.

Do not implement the 20-second movement segmentation or business validation on ESP32.

---

# 26. Event Flow

```text
Rolling stock approaches
        |
        v
Ultrasonic detects object
        |
        +------------------+
        |                  |
        v                  v
physical event       RFID detected
        |                  |
        +--------+---------+
                 |
                 v
               ESP32
                 |
               Wi-Fi
                 |
                 v
              Backend
                 |
                 v
           resolve RFID UID
                 |
                 v
          attach to movement
                 |
                 v
              validate
              /                 valid    discrepancy
             |          |
             v          v
         inventory    alert
             |
             v
          dashboard
```

---

# 27. Failure Handling

### Wi-Fi failure
Retry transmission. Queue events locally while disconnected.

### Backend unavailable
Do not discard important events. Use a bounded retry queue and replay it in `sequence` order when connectivity returns.

Queue rules:

- reuse the original `event_id` on every retry, so the backend can deduplicate;
- preserve event order within the queue;
- cap the queue (for example, 50 events) and log dropped events explicitly;
- never block the sensing loop while retrying; sensing continues and new events are queued behind the old ones;
- if the queue overflows, the demo should show missing `sequence` numbers and the backend should raise `IMPLAUSIBLE_EVENT_RATE` or record a sequence gap.

### RFID unavailable
Physical passage is still recorded:

```text
PHYSICAL_WITHOUT_RFID
```

### Ultrasonic unavailable
RFID can still be recorded:

```text
RFID_WITHOUT_PHYSICAL
```

This is a key benefit of preserving two independent evidence sources.

---

# 28. Testing Requirements

## Backend tests

Test:
- event ingestion
- duplicate `event_id` returns `200` and does not double count
- duplicate `(device_id, sequence)`
- movement creation
- 20-second segmentation
- automatic movement closure via sweep
- automatic movement closure via lazy read
- entry and exit
- direction taken from the device record, not the payload
- RFID resolution
- unknown RFID
- count mismatch
- physical-without-RFID
- RFID-without-physical
- pairing window boundaries (just inside / just outside 2 s)
- pairing is one-to-one and order-independent
- duplicate observation within one movement
- unplanned movement
- retroactive plan binding resolves `UNPLANNED_MOVEMENT`
- one plan cannot bind to two movements
- unexpected exit
- alert deduplication
- inventory updates
- unknown UID appears in inventory
- inventory `since` only changes when presence changes
- re-validation replaces findings and is idempotent

## Firmware tests

Test:
- RFID read
- repeated RFID cooldown
- ultrasonic object detection
- gap detection
- physical-passage generation
- Wi-Fi reconnection
- retry behavior

## Physical prototype tests

Test the actual paper/cardboard gate with:

- 20 consecutive passes with gaps: exactly 20 passages
- two units with no gap: one passage, documented as expected
- hand passing through the detection zone: must be prevented by the gate design
- tag at the edge of reader range: does it still read?
- unit with no tag: passage without RFID
- duplicate tag on two units: duplicate observation
- reflective/glossy paper: does it cause false passages?
- deliberate double-read while stationary in the field: cooldown suppresses it
- rapid insertion and withdrawal: still one passage per unit
- power-cycle in the middle of a batch: sequence and queue behaviour

The chosen sensor thresholds, slot dimensions, and tag position must be recorded in the demonstration notes.

---

# 29. Configurable Parameters

Backend:

```text
movement_gap_seconds = 20
pairing_window_seconds = 2.0
plan_match_window_minutes = 60
closure_sweep_seconds = 5
duplicate_observation_window_seconds = 60
```

Firmware:

```text
ultrasonic_detection_threshold_cm
ultrasonic_clear_threshold_cm
ultrasonic_filter_window
ultrasonic_min_object_ms
ultrasonic_min_clear_ms
rfid_cooldown_seconds = 3
device_retry_count
device_retry_delay_ms
event_queue_size
```

Rules:

- the backend owns every movement, pairing, plan, and inventory rule;
- firmware owns only sensor interpretation and transport behaviour;
- all values must come from configuration or environment, never from literals scattered through the code;
- `movement_gap_seconds` is stored on each movement as `segmentation_threshold_seconds` so a historical result remains explainable after the configuration changes.

---

# 30. Limitations

### RFID cloning
UID-based identification is not secure against cloning.

### RC522 range
RC522 is short-range and intended here for prototype demonstration. The tag must pass within a few centimetres of the reader.

### Ultrasonic geometry
Reliable passage counting depends on the gate geometry, sensor placement, and the operator leaving a visible gap between units. A continuous strip counts as one unit.

### Hand-operated demonstration
Units are passed by hand, so throughput, spacing, and speed are controlled by the operator. A hand entering the detection zone creates a false passage unless the slot is designed to prevent it. This is a demonstration constraint, not a claim about real railway operation.

### Not a real consist counter
The prototype does not claim to count the coaches of a coupled real train. It counts discrete units passing a tabletop gate, each carrying one tag.

### 20-second segmentation
The 20-second threshold is a project assumption, not a universal railway operating standard.

### Network
The prototype uses Wi-Fi rather than railway-grade communications.

### Safety
This is not a safety-critical railway control system.

---

# 31. Overall Architecture

```text
+------------------------------------------------+
|                 Railway Yard                   |
|                                                |
| ENTRY SENSOR UNIT          EXIT SENSOR UNIT    |
| ESP32                     ESP32                |
|  |                         |                   |
|  +-- RC522                 +-- RC522           |
|  +-- Ultrasonic            +-- Ultrasonic      |
|          |                         |            |
+----------|-------------------------|------------+
           |                         |
          Wi-Fi                     Wi-Fi
           |                         |
           +------------+------------+
                        |
                        v
              +-------------------+
              |      BACKEND      |
              |                   |
              | Auth              |
              | Device Auth       |
              | Event Ingestion   |
              | Movement Engine   |
              | Validation        |
              | Inventory         |
              | Alerts            |
              | API               |
              +---------+---------+
                        |
                        v
              +-------------------+
              |      DATABASE     |
              +---------+---------+
                        |
                        v
              +-------------------+
              |     DASHBOARD     |
              |    Yard Master    |
              +-------------------+
```

---

# 32. Core Design Principle

```text
SENSORS PROVIDE EVIDENCE.
BACKEND PROVIDES INTERPRETATION.
```

RFID says:

```text
"This UID was detected."
```

Ultrasonic says:

```text
"A physical passage occurred."
```

The backend determines:

```text
"These observations belong to movement #1001."
```

Pairing is explicit, not implied: each RFID observation is linked to at most one physical passage within a time window, and unmatched evidence becomes a finding rather than being ignored.

Then:

```text
"Movement #1001 matches / does not match the expected plan."
```

Then:

```text
"The observed rolling stock should be represented in inventory."
```

Finally:

```text
"There is / is not a discrepancy."
```

This separation keeps the system testable, understandable, and extensible.

---

# 33. Final Project Definition

The final prototype is an **IoT-based railway station yard entry/exit monitoring and auditing system** using:

```text
ESP32
+
RC522 RFID
+
Ultrasonic physical-passage sensing
+
Wi-Fi
+
Central backend
+
Database
+
Yard-master dashboard
```

The system observes rolling stock, correlates RFID identity with physical passage, constructs entry/exit movements, validates movements against expected information, maintains current station-yard inventory, and raises alerts when evidence is inconsistent.

It is an **audit and monitoring system, not a train-control system**.

The ultrasonic approach is retained because the prototype uses a tabletop gate with discrete paper units separated by a visible gap and passed by hand. Its reliability must be demonstrated through the physical tests in §28.

---

# 34. Demonstration Script

A complete demonstration can be run in about ten minutes. Seed the database with:

- one station: `Mumbai Central`;
- one yard-master user;
- two devices: `ENTRY-01` (ENTRY) and `EXIT-01` (EXIT);
- rolling stock: `LOCO-1847`, `COACH-001` … `COACH-004`;
- one plan: `LOCO-1847`, ENTRY, expected `1 LOCO + 4 COACH`.

Operating notes for every scene:

- pass units less than 20 seconds apart, or the backend will start a new movement (that is intentional in Scene 5);
- keep the hand outside the slot and pass one unit at a time;
- keep the reader and the ultrasonic beam a few centimetres apart so each tag read pairs with its passage.

## Scene 1 — Valid entry

Pass `LOCO-1847`, `COACH-001`, `COACH-002`, `COACH-003`, `COACH-004` through the entry gate with gaps.

Say: *"Five physical passages, five RFID reads, all paired, all expected. The movement closes as VALID and five units appear in the yard inventory."*

## Scene 2 — Missing coach

Pass the same five units but leave `COACH-004` out.

Say: *"The plan expected four coaches; we observed three. The movement is FLAGGED with COUNT_MISMATCH, and the dashboard shows exactly which checks failed."*

## Scene 3 — Unit with no tag

Pass `COACH-002` and one untagged card through the entry gate.

Say: *"The sensor saw two passages but only one identity. The extra passage is PHYSICAL_WITHOUT_RFID. The movement is FLAGGED, and the dashboard shows an unpaired passage. It cannot be added to inventory because it has no identity at all."*

## Scene 4 — Unknown tag

Pass `COACH-003` and a card whose tag is not in the database through the entry gate.

Say: *"The tag was read, but it does not belong to any registered unit. UNKNOWN_RFID, and it still appears in the yard register as an unidentified unit so it cannot be ignored."*

## Scene 5 — Two movements

Pass four units, wait more than 20 seconds, then pass four more.

Say: *"The backend closed the first movement after 20 seconds of silence and opened a second one. Two movements, not one."*

## Scene 6 — Exit and unexpected exit

Pass `COACH-001` through the exit gate, then pass a unit that was never recorded as inside.

Say: *"Exit removes the unit from the yard. The second unit was never logged in, so UNEXPECTED_EXIT is raised."*

## Scene 7 — Offline retry

Disconnect the ESP32, pass two units, reconnect.

Say: *"The events were queued and replayed with the same event IDs. The backend deduplicated any retry, so nothing was counted twice."*

---

# 35. Implementation Order

Build in this order; do not start the next milestone until the previous one has tests.

| Milestone | Deliverable | Done when |
|---|---|---|
| **M0** | Project skeleton: FastAPI, SQLAlchemy, Alembic, pytest, config, health endpoint | `GET /health` passes; one migration applies |
| **M1** | Domain model + seed data: stations, users, devices, rolling stock, plans | seed script creates the demo data |
| **M2** | Event ingestion: device auth, validation, idempotency, storage | duplicate event returns `200`; bad key returns `401` |
| **M3** | Movement engine: active movement, 20-second rule, closure sweep, lazy closure | segmentation and closure tests pass |
| **M4** | Pairing + validation + findings + alerts | all §23 simulation scenarios pass as tests |
| **M5** | Inventory + station/movement/alert APIs | dashboard can be built from the API alone |
| **M6** | Frontend dashboard | all §24 views work against seeded data |
| **M7** | ESP32 firmware | one passage and one RFID read reach the backend; retry works |
| **M8** | Physical gate build + calibration + demo rehearsal | §28 physical tests pass; demo script runs clean twice |

The backend must be fully demonstrable with simulated events before M7 begins. Hardware is the last dependency, never the first.
