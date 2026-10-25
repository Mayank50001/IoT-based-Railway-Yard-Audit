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
- Station and `ENTRY`/`EXIT` direction come from the authenticated device record, not from the request payload.
- This revision does **not** authorize changing to inventory-inferred, bidirectional operation. Existing registered direction remains the implementation baseline until that separate decision is made.
- Device API keys and yard-master authentication are separate. Dashboard requests are scoped to the user's station.
- Events are stored transactionally and deduplicated by globally unique `event_id`. Retrying an event returns HTTP 200 without counting or assigning it again.
- The ESP32 senses, filters, timestamps, queues, and transmits independent events. It does not decide plans, counts, inventory, validation, or movement boundaries.
- Preserve unfamiliar project files and follow `AGENTS.md`: code is provided in chat unless the human explicitly authorizes code-file edits; ask before accessing files outside the project.

Previous inventory state is useful for identifying unexpected exits or repeated entries. It must not silently turn an incoming observation into an exit, or vice versa.

## 3. Movement buckets and timing

There is at most one `OPEN` movement per `(station_id, direction, device_id)`.

1. **Any accepted event can start a movement**, including a coach RFID read or an ultrasonic passage. A locomotive read is not required to open the bucket.
2. Use server `received_at` for segmentation and update `last_evidence_at` on every assigned event.
3. An evidence gap **less than or equal to 20 seconds** stays in the same movement. A gap **greater than 20 seconds** starts a new one.
4. Close an idle movement through both a periodic sweep and lazy checks on reads/ingestion. Closing twice must have no additional effect.
5. At closure, resolve the plan, calculate distinct counts, store findings, and apply inventory changes once.

Device sensing timestamps and passage entry/exit metadata remain useful audit data. Passage timestamps continue to describe detection-zone entry, even though the event is emitted on exit. They are **not used for per-unit pairing** in v4.

Store timestamps in UTC and display IST on the dashboard. Do not label an unsynchronised clock as UTC. Delayed queue replay can still distort receive-time movement segmentation; this inherited prototype limitation is not solved by changing the counting policy.

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
- Retain audit events, event-id uniqueness, one open movement per device/direction/station, one movement per bound plan, station-scoped inventory uniqueness, and alert deduplication.

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
7. Exercise the acceptance scenarios through the same ingestion path with a simulator.
8. Provide final ESP32 firmware only after the API contract is stable and the missing wiring/clock/transport details have been confirmed.

Keep code delivery in chat unless explicit permission to edit code files is given. This documentation revision does not complete any backend implementation milestone.

## 12. Configuration

Backend defaults:

- `movement_gap_seconds = 20`;
- `closure_sweep_seconds = 5`;
- `plan_match_window_minutes = 60`;
- `validation_policy = RFID_PRIMARY_COUNTS_V4`.

Firmware retains configurable ultrasonic filtering, threshold/hysteresis, minimum occupied/clear intervals, RFID cooldown, and bounded queue/retry settings. A starting same-UID absence/cooldown is 3 seconds; refresh its last-seen time on every successful raw read.

There is **no required RFID-to-ultrasonic pairing window**. Maintain clear gaps for reliable interruption counts and keep evidence within the intended movement bucket; do not reintroduce per-unit correlation as a prerequisite for validation or inventory.
