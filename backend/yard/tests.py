from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Device,
    Direction,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementPlan,
    PlanAssignment,
    RollingStock,
    Station,
)
from .services import set_lane_mode
from .uid import canonical_uid


class CanonicalUidTests(TestCase):
    def test_separators_are_removed_and_letters_uppercased(self):
        self.assertEqual(canonical_uid('fa:37:e6:3f'), 'FA37E63F')

    def test_leading_zero_is_preserved(self):
        self.assertEqual(canonical_uid('0e:78:e1:3f'), '0E78E13F')

    def test_spaces_and_dashes_are_accepted(self):
        self.assertEqual(canonical_uid(' FA-37 E6-3F '), 'FA37E63F')

    def test_non_hex_is_rejected(self):
        with self.assertRaises(ValidationError):
            canonical_uid('not-a-uid')

    def test_odd_length_is_rejected(self):
        with self.assertRaises(ValidationError):
            canonical_uid('FA37E')


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class YardModelTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(code='NDLS', name='New Delhi')
        self.entry = self._device('gate-entry-1', 'bench-key-1', Direction.ENTRY)
        self.entry_two = self._device('gate-entry-2', 'bench-key-2', Direction.ENTRY)
        self.exit = self._device('gate-exit-1', 'bench-key-3', Direction.EXIT)
        self.unset = self._device('gate-spare-1', 'bench-key-4', None)

        self.loco = RollingStock.objects.create(
            number='WDM3A-1001',
            rfid_uid='fa:37:e6:3f',
            kind=RollingStock.Kind.LOCO,
        )
        self.plan = MovementPlan.objects.create(
            plan_code='PLAN-001',
            station=self.station,
            direction=Direction.ENTRY,
            expected_lead_loco=self.loco,
            expected_locos=1,
            expected_coaches=1,
            expected_start=timezone.now(),
        )
        self.movement = Movement.objects.create(
            station=self.station,
            device=self.entry,
            direction=Direction.ENTRY,
        )

    def _device(self, device_id, api_key, lane_mode):
        device = Device(device_id=device_id, station=self.station, lane_mode=lane_mode)
        device.set_api_key(api_key)
        device.save()
        if lane_mode:
            LaneModeChange.objects.create(
                device=device,
                direction=lane_mode,
                note='test setup',
            )
        return device

    def test_device_api_key_is_hashed(self):
        self.assertNotEqual(self.entry.api_key_hash, 'bench-key-1')
        self.assertTrue(self.entry.check_api_key('bench-key-1'))
        self.assertFalse(self.entry.check_api_key('wrong-key'))

    def test_lane_without_a_mode_is_allowed_but_unusable(self):
        self.assertIsNone(self.unset.lane_mode)
        self.assertIsNone(self.unset.lane_mode_changes.first())

    def test_set_lane_mode_updates_the_lane_and_records_history(self):
        user = get_user_model().objects.create_user(
            username='yard-master',
            password='secret',
        )

        change = set_lane_mode(
            self.unset,
            Direction.ENTRY,
            user=user,
            note='Loading rake',
        )

        self.unset.refresh_from_db()
        self.assertEqual(self.unset.lane_mode, Direction.ENTRY)
        self.assertEqual(change.direction, Direction.ENTRY)
        self.assertEqual(change.set_by, user)
        self.assertEqual(self.unset.lane_mode_changes.count(), 1)

    def test_set_lane_mode_rejects_unknown_directions(self):
        with self.assertRaises(ValidationError):
            set_lane_mode(self.unset, 'SIDEWAYS')

    def test_movement_direction_must_match_the_recorded_lane_mode(self):
        change = self.unset.lane_mode_changes.create(
            device=self.unset,
            direction=Direction.ENTRY,
        )
        movement = Movement(
            station=self.station,
            device=self.unset,
            direction=Direction.EXIT,
            lane_mode_change=change,
        )

        with self.assertRaises(ValidationError) as raised:
            movement.full_clean()

        self.assertIn('direction', raised.exception.message_dict)

    def test_rolling_stock_uid_is_canonicalised_on_save(self):
        self.assertEqual(self.loco.rfid_uid, 'FA37E63F')

    def test_plan_expected_total(self):
        self.assertEqual(self.plan.expected_total, 2)

    def test_only_one_open_movement_per_lane(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Movement.objects.create(
                station=self.station,
                device=self.entry,
                direction=Direction.ENTRY,
            )

    def test_closed_movement_does_not_block_the_lane(self):
        self.movement.state = Movement.State.CLOSED
        self.movement.closed_at = timezone.now()
        self.movement.save()

        second = Movement.objects.create(
            station=self.station,
            device=self.entry,
            direction=Direction.ENTRY,
        )
        self.assertEqual(second.state, Movement.State.OPEN)

    def test_plan_binds_to_one_movement(self):
        Movement.objects.create(
            station=self.station,
            device=self.entry_two,
            direction=Direction.ENTRY,
            plan=self.plan,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Movement.objects.create(
                station=self.station,
                device=self.exit,
                direction=Direction.ENTRY,
                plan=self.plan,
            )

    def test_event_id_is_unique(self):
        MovementEvent.objects.create(
            event_id='evt-1',
            movement=self.movement,
            device=self.entry,
            kind=MovementEvent.Kind.PHYSICAL_PASSAGE,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            MovementEvent.objects.create(
                event_id='evt-1',
                movement=self.movement,
                device=self.entry,
                kind=MovementEvent.Kind.PHYSICAL_PASSAGE,
            )

    def test_rfid_event_requires_a_uid(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovementEvent.objects.create(
                event_id='evt-2',
                movement=self.movement,
                device=self.entry,
                kind=MovementEvent.Kind.RFID_DETECTED,
            )

    def test_passage_event_rejects_a_uid(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovementEvent.objects.create(
                event_id='evt-3',
                movement=self.movement,
                device=self.entry,
                kind=MovementEvent.Kind.PHYSICAL_PASSAGE,
                rfid_uid='FA37E63F',
            )

    def test_inventory_is_unique_per_station_and_uid(self):
        record = InventoryRecord.objects.create(
            station=self.station,
            rfid_uid='0e:78:e1:3f',
        )
        self.assertEqual(record.rfid_uid, '0E78E13F')

        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryRecord.objects.create(
                station=self.station,
                rfid_uid='0E78E13F',
            )


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class DeviceAdminTests(TestCase):
    def test_changing_the_lane_mode_in_admin_is_audited(self):
        admin_user = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='secret',
        )
        self.client.force_login(admin_user)

        station = Station.objects.create(code='NDLS', name='New Delhi')
        device = Device(device_id='gate-1', station=station)
        device.set_api_key('bench-key')
        device.save()

        response = self.client.post(
            reverse('admin:yard_device_change', args=[device.pk]),
            {
                'device_id': 'gate-1',
                'label': '',
                'station': station.pk,
                'lane_mode': Direction.ENTRY,
                'is_active': 'on',
                'api_key': '',
            },
        )

        self.assertEqual(response.status_code, 302)

        device.refresh_from_db()
        self.assertEqual(device.lane_mode, Direction.ENTRY)
        change = device.lane_mode_changes.get()
        self.assertEqual(change.direction, Direction.ENTRY)
        self.assertEqual(change.set_by, admin_user)
        self.assertTrue(device.check_api_key('bench-key'))


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class SeedDemoCommandTests(TestCase):
    def test_seed_is_idempotent(self):
        output = StringIO()
        call_command('seed_demo', stdout=output)
        call_command('seed_demo', stdout=output)

        self.assertEqual(Station.objects.count(), 1)
        self.assertEqual(Device.objects.count(), 1)
        self.assertEqual(RollingStock.objects.count(), 10)
        self.assertEqual(MovementPlan.objects.count(), 3)
        self.assertEqual(PlanAssignment.objects.count(), 9)
        self.assertEqual(LaneModeChange.objects.count(), 1)

        device = Device.objects.get()
        self.assertEqual(device.lane_mode, Direction.ENTRY)
        self.assertTrue(device.has_api_key)
        self.assertIn('generated', output.getvalue())

    def test_rotate_key_changes_the_stored_hash(self):
        call_command('seed_demo', stdout=StringIO())
        device = Device.objects.get()
        previous_hash = device.api_key_hash

        output = StringIO()
        call_command('seed_demo', '--rotate-key', stdout=output)

        device.refresh_from_db()
        self.assertNotEqual(device.api_key_hash, previous_hash)
        self.assertIn('shown once', output.getvalue())

    def test_reset_removes_demo_history_but_keeps_master_data(self):
        call_command('seed_demo', stdout=StringIO())
        station = Station.objects.get(code='NDLS')
        device = Device.objects.get()
        Movement.objects.create(
            station=station,
            device=device,
            direction=Direction.ENTRY,
        )
        InventoryRecord.objects.create(
            station=station,
            rfid_uid='FA37E63F',
        )

        output = StringIO()
        call_command('seed_demo', '--reset', stdout=output)

        self.assertFalse(Movement.objects.exists())
        self.assertFalse(InventoryRecord.objects.exists())
        self.assertEqual(MovementPlan.objects.count(), 3)
        self.assertEqual(RollingStock.objects.count(), 10)
        self.assertIn('Reset: deleted', output.getvalue())
