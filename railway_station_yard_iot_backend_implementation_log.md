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
| M3 — Movement bucket | 20-second receive-time gap, idempotent closure, sweep command | **VERIFIED** for bucket mechanics (CP-04); closure finalisation waits on M4 |
| M4 — Validation | Direct/fallback plan match, distinct type counts, ultrasonic warning, findings | **VERIFIED** (2026-10-25, CP-06) |
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
| D-31 | `DIRECTION_CONFLICT` is only raised for `ENTRY`; exit anomalies use per-unit `UNEXPECTED_EXIT` | On an exit, "not recorded inside" is already a per-unit failure, so an additional aggregate warning would say the same thing twice. The v4 scenario that motivates `DIRECTION_CONFLICT` is stock already in the yard being "entered" again. | Raising `DIRECTION_CONFLICT` for both directions (double signalling). |
| D-32 | Inventory anomaly checks read the current inventory row as the pre-movement state | Correct today because CP-06 applies no inventory transitions. CP-07 must freeze the pre-movement state before applying its first transition, otherwise revalidation would compare against its own earlier update. | Guessing the pre-state from `last_movement` (ambiguous when a UID moves twice). |
| D-33 | Retired (`is_active = False`) stock is excluded from observed counts and reported as `RETIRED_ROLLING_STOCK` | A retired unit must not silently satisfy an expected count, but its tag being read is still evidence worth surfacing. | Counting retired stock normally; silently ignoring it. |

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

## 7. Verification status (current)

```bash
cd backend
python manage.py makemigrations --check --dry-run
python manage.py test yard backend
python manage.py check
```

Result: migrations `0001_initial` and `0002_lanemodechange_and_more` are the whole schema; `yard` (81 tests) plus the 3 pre-existing health tests all pass; `check` reports no issues and no migrations are pending.

Then create a superuser and confirm the models appear in `/admin/`:

```bash
python manage.py createsuperuser
python manage.py runserver
```

---

## 8. Known gaps and open decisions

| Item | State |
|---|---|
| Plan reassignment after a wrong bind | Open. A plan may bind to only one movement; the yard master currently has no UI/endpoint to release it. `Movement.plan` stays editable in admin as the manual workaround. |
| Firmware `event_id` format | **Closed (D-24):** `<device_id>-<boot_id>-<sequence>`. The firmware must persist `boot_id` alongside the queue so a retried event keeps its id. |
| API-key rotation | Hashing and cache invalidation are in place; `seed_demo --rotate-key` and the admin form both work. A dedicated dashboard action belongs in CP-07. |
| `ALLOWED_HOSTS` for ESP32 on LAN | Configured via `DJANGO_ALLOWED_HOSTS`; the concrete LAN IP is not yet fixed. |
| Closure sweep runner | `python manage.py sweep_movements` exists and is idempotent. A scheduler (cron, systemd timer, or a thread in the demo server) still has to run it. |
| SQLite concurrency | Prototype-acceptable. Enable WAL if ingestion and the dashboard run together under load. |
| Device key verification cost | Mitigated: one PBKDF2 check per key per 60 s, then cached (D-23). |
| Auth cache across processes | The default locmem cache is per process. With multiple workers, each pays one verification per key per minute; that is acceptable here, and a shared cache fixes it if workers multiply. |
| Scaffold/version drift | Settings header says Django 4.2.16; the environment actually runs 6.1.2. Code now targets the installed version. Re-check on any environment rebuild. |
| UID length variety | Canonical form accepts 4–32 hex characters (2–16 bytes) in whole bytes; RC522 4-byte tags are the current case. |
| Closed movements awaiting validation | **Closed:** `python manage.py revalidate_movements` validates every closed movement with `validated_at IS NULL` and replaces the `VALIDATION_PENDING` placeholder. Verified against a synthetic legacy movement. |
| Revalidation of a closed movement | **Implemented:** findings are upserted and pruned, alerts are created once, plan binding is stable, and the same events always produce the same outcome. Inventory transitions still have to respect this when CP-07 adds them (D-32). |
| Inventory-inferred direction | **Rejected by decision D-19.** Never implemented. Only a `DIRECTION_CONFLICT` warning will consult inventory state. |
| Lane mode unset | **Implemented:** HTTP 409 `LANE_MODE_NOT_SET`, nothing stored. The firmware queue must retain and retry. |
| Mode change during a backlog drain | A late batch can be ingested after the operator changed the lane mode, giving it the new direction. Same class of problem as delayed replay; mitigate by closing the movement before switching modes. |
| Operator ergonomics | Setting the mode is currently an admin action. A dedicated dashboard control/API belongs in CP-07. |
| Lazy closure on read | The sweep closes idle movements; ingestion also rotates a bucket immediately when a later event exceeds the gap. A dashboard read path still has no lazy-close hook (CP-07). |
| Validation outcome and alerts are stored, nothing reads them yet | Findings and alerts are written and idempotent, but only the admin site can display them. Read APIs are CP-07. |
| Inventory transitions | **Not implemented.** Validation reports `UNEXPECTED_EXIT` / `ENTRY_ALREADY_PRESENT` against current inventory rows, which are still the pre-movement state. CP-07 must freeze that state before applying its first transition (D-32). |
| Plan window vs rehearsal timing | Plans are matched within 60 minutes of `expected_start`, which `seed_demo` writes at seed time. Run `python manage.py seed_demo --reset` before a demo rehearsal. |
| Plan without a lead locomotive | Open by data quality, not by code: `expected_lead_loco` is nullable, so a plan that names no locomotive identity and has no locomotive assignment can only be count-checked. The seed always sets a lead. |
| Multi-locomotive demo data | No demo plan expects two locomotives yet, because the bench has one locomotive tag. The behaviour is covered by tests; CP-08's simulator will exercise it through the real ingestion path. |
| Banker/helper locomotive attached en route | **Deferred by the user (2026-10-25) — not a demo concern.** Current behaviour: a complete plan roster plus an unplanned banker yields `LOCOMOTIVE_MISMATCH` + `COUNT_MISMATCH`, i.e. `FLAGGED`. The open question is whether an extra locomotive should stay `FAIL` or become a `WARN`; flipping it is a one-line severity change plus one test. Revisit after the demo. |

---

## 9. Next checkpoints

1. **CP-07 — authentication:** Django-session login/logout/me for yard masters, global `IsAuthenticated` default, station scoping from `StaffProfile`, role checks, and `set_by` on lane-mode changes. Decisions taken with the user: Django-served dashboard (session cookie, not a token), and every account is station-bound with no administrator exception.
2. **CP-07 — inventory & APIs:** one RFID-primary transition per distinct UID actually read, station-scoped read APIs, and the operator lane-mode control.
3. **CP-08 — simulator:** acceptance scenarios through the real ingestion path.
4. **CP-09 — firmware:** final ESP32 `main.py` after the contract is stable.
