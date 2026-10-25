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
| M1 — Domain models | Stations, staff, devices, rolling stock, plans, assignments, movements, events, findings, inventory, alerts | **DELIVERED** (CP-02, pending apply) |
| M1b — Seed data | Repeatable demo station, devices, fleet, coaches assigned to a plan | Planned (CP-03) |
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

---

## 5. Data model map (CP-02)

| Model | Table | Purpose | Key rules |
|---|---|---|---|
| `Station` | `yard_station` | Station master data; optional division/zone | `code` unique |
| `StaffProfile` | `yard_staffprofile` | Links a Django user to a station and role | One profile per user |
| `Device` | `yard_device` | Registered gate reader (ESP32) | `device_id` unique; hashed API key; `direction` fixed per device |
| `RollingStock` | `yard_rollingstock` | Fleet master data: loco/coach/wagon | `rfid_uid` canonical + unique; `number` unique |
| `MovementPlan` | `yard_movementplan` | Expected movement: lead loco + counts + window | `plan_code` unique; at least one expected unit |
| `PlanAssignment` | `yard_planassignment` | Current coach/wagon/loco ↔ plan context | Unique `(plan, rolling_stock)` |
| `Movement` | `yard_movement` | One passage of a consist through one lane | One `OPEN` per lane; one movement per plan; outcome separate from state |
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

---

## 7. How CP-02 is verified

```bash
cd backend
python manage.py makemigrations yard
python manage.py migrate
python manage.py test yard
python manage.py check
```

Expected: migration `0001_initial` created and applied; all `yard` tests pass; `check` reports no issues.

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
| UID length variety | Canonical form accepts 4–32 hex characters (2–16 bytes) in whole bytes; RC522 4-byte tags are the current case. |
| Revalidation of a closed movement | Policy defined (replace findings atomically, never repeat inventory transitions); implementation still to come. |
| Inventory-inferred direction | Explicitly **not** implemented. Registered device direction remains authoritative. |

---

## 9. Next checkpoints

1. **CP-03 — seed data:** repeatable management command creating a demo station, one entry and one exit device, a fleet of locos/coaches/wagons, a plan expecting 1 loco + 4 coaches, and four distinct coaches assigned to that plan.
2. **CP-04 — ingestion:** authenticated single/batch endpoint with transactional dedup and UID resolution.
3. **CP-05 — movement buckets:** 20-second receive-time segmentation and idempotent closure.
4. **CP-06 — validation:** plan direct/fallback matching, count checks, ultrasonic warning, findings.
5. **CP-07 — inventory & APIs:** presence updates and station-scoped read APIs.
6. **CP-08 — simulator:** acceptance scenarios through the real ingestion path.
