"""Tests for the station-scoped read APIs.

The read surface is where a scoping mistake would leak another station's yard, so
the tests concentrate on the boundary rather than on serialisation details.
"""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Alert,
    Device,
    Direction,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementPlan,
    PlanAssignment,
    RollingStock,
    StaffProfile,
    Station,
)
from .validation import validate_movement

User = get_user_model()
PASSWORD = 'demo-pass-123'


class CsrfEnforcingClient(Client):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('enforce_csrf_checks', True)
        super().__init__(*args, **kwargs)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class ReadApiTestCase(TestCase):
    client_class = CsrfEnforcingClient

    def setUp(self):
        cache.clear()
        self.counter = 0

        self.station = Station.objects.create(code='NDLS', name='New Delhi')
        self.other_station = Station.objects.create(
            code='BCT', name='Mumbai Central'
        )

        self.master = self._user(
            'master', self.station, StaffProfile.Role.YARD_MASTER
        )
        self.viewer = self._user(
            'viewer', self.station, StaffProfile.Role.VIEWER
        )
        self.outsider = self._user(
            'outsider', self.other_station, StaffProfile.Role.YARD_MASTER
        )

        self.entry = self._lane('gate-1', self.station, Direction.ENTRY)
        self.foreign_lane = self._lane(
            'gate-9', self.other_station, Direction.EXIT
        )

        self.loco = self._stock(
            'WDM3A-1001', 'FA37E63F', RollingStock.Kind.LOCO
        )
        self.coaches = [
            self._stock(
                f'NR-C-22{index:02d}',
                f'C0C000{index:02d}',
                RollingStock.Kind.COACH,
            )
            for index in (1, 2, 3, 4)
        ]
        self.plan = MovementPlan.objects.create(
            plan_code='NDLS-IN-FULL',
            station=self.station,
            direction=Direction.ENTRY,
            expected_lead_loco=self.loco,
            expected_locos=1,
            expected_coaches=4,
            expected_start=timezone.now(),
        )
        for coach in self.coaches:
            PlanAssignment.objects.create(plan=self.plan, rolling_stock=coach)

        self.movement = self._closed_movement(
            station=self.station,
            device=self.entry,
            direction=Direction.ENTRY,
            uids=[self.loco.rfid_uid]
            + [coach.rfid_uid for coach in self.coaches[:2]],
        )
        validate_movement(self.movement)

        self.foreign_movement = self._closed_movement(
            station=self.other_station,
            device=self.foreign_lane,
            direction=Direction.EXIT,
            uids=[],
        )
        validate_movement(self.foreign_movement)

    # --- fixtures ----------------------------------------------------------

    def _user(self, username, station, role):
        user = User.objects.create_user(username=username, password=PASSWORD)
        StaffProfile.objects.create(user=user, station=station, role=role)
        return user

    def _lane(self, device_id, station, direction):
        device = Device(
            device_id=device_id, station=station, lane_mode=direction
        )
        device.set_api_key(f'key-{device_id}')
        device.save()
        LaneModeChange.objects.create(device=device, direction=direction)
        return device

    def _stock(self, number, uid, kind):
        return RollingStock.objects.create(number=number, rfid_uid=uid, kind=kind)

    def _closed_movement(self, station, device, direction, uids):
        now = timezone.now()
        movement = Movement.objects.create(
            station=station,
            device=device,
            direction=direction,
            state=Movement.State.CLOSED,
            started_at=now,
            last_evidence_at=now,
            closed_at=now + timedelta(seconds=30),
        )
        for uid in uids:
            self.counter += 1
            MovementEvent.objects.create(
                event_id=f'{device.device_id}-read-{self.counter:04d}',
                movement=movement,
                device=device,
                kind=MovementEvent.Kind.RFID_DETECTED,
                rfid_uid=uid,
                rolling_stock=RollingStock.objects.filter(
                    rfid_uid=uid
                ).first(),
                received_at=now,
            )
        return movement

    # --- helpers -----------------------------------------------------------

    def csrf_token(self):
        return self.client.get(reverse('yard:auth-csrf')).json()['csrfToken']

    def post_json(self, url, payload=None):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type='application/json',
            headers={'X-CSRFToken': self.csrf_token()},
        )

    # --- authentication ----------------------------------------------------

    def test_every_read_endpoint_requires_a_session(self):
        urls = [
            reverse('yard:overview'),
            reverse('yard:movement-list'),
            reverse('yard:movement-detail', args=[self.movement.pk]),
            reverse('yard:finding-list'),
            reverse('yard:alert-list'),
            reverse('yard:inventory-list'),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['code'], 'NOT_AUTHENTICATED')

    # --- station scoping ---------------------------------------------------

    def test_movement_list_contains_only_the_users_station(self):
        self.client.force_login(self.master)

        response = self.client.get(reverse('yard:movement-list'))

        self.assertEqual(response.status_code, 200)
        ids = [row['id'] for row in response.json()['movements']]
        self.assertEqual(ids, [self.movement.pk])
        self.assertNotIn(self.foreign_movement.pk, ids)

    def test_another_stations_movement_is_a_404(self):
        self.client.force_login(self.master)

        response = self.client.get(
            reverse('yard:movement-detail', args=[self.foreign_movement.pk])
        )
        missing = self.client.get(
            reverse('yard:movement-detail', args=[999999])
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), missing.json())
        self.assertEqual(response.json()['code'], 'NOT_FOUND')

    def test_findings_and_alerts_are_station_scoped(self):
        self.client.force_login(self.master)

        findings = self.client.get(reverse('yard:finding-list')).json()
        alerts = self.client.get(reverse('yard:alert-list')).json()

        self.assertGreater(findings['count'], 0)
        self.assertGreater(alerts['count'], 0)
        for row in findings['findings']:
            self.assertEqual(row['code'], row['code'].upper())
        for row in alerts['alerts']:
            self.assertEqual(row['movement_id'], self.movement.pk)

    def test_inventory_is_station_scoped_and_unverified_for_a_flagged_movement(self):
        self.client.force_login(self.master)

        response = self.client.get(reverse('yard:inventory-list')).json()

        self.assertEqual(response['count'], 3)
        for row in response['inventory']:
            self.assertEqual(row['status'], InventoryRecord.Status.IN_YARD)
            self.assertEqual(
                row['verification'],
                InventoryRecord.Verification.UNVERIFIED,
            )
            self.assertEqual(
                row['last_movement_id'], self.movement.pk
            )

    def test_a_foreign_alert_cannot_be_acknowledged(self):
        alert = Alert.objects.create(
            station=self.other_station,
            movement=self.foreign_movement,
            code='TEST_ONLY',
            severity=Alert.Severity.CRITICAL,
            message='foreign',
        )
        self.client.force_login(self.master)

        response = self.post_json(
            reverse('yard:alert-acknowledge', args=[alert.pk])
        )

        self.assertEqual(response.status_code, 404)
        alert.refresh_from_db()
        self.assertIsNone(alert.acknowledged_at)

    # --- movement list -----------------------------------------------------

    def test_movement_list_filters_and_paginates(self):
        self.client.force_login(self.master)

        by_result = self.client.get(
            reverse('yard:movement-list'), {'result': 'FLAGGED'}
        ).json()
        self.assertEqual(by_result['count'], 1)

        by_state = self.client.get(
            reverse('yard:movement-list'), {'state': 'OPEN'}
        ).json()
        self.assertEqual(by_state['count'], 0)

        by_device = self.client.get(
            reverse('yard:movement-list'), {'device_id': 'gate-9'}
        ).json()
        self.assertEqual(by_device['count'], 0)

        page = self.client.get(
            reverse('yard:movement-list'), {'limit': 1, 'offset': 1}
        ).json()
        self.assertEqual(page['count'], 1)
        self.assertEqual(page['movements'], [])

    def test_an_invalid_filter_is_a_400(self):
        self.client.force_login(self.master)

        response = self.client.get(
            reverse('yard:movement-list'), {'result': 'SOMETHING'}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('result', response.json())

    # --- movement detail ---------------------------------------------------

    def test_movement_detail_reports_composition_findings_and_effects(self):
        self.client.force_login(self.master)

        body = self.client.get(
            reverse('yard:movement-detail', args=[self.movement.pk])
        ).json()

        self.assertEqual(body['device_id'], 'gate-1')
        self.assertEqual(body['plan_code'], 'NDLS-IN-FULL')
        self.assertEqual(body['validation_result'], 'FLAGGED')
        self.assertEqual(body['station']['code'], 'NDLS')

        self.assertEqual(body['observed_counts']['locos'], 1)
        self.assertEqual(body['observed_counts']['coaches'], 2)
        self.assertEqual(body['observed_counts']['distinct_uids'], 3)
        self.assertEqual(body['observed_counts']['unknown_uids'], 0)
        self.assertEqual(body['observed_counts']['passages'], 0)
        self.assertFalse(body['counts_agree'])

        codes = {finding['code'] for finding in body['findings']}
        self.assertIn('COUNT_MISMATCH', codes)
        self.assertIn('ULTRASONIC_COUNT_MISMATCH', codes)

        self.assertEqual(len(body['events']), 3)
        self.assertEqual(len(body['inventory_effects']), 3)
        self.assertEqual(
            {effect['status_after'] for effect in body['inventory_effects']},
            {InventoryRecord.Status.IN_YARD},
        )
        self.assertTrue(
            all(effect['applied_at'] for effect in body['inventory_effects'])
        )

    def test_movement_detail_includes_alerts(self):
        self.client.force_login(self.master)

        body = self.client.get(
            reverse('yard:movement-detail', args=[self.movement.pk])
        ).json()

        self.assertGreaterEqual(len(body['alerts']), 1)
        self.assertIn(
            'COUNT_MISMATCH', {alert['code'] for alert in body['alerts']}
        )

    # --- lazy closure ------------------------------------------------------

    def test_an_idle_open_movement_is_closed_and_validated_by_a_read(self):
        idle = self._closed_movement(
            station=self.station,
            device=self.entry,
            direction=Direction.ENTRY,
            uids=[self.coaches[3].rfid_uid],
        )
        Movement.objects.filter(pk=idle.pk).update(
            state=Movement.State.OPEN,
            closed_at=None,
            started_at=timezone.now() - timedelta(seconds=60),
            last_evidence_at=timezone.now() - timedelta(seconds=60),
        )
        self.client.force_login(self.master)

        response = self.client.get(reverse('yard:movement-list'))

        self.assertEqual(response.status_code, 200)
        idle.refresh_from_db()
        self.assertEqual(idle.state, Movement.State.CLOSED)
        self.assertIsNotNone(idle.validated_at)
        self.assertIsNotNone(idle.validation_result)
        self.assertIn(
            idle.pk, [row['id'] for row in response.json()['movements']]
        )

    def test_lazy_closure_does_not_touch_another_stations_bucket(self):
        idle = timezone.now() - timedelta(seconds=60)
        foreign_open = Movement.objects.create(
            station=self.other_station,
            device=self.foreign_lane,
            direction=Direction.EXIT,
            state=Movement.State.OPEN,
            started_at=idle,
            last_evidence_at=idle,
        )
        self.client.force_login(self.master)

        self.client.get(reverse('yard:movement-list'))

        foreign_open.refresh_from_db()
        self.assertEqual(foreign_open.state, Movement.State.OPEN)

    # --- alerts ------------------------------------------------------------

    def test_acknowledging_an_alert_records_the_operator_and_is_idempotent(self):
        alert = Alert.objects.filter(
            station=self.station, acknowledged_at__isnull=True
        ).first()
        self.assertIsNotNone(alert)
        self.client.force_login(self.master)

        first = self.post_json(
            reverse('yard:alert-acknowledge', args=[alert.pk])
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['acknowledged_by'], 'master')
        acknowledged_at = first.json()['acknowledged_at']
        self.assertIsNotNone(acknowledged_at)

        second = self.post_json(
            reverse('yard:alert-acknowledge', args=[alert.pk])
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['acknowledged_at'], acknowledged_at)
        self.assertEqual(second.json()['acknowledged_by'], 'master')

    def test_alert_filter_separates_acknowledged_from_open(self):
        alert = Alert.objects.filter(
            station=self.station, acknowledged_at__isnull=True
        ).first()
        self.client.force_login(self.master)
        opening = self.client.get(
            reverse('yard:alert-list'), {'acknowledged': 'false'}
        ).json()
        self.assertIn(alert.pk, [row['id'] for row in opening['alerts']])

        self.post_json(reverse('yard:alert-acknowledge', args=[alert.pk]))

        after = self.client.get(
            reverse('yard:alert-list'), {'acknowledged': 'false'}
        ).json()
        self.assertNotIn(alert.pk, [row['id'] for row in after['alerts']])
        acknowledged = self.client.get(
            reverse('yard:alert-list'), {'acknowledged': 'true'}
        ).json()
        self.assertIn(alert.pk, [row['id'] for row in acknowledged['alerts']])

    # --- roles -------------------------------------------------------------

    def test_a_viewer_can_read_but_cannot_acknowledge(self):
        alert = Alert.objects.filter(
            station=self.station, acknowledged_at__isnull=True
        ).first()
        self.client.force_login(self.viewer)

        self.assertEqual(
            self.client.get(reverse('yard:overview')).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('yard:movement-list')).status_code, 200
        )

        response = self.post_json(
            reverse('yard:alert-acknowledge', args=[alert.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'ROLE_NOT_PERMITTED')
        alert.refresh_from_db()
        self.assertIsNone(alert.acknowledged_at)

    # --- inventory ---------------------------------------------------------

    def test_inventory_filters_by_status_and_uid_fragment(self):
        self.client.force_login(self.master)

        verified_only = self.client.get(
            reverse('yard:inventory-list'),
            {'verification': InventoryRecord.Verification.VERIFIED},
        ).json()
        self.assertEqual(verified_only['count'], 0)

        fragment = self.client.get(
            reverse('yard:inventory-list'),
            {'q': self.loco.rfid_uid[:4]},
        ).json()
        self.assertEqual(fragment['count'], 1)
        self.assertEqual(fragment['inventory'][0]['rfid_uid'], self.loco.rfid_uid)

        out_only = self.client.get(
            reverse('yard:inventory-list'),
            {'status': InventoryRecord.Status.OUT},
        ).json()
        self.assertEqual(out_only['count'], 0)

    # --- overview ----------------------------------------------------------

    def test_overview_summarises_the_station(self):
        self.client.force_login(self.master)

        body = self.client.get(reverse('yard:overview')).json()

        self.assertEqual(body['station']['code'], 'NDLS')
        self.assertEqual(body['lanes']['total'], 1)
        self.assertEqual(body['lanes']['active'], 1)
        self.assertEqual(body['lanes']['by_mode']['ENTRY'], 1)
        self.assertEqual(body['inventory']['in_yard'], 3)
        self.assertEqual(body['inventory']['unverified'], 3)
        self.assertGreaterEqual(body['alerts']['unacknowledged'], 1)
        self.assertEqual(body['open_movements'], [])
        self.assertEqual(body['latest_movement']['id'], self.movement.pk)
