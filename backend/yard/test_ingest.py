"""Tests for device authentication, event ingestion, and movement buckets."""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.hashers import check_password as real_check_password
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    Device,
    Direction,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementFinding,
    RollingStock,
    Station,
)
from .movements import close_idle_movements, open_movement_for
from .services import set_lane_mode

T0 = datetime(2026, 10, 25, 9, 30, 0, tzinfo=dt_timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def event(event_id='gate-1-boot-000001', **overrides):
    payload = {
        'event_id': event_id,
        'event_type': 'RFID_DETECTED',
        'device_id': 'gate-1',
        'timestamp': '2026-10-25T09:30:00.000Z',
        'sequence': 1,
        'rfid_uid': 'FA37E63F',
    }
    payload.update(overrides)
    return payload


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class IngestTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.station = Station.objects.create(code='NDLS', name='New Delhi')
        self.keys = {}
        self.device = self._make_device('gate-1', 'bench-key-1', Direction.ENTRY)
        self.loco = RollingStock.objects.create(
            number='WDM3A-1001',
            rfid_uid='FA37E63F',
            kind=RollingStock.Kind.LOCO,
        )
        self.coach = RollingStock.objects.create(
            number='NR-C-2201',
            rfid_uid='0E78E13F',
            kind=RollingStock.Kind.COACH,
        )

    def _make_device(self, device_id, key, mode):
        device = Device(device_id=device_id, station=self.station, lane_mode=mode)
        device.set_api_key(key)
        device.save()
        if mode:
            LaneModeChange.objects.create(
                device=device,
                direction=mode,
                note='test setup',
            )
        self.keys[device_id] = key
        return device

    def post_events(self, payload, device=None, key=None, headers=None):
        device = device or self.device
        if headers is None:
            headers = {
                'X-Device-ID': device.device_id,
                'X-Api-Key': key if key is not None else self.keys[device.device_id],
            }
        return self.client.post(
            reverse('yard:ingest-events'),
            data=json.dumps(payload),
            content_type='application/json',
            headers=headers,
        )

    def post_at(self, seconds, payload, **kwargs):
        with patch('yard.clock.server_now', return_value=at(seconds)):
            return self.post_events(payload, **kwargs)


class DeviceAuthenticationTests(IngestTestCase):
    def test_headers_are_required(self):
        response = self.client.post(
            reverse('yard:ingest-events'),
            data=json.dumps(event()),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'DEVICE_AUTH_REQUIRED')
        self.assertFalse(MovementEvent.objects.exists())

    def test_wrong_key_is_rejected(self):
        response = self.post_events(event(), key='not-the-key')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'DEVICE_AUTH_FAILED')
        self.assertFalse(MovementEvent.objects.exists())

    def test_unknown_device_is_rejected(self):
        response = self.post_events(
            event(),
            headers={'X-Device-ID': 'gate-unknown', 'X-Api-Key': 'bench-key-1'},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'DEVICE_AUTH_FAILED')

    def test_inactive_device_is_rejected(self):
        self.device.is_active = False
        self.device.save(update_fields=('is_active', 'updated_at'))

        response = self.post_events(event())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'DEVICE_INACTIVE')

    def test_key_is_verified_once_per_cache_window(self):
        with patch(
            'yard.models.check_password',
            side_effect=real_check_password,
        ) as mocked:
            first = self.post_at(0, event(event_id='gate-1-boot-000001'))
            second = self.post_at(
                5,
                event(event_id='gate-1-boot-000002', rfid_uid='0E78E13F'),
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(mocked.call_count, 1)

    def test_rotating_the_key_invalidates_the_cache(self):
        self.post_at(0, event(event_id='gate-1-boot-000001'))

        self.device.set_api_key('rotated-key')
        self.device.save(update_fields=('api_key_hash', 'updated_at'))
        self.keys['gate-1'] = 'rotated-key'

        stale = self.post_events(event(event_id='gate-1-boot-000002'), key='bench-key-1')
        fresh = self.post_events(event(event_id='gate-1-boot-000003'))

        self.assertEqual(stale.status_code, 401)
        self.assertEqual(fresh.status_code, 200)


class LaneModeGateTests(IngestTestCase):
    def test_unset_lane_mode_blocks_ingestion(self):
        spare = self._make_device('gate-spare', 'spare-key', None)

        response = self.post_events(event(device_id='gate-spare'), device=spare)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'LANE_MODE_NOT_SET')
        self.assertFalse(Movement.objects.exists())
        self.assertFalse(MovementEvent.objects.exists())

    def test_spoofed_device_id_rejects_the_whole_batch(self):
        response = self.post_events(
            {
                'events': [
                    event(event_id='gate-1-boot-000001'),
                    event(event_id='gate-1-boot-000002', device_id='gate-2'),
                ]
            }
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'DEVICE_ID_MISMATCH')
        self.assertFalse(MovementEvent.objects.exists())
        self.assertFalse(Movement.objects.exists())


class AcceptedEventTests(IngestTestCase):
    def test_rfid_event_opens_a_movement_from_the_lane_mode(self):
        response = self.post_at(0, event())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['accepted'], 1)
        self.assertEqual(body['rejected'], 0)
        result = body['results'][0]
        self.assertEqual(result['status'], 'accepted')
        self.assertTrue(result['movement_created'])
        self.assertEqual(result['rolling_stock'], 'WDM3A-1001')

        movement = Movement.objects.get()
        self.assertEqual(movement.direction, Direction.ENTRY)
        self.assertEqual(movement.direction_source, Movement.DirectionSource.OPERATOR)
        self.assertEqual(
            movement.lane_mode_change,
            self.device.lane_mode_changes.get(),
        )
        self.assertEqual(movement.station, self.station)
        self.assertEqual(movement.started_at, T0)
        self.assertEqual(movement.last_evidence_at, T0)

        stored = MovementEvent.objects.get()
        self.assertEqual(stored.rfid_uid, 'FA37E63F')
        self.assertEqual(stored.rolling_stock, self.loco)
        self.assertEqual(stored.movement, movement)
        self.assertEqual(stored.received_at, T0)

    def test_uid_is_canonicalised(self):
        response = self.post_at(0, event(rfid_uid='fa:37:e6:3f'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['rfid_uid'], 'FA37E63F')
        self.assertEqual(MovementEvent.objects.get().rfid_uid, 'FA37E63F')

    def test_unknown_uid_is_kept_as_evidence(self):
        response = self.post_at(0, event(rfid_uid='DEADBEEF'))

        self.assertEqual(response.status_code, 200)
        stored = MovementEvent.objects.get()
        self.assertEqual(stored.rfid_uid, 'DEADBEEF')
        self.assertIsNone(stored.rolling_stock)

    def test_kind_alias_is_accepted(self):
        payload = event()
        payload.pop('event_type')
        payload['kind'] = 'RFID_DETECTED'

        response = self.post_at(0, payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MovementEvent.objects.get().kind, 'RFID_DETECTED')

    def test_passage_event_stores_metadata(self):
        response = self.post_at(
            0,
            {
                'event_id': 'gate-1-boot-000009',
                'event_type': 'PHYSICAL_PASSAGE',
                'device_id': 'gate-1',
                'sequence': 9,
                'timestamp': '2026-10-25T09:30:09.400Z',
                'metadata': {
                    'started_at': '2026-10-25T09:30:09.400Z',
                    'ended_at': '2026-10-25T09:30:09.850Z',
                    'duration_ms': 450,
                    'min_distance_cm': 8,
                    'peak_distance_cm': 41,
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        stored = MovementEvent.objects.get()
        self.assertEqual(stored.kind, 'PHYSICAL_PASSAGE')
        self.assertEqual(stored.rfid_uid, '')
        self.assertEqual(stored.passage_duration_ms, 450)
        self.assertIsNotNone(stored.passage_started_at)
        self.assertEqual(stored.raw_payload['metadata']['duration_ms'], 450)

    def test_last_seen_is_recorded(self):
        self.post_at(0, event())

        self.device.refresh_from_db()
        self.assertEqual(self.device.last_seen_at, T0)


class DuplicateTests(IngestTestCase):
    def test_repeated_event_id_is_idempotent(self):
        first = self.post_at(0, event())
        second = self.post_at(5, event())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['duplicates'], 1)
        self.assertEqual(second.json()['accepted'], 0)

        result = second.json()['results'][0]
        self.assertEqual(result['status'], 'duplicate')
        self.assertEqual(
            result['movement_id'],
            first.json()['results'][0]['movement_id'],
        )

        self.assertEqual(MovementEvent.objects.count(), 1)
        self.assertEqual(Movement.objects.count(), 1)

    def test_same_event_id_from_another_device_is_a_conflict(self):
        other = self._make_device('gate-2', 'bench-key-2', Direction.EXIT)
        self.post_at(0, event())
        first_movement = Movement.objects.get()

        response = self.post_at(5, event(device_id='gate-2'), device=other)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'EVENT_ID_DEVICE_MISMATCH')
        self.assertEqual(MovementEvent.objects.count(), 1)
        self.assertEqual(Movement.objects.get(), first_movement)


class BatchTests(IngestTestCase):
    def test_per_event_outcomes_in_one_batch(self):
        response = self.post_at(
            0,
            {
                'events': [
                    event(event_id='gate-1-boot-000001'),
                    event(event_id='gate-1-boot-000002', rfid_uid='not-hex'),
                    {
                        'event_id': 'gate-1-boot-000003',
                        'event_type': 'RFID_DETECTED',
                    },
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['accepted'], 1)
        self.assertEqual(body['rejected'], 2)
        self.assertEqual(
            [result['status'] for result in body['results']],
            ['accepted', 'rejected', 'rejected'],
        )
        self.assertEqual(body['results'][1]['error']['code'], 'INVALID_UID')
        self.assertEqual(body['results'][2]['error']['code'], 'MISSING_RFID_UID')

        self.assertEqual(MovementEvent.objects.count(), 1)
        self.assertEqual(Movement.objects.count(), 1)

    def test_passage_with_uid_is_rejected(self):
        response = self.post_at(
            0,
            event(event_type='PHYSICAL_PASSAGE', rfid_uid='FA37E63F'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rejected'], 1)
        self.assertEqual(
            response.json()['results'][0]['error']['code'],
            'UNEXPECTED_RFID_UID',
        )
        self.assertFalse(MovementEvent.objects.exists())
        self.assertFalse(Movement.objects.exists())

    def test_oversized_batch_is_rejected(self):
        events = [
            event(event_id=f'gate-1-boot-{index:06d}')
            for index in range(51)
        ]

        response = self.post_at(0, {'events': events})

        self.assertEqual(response.status_code, 400)
        self.assertIn('at most 50', response.json()['detail'])
        self.assertFalse(MovementEvent.objects.exists())

    def test_malformed_envelope_is_rejected(self):
        not_a_list = self.post_at(0, {'events': 'nope'})
        empty = self.post_at(0, {'events': []})
        scalar = self.post_at(0, ['not', 'an', 'event'])

        for response in (not_a_list, empty, scalar):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['code'], 'PARSE_ERROR')

        self.assertFalse(MovementEvent.objects.exists())


class MovementBucketTests(IngestTestCase):
    def test_gap_of_exactly_twenty_seconds_stays_in_the_movement(self):
        self.post_at(0, event(event_id='gate-1-boot-000001'))
        self.post_at(20, event(event_id='gate-1-boot-000002'))

        self.assertEqual(Movement.objects.count(), 1)
        movement = Movement.objects.get()
        self.assertEqual(movement.last_evidence_at, at(20))

    def test_gap_over_twenty_seconds_opens_a_new_movement(self):
        self.post_at(0, event(event_id='gate-1-boot-000001'))
        self.post_at(15, event(event_id='gate-1-boot-000002'))
        self.post_at(36, event(event_id='gate-1-boot-000003'))

        first, second = Movement.objects.order_by('started_at', 'id')
        self.assertEqual(first.state, Movement.State.CLOSED)
        self.assertEqual(first.closed_at, at(15))
        self.assertTrue(
            MovementFinding.objects.filter(
                movement=first,
                code='VALIDATION_PENDING',
            ).exists()
        )
        self.assertIsNone(first.validation_result)
        self.assertEqual(second.state, Movement.State.OPEN)
        self.assertEqual(second.started_at, at(36))

    def test_mode_change_applies_to_the_next_movement_only(self):
        self.post_at(0, event(event_id='gate-1-boot-000001'))
        set_lane_mode(self.device, Direction.EXIT, note='rake leaving')
        self.device.refresh_from_db()

        self.post_at(10, event(event_id='gate-1-boot-000002'))
        self.post_at(40, event(event_id='gate-1-boot-000003'))

        first, second = Movement.objects.order_by('started_at', 'id')
        self.assertEqual(first.direction, Direction.ENTRY)
        self.assertEqual(second.direction, Direction.EXIT)
        self.assertEqual(second.lane_mode_change.direction, Direction.EXIT)
        self.assertNotEqual(first.lane_mode_change_id, second.lane_mode_change_id)

    def test_sweep_closes_idle_movements_once(self):
        self.post_at(0, event())

        self.assertEqual(close_idle_movements(now=at(20)), 0)
        self.assertEqual(close_idle_movements(now=at(21)), 1)
        self.assertEqual(close_idle_movements(now=at(60)), 0)

        movement = Movement.objects.get()
        self.assertEqual(movement.state, Movement.State.CLOSED)
        self.assertEqual(movement.closed_at, T0)
        self.assertIsNone(open_movement_for(self.device))
