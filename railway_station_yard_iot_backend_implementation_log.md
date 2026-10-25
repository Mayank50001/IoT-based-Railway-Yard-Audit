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
| M2 — Ingestion | Authenticated single/batch event endpoint, UID canonicalisation, idempotency | Planned |
| M3 — Movement bucket | 20-second receive-time gap, lazy + sweep closure, idempotent close | Planned |
| M4 — Validation | Direct/fallback plan match, distinct type counts, ultrasonic warning, findings | Planned |
| M5 — Inventory & APIs | RFID-primary inventory, dashboard/history/alert endpoints | Planned |
| M6 — Simulator | v4 acceptance scenarios through the real ingestion path | Planned |
| M7 — Firmware | Final ESP32 `main.py` after the API contract is stable | Deferred |

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

The bench plan is bound to the two tags the user physically owns, so a real two-unit demonstration is possible today. The 4-coach plans are for the simulator. Plan status is intentionally **not** reset on re-seed, because a plan already used by a movement must keep its lifecycle. `--reset` deletes demo movements, inventory, and plans for NDLS only; master data and the lane key survive.

---

## 4. Decision register

| ID | Decision | Why | Rejected alternative |
|---|---|---|---|
| D-01 | Distinct registered UID/type counts are the primary validation evidence | RFID answers *which* units were seen; ultrasonic only answers *how many* interruptions occurred. Comparing those two questions at movement level is defensible without per-unit guessing. | One-to-one RFID↔ultrasonic pairing (v3); fails on missed reads and merged passages. |
| D-02 | No pairing table, no pairing window, no greedy/order matching | Pairing adds a correctness requirement the hardware cannot guarantee and hides the real uncertainty. | v3 `paired_passage_event_id` and 2-second window. |
| D-03 | Ultrasonic disagreement is a movement-level `WARN`, not a validation failure | A complete, otherwise valid RFID composition should not be failed by an advisory sensor. | Pairing-dependent validation. |
| D-04 | Plan lookup: lead-locomotive RFID first, unambiguous coach-to-plan assignment second | A coach can carry its plan context when the loco tag is missed, without inventing a loco read. | Requiring the loco read; or guessing among conflicting assignments. |
| D-05 | Expected/inferred locomotive data is stored separately from observed evidence | A database lookup is not a detection. Conflating them would create fake observations and fake inventory transitions. | Treating the expected loco as observed. |
| D-06 | One `OPEN` movement per `(station, direction, device)`, enforced by a partial unique constraint | Concurrency-safe invariant: two simultaneous ingestion requests cannot open two buckets. Declared in the database, not only in Python. | Application-only check (race window). |
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
| `InventoryRecord` | `yard_inventoryrecord` | Current yard presence per station + UID | Unique `(station, rfid_uid)`; `VERIFIED`/`UNVERIFIED` |
| `Alert` | `yard_alert` | Operator-facing alert raised from a finding | Unique `(movement, code, subject)`; acknowledgement fields |

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

---

## 7. Verification status of CP-02 and CP-02b

```bash
cd backend
python manage.py makemigrations yard
python manage.py migrate
python manage.py test yard backend
python manage.py check
```

Result: migrations `0001_initial` and `0002_lanemodechange_and_more` applied; 20 `yard` tests and the 3 pre-existing health tests pass; `check` reports no issues.

Then create a superuser and confirm the models appear in `/admin/`:

```bash
python manage.py createsuperuser
python manage.py runserver
```

---

## 8. Known gaps and open decisions

| Item | State |
|---|---|
| Plan reassignment after a wrong bind | Open. A plan may bind to only one movement; the yard master currently has no UI/endpoint to release it. |
| Firmware `event_id` format | Open. Must be stable across retries; the server accepts any string up to 64 characters. |
| API-key rotation | Open. Hashing is in place; rotation endpoint comes with ingestion. |
| `ALLOWED_HOSTS` for ESP32 on LAN | Configured via `DJANGO_ALLOWED_HOSTS`; the concrete LAN IP is not yet fixed. |
| Closure sweep runner | Movement closure needs a periodic trigger (management command + cron, or a small loop) in addition to lazy checks. |
| SQLite concurrency | Prototype-acceptable. Enable WAL if ingestion and the dashboard run together under load. |
| Device key verification cost | ~1.3 s per PBKDF2 check. CP-04 must authenticate once per request and cache successful verifications; otherwise batch ingestion becomes slow. |
| Scaffold/version drift | Settings header says Django 4.2.16; the environment actually runs 6.1.2. Code now targets the installed version. Re-check on any environment rebuild. |
| UID length variety | Canonical form accepts 4–32 hex characters (2–16 bytes) in whole bytes; RC522 4-byte tags are the current case. |
| Revalidation of a closed movement | Policy defined (replace findings atomically, never repeat inventory transitions); implementation still to come. |
| Inventory-inferred direction | **Rejected by decision D-19.** Never implemented. Only a `DIRECTION_CONFLICT` warning will consult inventory state. |
| Lane mode unset | Ingestion will refuse events with HTTP 409 `LANE_MODE_NOT_SET`; the firmware queue must retain and retry them. Not yet implemented. |
| Mode change during a backlog drain | A late batch can be ingested after the operator changed the lane mode, giving it the new direction. Same class of problem as delayed replay; mitigate by closing the movement before switching modes. |
| Operator ergonomics | Setting the mode is currently an admin action. A dedicated dashboard control/API belongs in CP-07. |

---

## 9. Next checkpoints

1. **CP-04 — ingestion:** authenticated single/batch endpoint with transactional dedup, UID resolution, and `LANE_MODE_NOT_SET` handling.
2. **CP-05 — movement buckets:** 20-second receive-time segmentation and idempotent closure.
3. **CP-06 — validation:** plan direct/fallback matching, count checks, ultrasonic warning, `DIRECTION_CONFLICT`, findings.
4. **CP-07 — inventory & APIs:** presence updates and station-scoped read APIs, including the operator lane-mode control.
5. **CP-08 — simulator:** acceptance scenarios through the real ingestion path.
6. **CP-09 — firmware:** final ESP32 `main.py` after the contract is stable.
