# IoT-Based Railway Station Yard Entry/Exit Inspection and Monitoring System
## Revised Project Context & Technical Specification — v2

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

The model tag should be mounted at a predictable position so it passes within the reader's usable range.

---

# 7. Ultrasonic Physical-Passage Detection

## 7.1 Role

The ultrasonic sensor is the physical-passage evidence source.

The prototype assumes a controlled model-track arrangement where adjacent rolling-stock units have a sufficiently visible gap.

The intended prototype assumption is approximately:

```text
gap between units >= 1.5 ft
```

With appropriate sensor placement, the ultrasonic sensor should distinguish:

```text
OBJECT -> GAP -> OBJECT -> GAP -> OBJECT
```

The project therefore retains ultrasonic sensing as the selected physical-passage technology.

## 7.2 Engineering Assumption

The project does **not** claim that ultrasonic will universally count every coach of every real railway train.

The prototype depends on:
- controlled sensor placement
- controlled model-track geometry
- sufficient inter-unit gap
- suitable sensor sampling/update rate
- appropriate distance thresholds
- experimentally validated detection behavior

The actual model setup must be tested before final demonstration.

## 7.3 Why the Gap Matters

The sensor should observe:

```text
COACH 1
   |
  GAP
   |
COACH 2
   |
  GAP
   |
COACH 3
```

The ESP32 should convert raw measurements into logical passage events rather than sending every raw distance reading to the backend.

## 7.4 Speed Consideration

Speed affects how long the gap remains visible.

For an approximately 0.46 m gap:

At 10 km/h:

```text
speed ≈ 2.78 m/s
gap visibility ≈ 0.46 / 2.78
               ≈ 0.165 s
```

At 35 km/h:

```text
speed ≈ 9.72 m/s
gap visibility ≈ 0.46 / 9.72
               ≈ 0.047 s
```

Therefore the chosen ultrasonic module and sampling logic must be experimentally verified at the intended model speed.

The 10–35 km/h range should be treated as a **prototype/design assumption**, not a universal railway rule unless separately verified from an authoritative railway source.

## 7.5 Ultrasonic State Detection

The ESP32 should use a small state machine:

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

A completed `OBJECT_PRESENT` interval becomes:

```text
PHYSICAL_PASSAGE
```

Distance thresholds should be configurable.

Filtering/debouncing should prevent noisy measurements from creating multiple passages.

---

# 8. RFID Repeated-Read Handling

A tag may remain in the RC522 field for multiple reads.

Without filtering, one unit could generate many identical detections.

Use a configurable same-UID cooldown, for example:

```text
3–5 seconds
```

This is only edge-side optimization. The backend must also deduplicate events because network retries can duplicate packets.

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

It should be configurable in the backend and not hardcoded into ESP32 firmware.

## 10.2 Movement Closure

A movement must close even if no future event arrives:

```text
movement starts
      |
last passage
      |
no more events
      |
20 seconds pass
      |
movement closes
```

This can be implemented with a small background task or lazy closure when another request/event arrives.

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
  "timestamp": "2026-10-21T09:30:08.120",
  "rfid_uid": "7F:32:AA:91",
  "sequence": 1284
}
```

The backend adds:

```text
received_at
```

The server receive time should be authoritative for backend ordering when device clocks are unreliable.

---

# 12. Event Idempotency and Ordering

Every event should contain:
- `event_id`
- `device_id`
- `sequence`
- device timestamp

`event_id` must be unique.

Retries must not create duplicate logical observations.

Therefore:

```text
event_id -> UNIQUE
```

The device sequence number helps diagnose ordering and missing events.

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
8. movement-plan validation
9. inventory
10. alerts
11. dashboard APIs
12. development event simulation

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
device_id
station_id
direction
device_key_hash
last_seen_at
enabled
```

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
```

## movements

```text
id
station_id
direction
device_id
plan_id (nullable)
locomotive_id (nullable)
started_at
ended_at
status
physical_passage_count
segmentation_threshold_seconds
```

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

## movement_observations

```text
id
movement_id
rolling_stock_id (nullable)
rfid_uid
observed_at
evidence_status
```

Possible evidence status:

```text
VERIFIED
UNVERIFIED
UNKNOWN
```

## alerts

```text
id
movement_id (nullable)
type
severity
message
created_at
resolved_at
```

Suggested alert types:

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
```

Alert generation should be deduplicated per movement/type.

---

# 16. Movement Plans and Authorization

A movement plan represents expected movement information.

Plans may be created through seed data or a protected backend/admin API.

A movement without a matching plan is still recorded as physical evidence.

It should be marked:

```text
UNPLANNED / FLAGGED
```

The system should never silently treat an unplanned movement as authorized.

---

# 17. Validation Logic

The backend should check:

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

Example:

```text
physical passages = 8
RFID observations = 7
```

This is a discrepancy.

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

Suggested movement statuses:

```text
ACTIVE
VALID
FLAGGED
UNVERIFIED
```

A flagged result should retain its reason.

Example:

```text
movement_status = FLAGGED

reason:
expected 8 coaches
observed 7 coaches
physical passages = 8
RFID observations = 7
```

Contradictory evidence must never be silently marked valid.

---

# 19. Inventory Semantics

The dashboard represents rolling stock currently considered inside the station's monitored yard.

Inventory is derived from entry/exit observations.

A flagged movement should not erase physical evidence.

Each inventory item should therefore carry a confidence/status concept.

Example:

```text
COACH-001
presence = IN_YARD
status = VERIFIED

COACH-009
presence = IN_YARD
status = UNVERIFIED
```

This distinguishes physically observed and validated stock from physically observed but disputed stock.

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

Alerts:
    COUNT_MISMATCH
    PHYSICAL_WITHOUT_RFID
```

All raw evidence remains stored.

---

# 22. API Design

Suggested endpoints:

```text
POST /api/auth/login

POST /api/ingest/events

GET /api/station

GET /api/station/inventory

GET /api/movements

GET /api/movements/{movement_id}

GET /api/alerts

GET /api/alerts/{alert_id}
```

Development-only:

```text
POST /api/dev/simulate
```

The simulation endpoint should be protected/disabled outside development.

---

# 23. Development Event Simulation

Backend development should not depend on the ESP32 being ready.

Simulation should support:

```text
RFID_DETECTED
PHYSICAL_PASSAGE
```

This allows movement, validation, inventory, alert, and frontend development before hardware integration is complete.

Simulated events should use the same format as real device events.

---

# 24. Dashboard

The yard-master dashboard should provide:

## Station Overview
- station name
- current rolling-stock count
- active movements
- today's entries
- today's exits
- unresolved alerts

## Current Rolling Stock
- identifier
- type
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
Retry transmission. If practical, temporarily queue events locally.

### Backend unavailable
Do not immediately discard important events. A small retry queue is preferred.

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
- duplicate events
- movement creation
- 20-second segmentation
- automatic movement closure
- entry and exit
- RFID resolution
- unknown RFID
- count mismatch
- physical-without-RFID
- RFID-without-physical
- unplanned movement
- alert deduplication
- inventory updates

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

Test the actual model rolling stock with:
- different speeds
- different object sizes
- different sensor distances
- different inter-unit gaps
- consecutive coaches
- locomotive followed by coach
- coach followed by wagon
- missing RFID tag
- duplicate RFID tag
- temporary RFID read failure

Ultrasonic parameters should be finalized from these experiments.

---

# 29. Configurable Parameters

```text
ultrasonic_detection_threshold
ultrasonic_clear_threshold
ultrasonic_filter_window
rfid_cooldown_seconds
movement_gap_seconds = 20
device_retry_count
device_retry_delay
```

The backend owns `movement_gap_seconds`.

Sensor-specific parameters can remain in firmware/configuration.

---

# 30. Limitations

### RFID cloning
UID-based identification is not secure against cloning.

### RC522 range
RC522 is short-range and intended here for prototype demonstration.

### Ultrasonic geometry
Reliable passage counting depends on physical prototype geometry and sensor placement.

### Speed and sampling
Higher speed reduces the duration of a visible gap, so the sensor sampling behavior must be experimentally validated.

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

The ultrasonic approach is retained because the prototype uses a controlled model-track arrangement with a sufficiently large gap between successive rolling-stock units. Its reliability must be demonstrated through physical testing at the intended model speed and geometry.
