"""Tests for RFID-primary validation at movement closure.

Scenarios mirror the v4 acceptance table, plus the properties the policy
depends on: revalidation is idempotent, a bound plan is never reused, and
aggregate ultrasonic disagreement changes the warning but never the outcome.
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    Alert,
    Device,
    Direction,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementFinding,
    MovementPlan,
    PlanAssignment,
    RollingStock,
    Station,
)
from .validation import (
    COUNT_MISMATCH,
    DIRECTION_CONFLICT,
    DUPLICATE_OBSERVATION,
    ENTRY_ALREADY_PRESENT,
    LOCOMOTIVE_MISMATCH,
    MISSING_LOCOMOTIVE_RFID,
    PLAN_AMBIGUOUS,
    RETIRED_ROLLING_STOCK,
    UNEXPECTED_EXIT,
    UNKNOWN_RFID,
    UNPLANNED_MOVEMENT,
    ULTRASONIC_COUNT_MISMATCH,
    VALIDATION_PENDING,
    validate_movement,
)

T0 = datetime(2026, 10, 25, 9, 0, 0, tzinfo=dt_timezone.utc)

INELIGIBLE = 'INELIGIBLE'


class ValidationTestCase(TestCase):
    """PBKDF2 costs ~1.3 s per device key; tests do not need that strength."""

    def setUp(self):
        self._hashers = override_settings(
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']
        )
        self._hashers.enable()
        self.addCleanup(self._hashers.disable)

        self.station = Station.objects.create(code='NDLS', name='New Delhi')
        self.entry = self._device('gate-1', Direction.ENTRY)
        self.exit_lane = self._device('gate-2', Direction.EXIT)

        self.loco = self._stock('WDM3A-1001', 'FA37E63F', RollingStock.Kind.LOCO)
        self.other_loco = self._stock(
            'WAP7-3020', 'A0A00002', RollingStock.Kind.LOCO
        )
        self.third_loco = self._stock(
            'WAG9-5001', 'A0A00003', RollingStock.Kind.LOCO
        )
        self.coaches = [
            self._stock(
                f'NR-C-220{index}',
                f'C0C0000{index}',
                RollingStock.Kind.COACH,
            )
            for index in (1, 2, 3, 4)
        ]
        self.plan = self._plan(
            'NDLS-IN-FULL',
            Direction.ENTRY,
            lead=self.loco,
            coaches=4,
            assign=self.coaches,
        )
        self.counter = 0

    # --- fixtures ----------------------------------------------------------

    def _device(self, device_id, direction):
        device = Device(device_id=device_id, station=self.station, lane_mode=direction)
        device.set_api_key(f'key-{device_id}')
        device.save()
        LaneModeChange.objects.create(device=device, direction=direction)
        return device

    def _stock(self, number, uid, kind):
        return RollingStock.objects.create(
            number=number,
            rfid_uid=uid,
            kind=kind,
        )

    def _plan(
        self,
        code,
        direction,
        lead=None,
        locos=1,
        coaches=0,
        wagons=0,
        assign=(),
        expected_start=T0,
    ):
        plan = MovementPlan.objects.create(
            plan_code=code,
            station=self.station,
            direction=direction,
            expected_lead_loco=lead,
            expected_locos=locos,
            expected_coaches=coaches,
            expected_wagons=wagons,
            expected_start=expected_start,
        )
        for stock in assign:
            PlanAssignment.objects.create(plan=plan, rolling_stock=stock)
        return plan

    def _movement(self, direction=Direction.ENTRY, device=None, started_at=T0):
        return Movement.objects.create(
            station=self.station,
            device=device or (self.entry if direction == Direction.ENTRY else self.exit_lane),
            direction=direction,
            state=Movement.State.CLOSED,
            started_at=started_at,
            last_evidence_at=started_at,
            closed_at=started_at + timedelta(seconds=30),
        )

    def _next_event_id(self):
        self.counter += 1
        return f'gate-test-0000{self.counter:02d}'

    def _rfid(self, movement, uid, received_at=None):
        stock = RollingStock.objects.filter(rfid_uid=uid).first()
        return MovementEvent.objects.create(
            event_id=self._next_event_id(),
            movement=movement,
            device=movement.device,
            kind=MovementEvent.Kind.RFID_DETECTED,
            rfid_uid=uid,
            rolling_stock=stock,
            received_at=received_at or movement.started_at,
        )

    def _passage(self, movement, received_at=None):
        return MovementEvent.objects.create(
            event_id=self._next_event_id(),
            movement=movement,
            device=movement.device,
            kind=MovementEvent.Kind.PHYSICAL_PASSAGE,
            received_at=received_at or movement.started_at,
        )

    # --- assertions --------------------------------------------------------

    def findings(self, movement):
        return list(MovementFinding.objects.filter(movement=movement))

    def codes(self, movement):
        return {finding.code for finding in self.findings(movement)}

    def finding(self, movement, code):
        return MovementFinding.objects.get(movement=movement, code=code)

    def consolidated(self, movement):
        """Lead loco plus four coaches, all read once."""
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

    def inventory(self, uid, status=InventoryRecord.Status.IN_YARD):
        return InventoryRecord.objects.create(
            station=self.station,
            rfid_uid=uid,
            status=status,
        )


class HappyPathTests(ValidationTestCase):
    def test_full_consist_is_valid_and_binds_the_plan(self):
        movement = self._movement()
        self.consolidated(movement)
        for _ in range(5):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertEqual(movement.plan, self.plan)
        self.assertEqual(
            movement.plan_match_source, Movement.MatchSource.LOCOMOTIVE_RFID
        )
        self.assertIsNotNone(movement.validated_at)
        self.assertEqual(movement.findings.count(), 0)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, MovementPlan.Status.COMPLETED)

    def test_expected_snapshot_is_copied_onto_the_movement(self):
        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.expected_lead_loco, self.loco)
        self.assertEqual(movement.expected_locos, 1)
        self.assertEqual(movement.expected_coaches, 4)
        self.assertEqual(movement.expected_wagons, 0)

    def test_two_locomotives_are_valid_when_the_plan_expects_two(self):
        # The roster is partial: the plan names one identity out of two, so the
        # second locomotive is counted but not identified. This is the ordinary
        # multi-loco freight case.
        plan = self._plan(
            'NDLS-IN-DOUBLE',
            Direction.ENTRY,
            lead=self.loco,
            locos=2,
            coaches=4,
            assign=self.coaches,
        )
        self.plan.status = MovementPlan.Status.CANCELLED
        self.plan.save(update_fields=('status', 'updated_at'))

        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        self._rfid(movement, self.other_loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)
        for _ in range(6):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertEqual(movement.plan, plan)
        self.assertNotIn(LOCOMOTIVE_MISMATCH, self.codes(movement))

    def test_unknown_uid_alone_is_unverified(self):
        movement = self._movement()
        self._rfid(movement, 'DEADBEEF')
        self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.UNVERIFIED)
        self.assertIn(UNKNOWN_RFID, self.codes(movement))
        self.assertEqual(
            self.finding(movement, UNKNOWN_RFID).severity,
            MovementFinding.Severity.FAIL,
        )

    def test_passages_only_is_unverified_without_inventing_identities(self):
        movement = self._movement()
        for _ in range(3):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.UNVERIFIED)
        self.assertNotIn(UNKNOWN_RFID, self.codes(movement))
        self.assertNotIn(COUNT_MISMATCH, self.codes(movement))


class MultiLocoTests(ValidationTestCase):
    """Identity is claimed only for the locomotives the plan actually names."""

    def _two_loco_plan(self, roster=()):
        return self._plan(
            'NDLS-IN-DOUBLE',
            Direction.ENTRY,
            lead=self.loco,
            locos=2,
            coaches=4,
            assign=tuple(self.coaches) + tuple(roster),
        )

    def test_complete_roster_accepts_the_named_second_locomotive(self):
        self.plan.status = MovementPlan.Status.CANCELLED
        self.plan.save(update_fields=('status', 'updated_at'))
        self._two_loco_plan(roster=(self.other_loco,))

        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        self._rfid(movement, self.other_loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)
        for _ in range(6):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertEqual(self.codes(movement), set())

    def test_complete_roster_fails_an_unnamed_locomotive(self):
        self.plan.status = MovementPlan.Status.CANCELLED
        self.plan.save(update_fields=('status', 'updated_at'))
        self._two_loco_plan(roster=(self.other_loco,))

        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        self._rfid(movement, self.third_loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertNotIn(COUNT_MISMATCH, self.codes(movement))
        mismatch = self.finding(movement, LOCOMOTIVE_MISMATCH)
        self.assertEqual(mismatch.details['observed_numbers'], [self.third_loco.number])
        self.assertEqual(
            mismatch.details['expected_numbers'],
            [self.loco.number, self.other_loco.number],
        )
        # The named second locomotive is missing as well, and says so per unit.
        missing = MovementFinding.objects.get(
            movement=movement,
            code=MISSING_LOCOMOTIVE_RFID,
            subject=self.other_loco.number,
        )
        self.assertEqual(missing.severity, MovementFinding.Severity.FAIL)

    def test_complete_roster_fails_a_missing_named_locomotive(self):
        self.plan.status = MovementPlan.Status.CANCELLED
        self.plan.save(update_fields=('status', 'updated_at'))
        self._two_loco_plan(roster=(self.other_loco,))

        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(MISSING_LOCOMOTIVE_RFID, self.codes(movement))
        self.assertIn(COUNT_MISMATCH, self.codes(movement))

    def test_partial_roster_does_not_claim_identity_for_the_extra_loco(self):
        self.plan.status = MovementPlan.Status.CANCELLED
        self.plan.save(update_fields=('status', 'updated_at'))
        self._two_loco_plan()

        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        self._rfid(movement, self.third_loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertNotIn(LOCOMOTIVE_MISMATCH, self.codes(movement))


class UltrasonicWarningTests(ValidationTestCase):
    def test_fewer_passages_still_yields_valid_with_a_warning(self):
        movement = self._movement()
        self.consolidated(movement)
        for _ in range(4):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        finding = self.finding(movement, ULTRASONIC_COUNT_MISMATCH)
        self.assertEqual(finding.severity, MovementFinding.Severity.WARN)
        self.assertEqual(finding.details['passage_count'], 4)
        self.assertEqual(finding.details['unique_rfid_count'], 5)
        self.assertEqual(finding.details['difference'], -1)

    def test_zero_passages_still_yields_valid_with_a_warning(self):
        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertIn(ULTRASONIC_COUNT_MISMATCH, self.codes(movement))

    def test_extra_passages_do_not_create_inventory_identities(self):
        movement = self._movement()
        self.consolidated(movement)
        for _ in range(7):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        finding = self.finding(movement, ULTRASONIC_COUNT_MISMATCH)
        self.assertEqual(finding.details['unique_rfid_count'], 5)
        self.assertEqual(finding.details['difference'], 2)
        self.assertEqual(MovementEvent.objects.filter(movement=movement).count(), 12)

    def test_ultrasonic_warning_never_flags_an_otherwise_valid_movement(self):
        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        severities = {finding.code: finding.severity for finding in self.findings(movement)}
        self.assertEqual(severities[ULTRASONIC_COUNT_MISMATCH], MovementFinding.Severity.WARN)
        self.assertFalse(any(
            severity == MovementFinding.Severity.FAIL
            for severity in severities.values()
        ))


class CompositionTests(ValidationTestCase):
    def test_short_consist_is_flagged_even_when_sensors_agree(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches[:3]:
            self._rfid(movement, coach.rfid_uid)
        for _ in range(4):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        finding = self.finding(movement, COUNT_MISMATCH)
        self.assertEqual(finding.severity, MovementFinding.Severity.FAIL)
        self.assertEqual(finding.details['observed']['coaches'], 3)
        self.assertEqual(finding.details['expected']['coaches'], 4)
        self.assertNotIn(ULTRASONIC_COUNT_MISMATCH, self.codes(movement))

    def test_short_consist_with_extra_passages_carries_both_findings(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches[:3]:
            self._rfid(movement, coach.rfid_uid)
        for _ in range(5):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(COUNT_MISMATCH, self.codes(movement))
        self.assertIn(ULTRASONIC_COUNT_MISMATCH, self.codes(movement))

    def test_unknown_uid_replacing_a_coach_is_flagged_and_retained(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches[:3]:
            self._rfid(movement, coach.rfid_uid)
        self._rfid(movement, 'DEADBEEF')
        for _ in range(5):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(UNKNOWN_RFID, self.codes(movement))
        self.assertIn(COUNT_MISMATCH, self.codes(movement))
        self.assertTrue(
            MovementEvent.objects.filter(
                movement=movement, rfid_uid='DEADBEEF'
            ).exists()
        )

    def test_repeated_uid_counts_once_and_warns(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)
        self._rfid(movement, self.coaches[0].rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        finding = self.finding(movement, DUPLICATE_OBSERVATION)
        self.assertEqual(finding.details['observations'], 2)
        self.assertEqual(finding.details['counted_as'], 1)

    def test_a_repeated_uid_never_fills_a_missing_coach(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches[:3]:
            self._rfid(movement, coach.rfid_uid)
        self._rfid(movement, self.coaches[0].rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(COUNT_MISMATCH, self.codes(movement))
        self.assertIn(DUPLICATE_OBSERVATION, self.codes(movement))

    def test_retired_stock_cannot_satisfy_the_expected_count(self):
        self.coaches[3].is_active = False
        self.coaches[3].save(update_fields=('is_active', 'updated_at'))

        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(RETIRED_ROLLING_STOCK, self.codes(movement))
        self.assertIn(COUNT_MISMATCH, self.codes(movement))


class PlanResolutionTests(ValidationTestCase):
    def test_coach_assignments_find_the_plan_when_the_loco_is_not_read(self):
        movement = self._movement()
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)
        for _ in range(4):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.plan, self.plan)
        self.assertEqual(
            movement.plan_match_source, Movement.MatchSource.COACH_ASSIGNMENT
        )
        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(MISSING_LOCOMOTIVE_RFID, self.codes(movement))
        finding = self.finding(movement, MISSING_LOCOMOTIVE_RFID)
        self.assertEqual(finding.subject, self.loco.number)

        # No fabricated observation: the expected locomotive is not in the
        # stored events and must not appear in the observed counts.
        self.assertFalse(
            MovementEvent.objects.filter(
                movement=movement, rfid_uid=self.loco.rfid_uid
            ).exists()
        )
        self.assertEqual(
            self.finding(movement, COUNT_MISMATCH).details['observed']['locos'],
            0,
        )

    def test_wrong_locomotive_is_not_hidden_by_the_fallback(self):
        movement = self._movement()
        self._rfid(movement, self.other_loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.plan, self.plan)
        self.assertEqual(
            movement.plan_match_source, Movement.MatchSource.COACH_ASSIGNMENT
        )
        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(LOCOMOTIVE_MISMATCH, self.codes(movement))
        self.assertIn(MISSING_LOCOMOTIVE_RFID, self.codes(movement))

    def test_no_usable_plan_is_flagged_as_unplanned(self):
        self.plan.delete()

        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertIsNone(movement.plan)
        self.assertEqual(movement.plan_match_source, Movement.MatchSource.NONE)
        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(UNPLANNED_MOVEMENT, self.codes(movement))

    def test_conflicting_coach_assignments_are_ambiguous_not_guessed(self):
        rival = self._plan(
            'NDLS-IN-RIVAL',
            Direction.ENTRY,
            lead=self.other_loco,
            coaches=4,
            assign=(self.coaches[0],),
        )
        PlanAssignment.objects.filter(plan=self.plan, rolling_stock=self.coaches[0]).delete()

        movement = self._movement()
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertIsNone(movement.plan)
        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(PLAN_AMBIGUOUS, self.codes(movement))
        self.assertIn(UNPLANNED_MOVEMENT, self.codes(movement))
        self.assertEqual(
            set(self.finding(movement, PLAN_AMBIGUOUS).details['candidate_plans']),
            {self.plan.pk, rival.pk},
        )

    def test_a_bound_plan_is_not_reused_by_a_second_movement(self):
        first = self._movement()
        self.consolidated(first)
        validate_movement(first)

        second = self._movement(started_at=T0 + timedelta(minutes=5))
        self.consolidated(second)
        validate_movement(second)
        second.refresh_from_db()

        self.assertIsNone(second.plan)
        self.assertIn(UNPLANNED_MOVEMENT, self.codes(second))

    def test_plan_outside_the_match_window_is_not_eligible(self):
        self.plan.expected_start = T0 - timedelta(minutes=61)
        self.plan.save(update_fields=('expected_start', 'updated_at'))

        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertIsNone(movement.plan)
        self.assertIn(UNPLANNED_MOVEMENT, self.codes(movement))


class InventoryEvidenceTests(ValidationTestCase):
    def test_entry_into_a_yard_that_already_holds_the_stock_warns(self):
        for coach in self.coaches:
            self.inventory(coach.rfid_uid)

        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertIn(ENTRY_ALREADY_PRESENT, self.codes(movement))
        self.assertIn(DIRECTION_CONFLICT, self.codes(movement))
        # The warning must not invent a state change.
        coach_row = InventoryRecord.objects.get(rfid_uid=self.coaches[0].rfid_uid)
        self.assertEqual(coach_row.status, InventoryRecord.Status.IN_YARD)

    def test_single_already_present_unit_is_not_a_direction_conflict(self):
        self.inventory(self.coaches[0].rfid_uid)

        movement = self._movement()
        self.consolidated(movement)

        validate_movement(movement)

        self.assertIn(ENTRY_ALREADY_PRESENT, self.codes(movement))
        self.assertNotIn(DIRECTION_CONFLICT, self.codes(movement))

    def test_exit_of_stock_never_recorded_inside_is_flagged(self):
        plan = self._plan(
            'NDLS-OUT-FULL',
            Direction.EXIT,
            lead=self.loco,
            coaches=4,
            assign=self.coaches,
        )

        movement = self._movement(direction=Direction.EXIT)
        self.consolidated(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.plan, plan)
        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        subjects = {
            finding.subject
            for finding in self.findings(movement)
            if finding.code == UNEXPECTED_EXIT
        }
        self.assertEqual(
            subjects,
            {self.loco.rfid_uid} | {coach.rfid_uid for coach in self.coaches},
        )

    def test_exit_of_recorded_stock_is_valid(self):
        self._plan(
            'NDLS-OUT-FULL',
            Direction.EXIT,
            lead=self.loco,
            coaches=4,
            assign=self.coaches,
        )
        for stock in [self.loco, *self.coaches]:
            self.inventory(stock.rfid_uid)

        movement = self._movement(direction=Direction.EXIT)
        self.consolidated(movement)
        for _ in range(5):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertNotIn(UNEXPECTED_EXIT, self.codes(movement))


class RevalidationTests(ValidationTestCase):
    def test_revalidation_is_idempotent(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches[:2]:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        first = self.finding(movement, COUNT_MISMATCH)
        first_created = first.created_at
        finding_count = MovementFinding.objects.filter(movement=movement).count()
        alert_count = Alert.objects.filter(movement=movement).count()
        self.assertGreater(alert_count, 0)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertEqual(movement.plan, self.plan)
        self.assertEqual(
            MovementFinding.objects.filter(movement=movement).count(),
            finding_count,
        )
        self.assertEqual(
            Alert.objects.filter(movement=movement).count(),
            alert_count,
        )
        again = self.finding(movement, COUNT_MISMATCH)
        self.assertEqual(again.created_at, first_created)

    def test_revalidation_replaces_the_legacy_placeholder(self):
        movement = self._movement()
        self.consolidated(movement)
        MovementFinding.objects.create(
            movement=movement,
            code=VALIDATION_PENDING,
            subject='',
            severity=MovementFinding.Severity.INFO,
            message='placeholder',
        )

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertFalse(
            MovementFinding.objects.filter(
                movement=movement, code=VALIDATION_PENDING
            ).exists()
        )
        # No passages were sent, so the advisory ultrasonic warning is expected;
        # the placeholder INFO row is not.
        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertEqual(movement.findings.count(), 1)
        self.assertEqual(
            movement.findings.get().code, ULTRASONIC_COUNT_MISMATCH
        )

    def test_failures_raise_one_critical_alert_per_finding(self):
        movement = self._movement()
        self._rfid(movement, 'DEADBEEF')

        validate_movement(movement)

        alert = Alert.objects.get(movement=movement, code=UNKNOWN_RFID)
        self.assertEqual(alert.severity, Alert.Severity.CRITICAL)
        self.assertEqual(alert.station, self.station)
        self.assertEqual(alert.subject, 'DEADBEEF')

    def test_an_acknowledged_alert_survives_revalidation(self):
        movement = self._movement()
        self._rfid(movement, 'DEADBEEF')
        validate_movement(movement)

        Alert.objects.filter(movement=movement).update(
            acknowledged_at=T0, acknowledged_by=None
        )
        validate_movement(movement)

        self.assertTrue(
            Alert.objects.filter(
                movement=movement, acknowledged_at=T0
            ).exists()
        )


class ClosureWiringTests(ValidationTestCase):
    """The real ingestion path must validate at closure."""

    def setUp(self):
        super().setUp()
        self.keys = {
            'gate-1': 'key-gate-1',
            'gate-2': 'key-gate-2',
        }

    def post_event(self, seconds, payload, device_id='gate-1'):
        with patch('yard.clock.server_now', return_value=T0 + timedelta(seconds=seconds)):
            response = self.client.post(
                reverse('yard:ingest-events'),
                data=json.dumps(payload),
                content_type='application/json',
                headers={
                    'X-Device-ID': device_id,
                    'X-Api-Key': self.keys[device_id],
                },
            )
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def test_closing_a_bucket_validates_it(self):
        payload = {
            'event_id': 'gate-1-boot-000001',
            'event_type': 'RFID_DETECTED',
            'rfid_uid': self.loco.rfid_uid,
        }
        self.post_event(0, payload)
        self.post_event(30, {**payload, 'event_id': 'gate-1-boot-000002'})

        first = Movement.objects.order_by('started_at', 'id').first()
        first.refresh_from_db()

        self.assertEqual(first.state, Movement.State.CLOSED)
        self.assertEqual(first.validation_result, Movement.Outcome.FLAGGED)
        self.assertIsNotNone(first.validated_at)
        self.assertFalse(
            first.findings.filter(code=VALIDATION_PENDING).exists()
        )
        self.assertTrue(first.findings.filter(code=COUNT_MISMATCH).exists())
