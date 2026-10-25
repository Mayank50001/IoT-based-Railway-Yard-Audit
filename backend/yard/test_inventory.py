"""Tests for RFID-primary inventory transitions at movement closure.

The rules under test are the ones that make revalidation safe:

* one transition per distinct UID actually read, no matter how often it was read;
* the pre-movement state is frozen before the transition is applied;
* a revalidation never replays a transition and never overwrites a later
  movement's inventory state;
* the verification flag follows the movement that established the presence.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.test import TestCase, override_settings

from .models import (
    Device,
    Direction,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementInventoryEffect,
    MovementPlan,
    PlanAssignment,
    RollingStock,
    Station,
)
from .validation import (
    COUNT_MISMATCH,
    MISSING_LOCOMOTIVE_RFID,
    UNEXPECTED_EXIT,
    UNKNOWN_RFID,
    ULTRASONIC_COUNT_MISMATCH,
    validate_movement,
)

T0 = datetime(2026, 10, 25, 9, 0, 0, tzinfo=dt_timezone.utc)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class InventoryTestCase(TestCase):
    def setUp(self):
        self.station = Station.objects.create(code='NDLS', name='New Delhi')
        self.entry = self._device('gate-1', Direction.ENTRY)
        self.exit_lane = self._device('gate-2', Direction.EXIT)

        self.loco = self._stock('WDM3A-1001', 'FA37E63F', RollingStock.Kind.LOCO)
        self.coaches = [
            self._stock(
                f'NR-C-220{index}',
                f'C0C0000{index}',
                RollingStock.Kind.COACH,
            )
            for index in (1, 2, 3, 4)
        ]
        self.entry_plan = self._plan(
            'NDLS-IN-FULL',
            Direction.ENTRY,
            lead=self.loco,
            coaches=4,
            assign=self.coaches,
        )
        self.counter = 0

    # --- fixtures ----------------------------------------------------------

    def _device(self, device_id, direction):
        device = Device(
            device_id=device_id,
            station=self.station,
            lane_mode=direction,
        )
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
    ):
        plan = MovementPlan.objects.create(
            plan_code=code,
            station=self.station,
            direction=direction,
            expected_lead_loco=lead,
            expected_locos=locos,
            expected_coaches=coaches,
            expected_wagons=wagons,
            expected_start=T0,
        )
        for stock in assign:
            PlanAssignment.objects.create(plan=plan, rolling_stock=stock)
        return plan

    def _movement(self, direction=Direction.ENTRY):
        return Movement.objects.create(
            station=self.station,
            device=self.entry if direction == Direction.ENTRY else self.exit_lane,
            direction=direction,
            state=Movement.State.CLOSED,
            started_at=T0,
            last_evidence_at=T0,
            closed_at=T0 + timedelta(seconds=30),
        )

    def _event(self, movement, uid=None):
        self.counter += 1
        stock = (
            RollingStock.objects.filter(rfid_uid=uid).first() if uid else None
        )
        return MovementEvent.objects.create(
            event_id=f'gate-test-{self.counter:06d}',
            movement=movement,
            device=movement.device,
            kind=(
                MovementEvent.Kind.RFID_DETECTED
                if uid
                else MovementEvent.Kind.PHYSICAL_PASSAGE
            ),
            rfid_uid=uid or '',
            rolling_stock=stock,
            received_at=movement.started_at,
        )

    def _rfid(self, movement, uid):
        return self._event(movement, uid)

    def _passage(self, movement):
        return self._event(movement)

    def _consist(self, movement):
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

    def _row(self, uid):
        return InventoryRecord.objects.get(
            station=self.station, rfid_uid=uid
        )

    # --- assertions --------------------------------------------------------

    def assertVerified(self, uid, status=InventoryRecord.Status.IN_YARD):
        row = self._row(uid)
        self.assertEqual(row.status, status)
        self.assertEqual(row.verification, InventoryRecord.Verification.VERIFIED)
        return row

    def assertUnverified(self, uid, status=InventoryRecord.Status.IN_YARD):
        row = self._row(uid)
        self.assertEqual(row.status, status)
        self.assertEqual(
            row.verification, InventoryRecord.Verification.UNVERIFIED
        )
        return row


class EntryTransitionTests(InventoryTestCase):
    def test_entry_creates_one_verified_row_per_distinct_uid(self):
        movement = self._movement()
        self._consist(movement)
        self._passage(movement)

        validate_movement(movement)

        for stock in [self.loco, *self.coaches]:
            row = self.assertVerified(stock.rfid_uid)
            self.assertEqual(row.rolling_stock, stock)
            self.assertEqual(row.last_movement, movement)

        self.assertEqual(
            InventoryRecord.objects.filter(station=self.station).count(), 5
        )
        effects = MovementInventoryEffect.objects.filter(movement=movement)
        self.assertEqual(effects.count(), 5)
        for effect in effects:
            self.assertIsNone(effect.status_before)
            self.assertEqual(
                effect.status_after, InventoryRecord.Status.IN_YARD
            )
            self.assertIsNotNone(effect.applied_at)

    def test_a_repeated_uid_produces_one_effect_and_one_row(self):
        movement = self._movement()
        self._consist(movement)
        self._rfid(movement, self.coaches[0].rfid_uid)
        self._rfid(movement, self.coaches[0].rfid_uid)

        validate_movement(movement)

        self.assertEqual(
            MovementInventoryEffect.objects.filter(movement=movement).count(),
            5,
        )
        self.assertEqual(
            InventoryRecord.objects.filter(station=self.station).count(), 5
        )

    def test_entry_of_stock_already_inside_keeps_its_presence_and_owner(self):
        original = InventoryRecord.objects.create(
            station=self.station,
            rfid_uid=self.coaches[0].rfid_uid,
            rolling_stock=self.coaches[0],
            status=InventoryRecord.Status.IN_YARD,
            verification=InventoryRecord.Verification.VERIFIED,
            since=T0 - timedelta(hours=2),
        )
        movement = self._movement()
        self._consist(movement)

        validate_movement(movement)

        row = self._row(self.coaches[0].rfid_uid)
        self.assertEqual(row.since, original.since)
        self.assertIsNone(row.last_movement)
        self.assertEqual(
            row.verification, InventoryRecord.Verification.VERIFIED
        )
        self.assertIn(
            'ENTRY_ALREADY_PRESENT',
            set(movement.findings.values_list('code', flat=True)),
        )

    def test_entry_after_a_previous_exit_changes_presence_and_since(self):
        InventoryRecord.objects.create(
            station=self.station,
            rfid_uid=self.loco.rfid_uid,
            rolling_stock=self.loco,
            status=InventoryRecord.Status.OUT,
            verification=InventoryRecord.Verification.UNVERIFIED,
            since=T0 - timedelta(days=1),
        )
        movement = self._movement()
        self._consist(movement)

        validate_movement(movement)

        row = self.assertVerified(self.loco.rfid_uid)
        self.assertNotEqual(row.since, T0 - timedelta(days=1))
        self.assertEqual(row.last_movement, movement)

    def test_passages_only_create_no_identified_inventory(self):
        movement = self._movement()
        for _ in range(5):
            self._passage(movement)

        validate_movement(movement)

        self.assertFalse(
            InventoryRecord.objects.filter(station=self.station).exists()
        )
        self.assertFalse(
            MovementInventoryEffect.objects.filter(movement=movement).exists()
        )


class ExitTransitionTests(InventoryTestCase):
    def setUp(self):
        super().setUp()
        self.exit_plan = self._plan(
            'NDLS-OUT-FULL',
            Direction.EXIT,
            lead=self.loco,
            coaches=4,
            assign=self.coaches,
        )

    def test_exit_of_recorded_stock_sets_out(self):
        for stock in [self.loco, *self.coaches]:
            InventoryRecord.objects.create(
                station=self.station,
                rfid_uid=stock.rfid_uid,
                rolling_stock=stock,
                status=InventoryRecord.Status.IN_YARD,
                verification=InventoryRecord.Verification.VERIFIED,
                since=T0 - timedelta(hours=1),
            )

        movement = self._movement(direction=Direction.EXIT)
        self._consist(movement)
        for _ in range(5):
            self._passage(movement)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        for stock in [self.loco, *self.coaches]:
            row = self._row(stock.rfid_uid)
            self.assertEqual(row.status, InventoryRecord.Status.OUT)
            self.assertEqual(
                row.verification, InventoryRecord.Verification.VERIFIED
            )
            self.assertEqual(row.last_movement, movement)

    def test_exit_never_recorded_inside_creates_an_unverified_out_row(self):
        movement = self._movement(direction=Direction.EXIT)
        self._consist(movement)

        validate_movement(movement)

        for stock in [self.loco, *self.coaches]:
            self.assertUnverified(stock.rfid_uid, InventoryRecord.Status.OUT)
        self.assertEqual(
            set(
                movement.findings.filter(code=UNEXPECTED_EXIT).values_list(
                    'subject', flat=True
                )
            ),
            {stock.rfid_uid for stock in [self.loco, *self.coaches]},
        )


class FlaggedMovementTests(InventoryTestCase):
    def test_a_flagged_movement_still_updates_the_units_it_observed(self):
        movement = self._movement()
        self._rfid(movement, self.loco.rfid_uid)
        for coach in self.coaches[:2]:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.FLAGGED)
        self.assertIn(
            COUNT_MISMATCH,
            set(movement.findings.values_list('code', flat=True)),
        )
        for stock in [self.loco, *self.coaches[:2]]:
            self.assertUnverified(stock.rfid_uid)

    def test_an_unread_planned_locomotive_gets_no_inventory_transition(self):
        movement = self._movement()
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertIn(
            MISSING_LOCOMOTIVE_RFID,
            set(movement.findings.values_list('code', flat=True)),
        )
        self.assertFalse(
            InventoryRecord.objects.filter(
                station=self.station, rfid_uid=self.loco.rfid_uid
            ).exists()
        )
        self.assertEqual(
            InventoryRecord.objects.filter(station=self.station).count(), 4
        )

    def test_a_planned_locomotive_with_no_read_does_not_receive_a_transition(self):
        # Guard against a future "helpful" fix that invents the missing read.
        movement = self._movement()
        for coach in self.coaches:
            self._rfid(movement, coach.rfid_uid)

        validate_movement(movement)

        self.assertEqual(
            set(
                MovementInventoryEffect.objects.filter(
                    movement=movement
                ).values_list('rfid_uid', flat=True)
            ),
            {coach.rfid_uid for coach in self.coaches},
        )


class UltrasonicIndependenceTests(InventoryTestCase):
    def test_an_ultrasonic_warning_does_not_downgrade_inventory(self):
        movement = self._movement()
        self._consist(movement)  # zero passages

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        self.assertIn(
            ULTRASONIC_COUNT_MISMATCH,
            set(movement.findings.values_list('code', flat=True)),
        )
        for stock in [self.loco, *self.coaches]:
            self.assertVerified(stock.rfid_uid)


class UnknownUidTests(InventoryTestCase):
    def test_an_unknown_uid_is_kept_as_unverified_inventory(self):
        movement = self._movement()
        self._rfid(movement, 'DEADBEEF')

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(
            movement.validation_result, Movement.Outcome.UNVERIFIED
        )
        self.assertIn(
            UNKNOWN_RFID,
            set(movement.findings.values_list('code', flat=True)),
        )
        row = self.assertUnverified('DEADBEEF')
        self.assertIsNone(row.rolling_stock)
        self.assertEqual(row.last_movement, movement)


class RevalidationTests(InventoryTestCase):
    def test_revalidation_does_not_repeat_a_transition(self):
        movement = self._movement()
        self._consist(movement)

        validate_movement(movement)
        rows = {
            stock.rfid_uid: self._row(stock.rfid_uid)
            for stock in [self.loco, *self.coaches]
        }
        applied = dict(
            MovementInventoryEffect.objects.filter(movement=movement).values_list(
                'rfid_uid', 'applied_at'
            )
        )

        validate_movement(movement)

        for uid, row in rows.items():
            again = self._row(uid)
            self.assertEqual(again.status, row.status)
            self.assertEqual(again.since, row.since)
            self.assertEqual(again.last_movement_id, movement.pk)

        self.assertEqual(
            dict(
                MovementInventoryEffect.objects.filter(
                    movement=movement
                ).values_list('rfid_uid', 'applied_at')
            ),
            applied,
        )
        self.assertEqual(
            InventoryRecord.objects.filter(station=self.station).count(), 5
        )

    def test_revalidation_does_not_overwrite_a_later_movements_state(self):
        later_plan = self._plan(
            'NDLS-OUT-FULL',
            Direction.EXIT,
            lead=self.loco,
            coaches=4,
            assign=self.coaches,
        )

        first = self._movement()
        self._consist(first)
        validate_movement(first)
        first.refresh_from_db()
        self.assertEqual(first.validation_result, Movement.Outcome.VALID)

        second = Movement.objects.create(
            station=self.station,
            device=self.exit_lane,
            direction=Direction.EXIT,
            state=Movement.State.CLOSED,
            started_at=T0 + timedelta(minutes=10),
            last_evidence_at=T0 + timedelta(minutes=10),
            closed_at=T0 + timedelta(minutes=11),
        )
        self._consist(second)
        validate_movement(second)
        second.refresh_from_db()
        self.assertEqual(second.plan, later_plan)
        self.assertEqual(second.validation_result, Movement.Outcome.VALID)

        exited = self._row(self.loco.rfid_uid)
        exit_since = exited.since
        self.assertEqual(exited.status, InventoryRecord.Status.OUT)

        # Revalidating the earlier entry must not put the stock back inside,
        # and must still judge it against the state before the entry.
        validate_movement(first)
        first.refresh_from_db()

        after = self._row(self.loco.rfid_uid)
        self.assertEqual(after.status, InventoryRecord.Status.OUT)
        self.assertEqual(after.since, exit_since)
        self.assertEqual(after.last_movement, second)
        self.assertEqual(first.validation_result, Movement.Outcome.VALID)
        self.assertNotIn(
            'ENTRY_ALREADY_PRESENT',
            set(first.findings.values_list('code', flat=True)),
        )

    def test_a_late_registration_reconciles_the_row_this_movement_owns(self):
        plan = self._plan(
            'NDLS-COACH-ONLY',
            Direction.ENTRY,
            lead=None,
            locos=0,
            coaches=1,
        )
        unknown_uid = 'DEADBEEF'
        movement = self._movement()
        self._rfid(movement, unknown_uid)

        validate_movement(movement)
        movement.refresh_from_db()
        self.assertEqual(
            movement.validation_result, Movement.Outcome.UNVERIFIED
        )
        row = self.assertUnverified(unknown_uid, InventoryRecord.Status.IN_YARD)

        coach = self._stock('NR-C-9001', unknown_uid, RollingStock.Kind.COACH)
        PlanAssignment.objects.create(plan=plan, rolling_stock=coach)

        validate_movement(movement)
        movement.refresh_from_db()

        self.assertEqual(movement.validation_result, Movement.Outcome.VALID)
        row.refresh_from_db()
        self.assertEqual(row.rolling_stock, coach)
        self.assertEqual(
            row.verification, InventoryRecord.Verification.VERIFIED
        )
        self.assertEqual(
            MovementInventoryEffect.objects.get(
                movement=movement, rfid_uid=unknown_uid
            ).status_before,
            None,
        )
