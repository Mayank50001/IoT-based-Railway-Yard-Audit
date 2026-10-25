"""Tests for yard-master session authentication, scoping, and lane control.

The theme is separation:

* device credentials do not authenticate a human, and a session does not
  authenticate a device;
* an account sees exactly one station, and another station's lane is a 404;
* a viewer can read but not write;
* every lane-mode change records the operator who made it.
"""

import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import staff_profile_for, station_for
from .exceptions import NoStaffProfile, StaffInactive
from .models import (
    Device,
    Direction,
    LaneModeChange,
    Movement,
    StaffProfile,
    Station,
)
from .movements import assign_event_to_movement, close_movement

User = get_user_model()

PASSWORD = 'demo-pass-123'


class CsrfEnforcingClient(Client):
    """The default test client switches CSRF off; these tests must not.

    Django's test client sets ``_dont_enforce_csrf_checks`` unless asked
    otherwise, and DRF's ``SessionAuthentication`` honours that flag. Without
    this, every CSRF negative test would silently pass the request through.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('enforce_csrf_checks', True)
        super().__init__(*args, **kwargs)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class AuthTestCase(TestCase):
    client_class = CsrfEnforcingClient

    def setUp(self):
        cache.clear()

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

        self.lane = self._lane('gate-1', self.station, Direction.ENTRY)
        self.other_lane = self._lane(
            'gate-9', self.other_station, Direction.EXIT
        )

    # --- fixtures ----------------------------------------------------------

    def _user(self, username, station, role):
        user = User.objects.create_user(username=username, password=PASSWORD)
        StaffProfile.objects.create(user=user, station=station, role=role)
        return user

    def _lane(self, device_id, station, direction):
        device = Device(
            device_id=device_id,
            station=station,
            lane_mode=direction,
        )
        device.set_api_key(f'key-{device_id}')
        device.save()
        LaneModeChange.objects.create(
            device=device,
            direction=direction,
            note='test setup',
        )
        return device

    # --- helpers -----------------------------------------------------------

    def csrf_token(self):
        response = self.client.get(reverse('yard:auth-csrf'))
        self.assertEqual(response.status_code, 200)
        return response.json()['csrfToken']

    def login(self, username='master', password=PASSWORD):
        token = self.csrf_token()
        return self.client.post(
            reverse('yard:auth-login'),
            data=json.dumps({'username': username, 'password': password}),
            content_type='application/json',
            headers={'X-CSRFToken': token},
        )

    def post_json(self, url, payload):
        token = self.csrf_token()
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': token},
        )

    def change_mode(self, device_id='gate-1', direction='EXIT', note=''):
        return self.post_json(
            reverse('yard:lane-mode', args=[device_id]),
            {'direction': direction, 'note': note},
        )

    def open_movement(self, device=None, direction=Direction.ENTRY, idle_for=0):
        device = device or self.lane
        at = timezone.now() - timedelta(seconds=idle_for)
        return Movement.objects.create(
            station=device.station,
            device=device,
            direction=direction,
            state=Movement.State.OPEN,
            started_at=at,
            last_evidence_at=at,
        )

    # --- public surfaces ---------------------------------------------------

    def test_health_stays_public(self):
        response = self.client.get(reverse('health'))
        self.assertEqual(response.status_code, 200)

    def test_a_session_does_not_authenticate_ingestion(self):
        self.client.force_login(self.master)

        response = self.client.post(
            reverse('yard:ingest-events'),
            data=json.dumps({'events': []}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'DEVICE_AUTH_REQUIRED')

    def test_a_device_key_does_not_authenticate_the_dashboard(self):
        response = self.client.get(
            reverse('yard:lane-list'),
            headers={
                'X-Device-ID': 'gate-1',
                'X-Api-Key': 'key-gate-1',
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'NOT_AUTHENTICATED')

    def test_dashboard_reads_require_a_session(self):
        response = self.client.get(reverse('yard:lane-list'))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'NOT_AUTHENTICATED')

    # --- login -------------------------------------------------------------

    def test_login_sets_a_session_and_returns_the_bound_station(self):
        response = self.login()

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['user']['username'], 'master')
        self.assertEqual(body['station']['code'], 'NDLS')
        self.assertEqual(body['role'], StaffProfile.Role.YARD_MASTER)
        self.assertTrue(body['can_write'])

        me = self.client.get(reverse('yard:auth-me'))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['station']['code'], 'NDLS')

    def test_login_rejects_a_wrong_password(self):
        response = self.login(password='not-the-password')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'INVALID_CREDENTIALS')
        self.assertEqual(
            self.client.get(reverse('yard:auth-me')).status_code, 401
        )

    def test_login_rejects_a_deactivated_django_user(self):
        self.master.is_active = False
        self.master.save(update_fields=('is_active',))

        response = self.login()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'INVALID_CREDENTIALS')

    def test_login_requires_a_csrf_token(self):
        response = self.client.post(
            reverse('yard:auth-login'),
            data=json.dumps(
                {'username': 'master', 'password': PASSWORD}
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'CSRF_FAILED')

    def test_login_without_a_staff_profile_creates_no_session(self):
        User.objects.create_user(username='orphan', password=PASSWORD)

        response = self.login(username='orphan')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'NO_STAFF_PROFILE')
        self.assertEqual(
            self.client.get(reverse('yard:auth-me')).status_code, 401
        )

    def test_login_with_an_inactive_profile_is_refused(self):
        self.master.staff_profile.is_active = False
        self.master.staff_profile.save(
            update_fields=('is_active', 'updated_at')
        )

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'STAFF_INACTIVE')

    def test_login_to_an_inactive_station_is_refused(self):
        self.station.is_active = False
        self.station.save(update_fields=('is_active', 'updated_at'))

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'STATION_INACTIVE')

    @override_settings(LOGIN_THROTTLE_RATE='2/min')
    def test_repeated_login_attempts_are_throttled(self):
        self.assertEqual(self.login(password='wrong').status_code, 401)
        self.assertEqual(self.login(password='wrong').status_code, 401)

        response = self.login(password='wrong')

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['code'], 'THROTTLED')

    @override_settings(LOGIN_THROTTLE_RATE='1/min')
    def test_a_spoofed_forwarded_for_header_does_not_reset_the_budget(self):
        self.assertEqual(self.login(password='wrong').status_code, 401)

        token = self.csrf_token()
        response = self.client.post(
            reverse('yard:auth-login'),
            data=json.dumps(
                {'username': 'master', 'password': 'wrong'}
            ),
            content_type='application/json',
            headers={
                'X-CSRFToken': token,
                'X-Forwarded-For': '203.0.113.99',
            },
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['code'], 'THROTTLED')

    # --- logout ------------------------------------------------------------

    def test_logout_ends_the_session(self):
        self.login()

        response = self.post_json(reverse('yard:auth-logout'), {})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.client.get(reverse('yard:auth-me')).status_code, 401
        )

    def test_logout_requires_a_csrf_token(self):
        self.login()

        response = self.client.post(
            reverse('yard:auth-logout'),
            data='{}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'CSRF_FAILED')

    # --- me and profile errors --------------------------------------------

    def test_me_requires_a_staff_profile(self):
        orphan = User.objects.create_user(
            username='orphan', password=PASSWORD
        )
        self.client.force_login(orphan)

        response = self.client.get(reverse('yard:auth-me'))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'NO_STAFF_PROFILE')

    def test_an_inactive_profile_cannot_read(self):
        self.master.staff_profile.is_active = False
        self.master.staff_profile.save(
            update_fields=('is_active', 'updated_at')
        )
        self.client.force_login(self.master)

        response = self.client.get(reverse('yard:lane-list'))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'STAFF_INACTIVE')

    def test_station_for_is_the_scoping_primitive(self):
        self.assertEqual(station_for(self.master), self.station)

        orphan = User.objects.create_user(
            username='orphan2', password=PASSWORD
        )
        with self.assertRaises(NoStaffProfile):
            station_for(orphan)

    def test_staff_profile_for_rejects_an_inactive_user_directly(self):
        self.master.is_active = False
        self.master.save(update_fields=('is_active',))

        with self.assertRaises(StaffInactive):
            staff_profile_for(self.master)

    # --- station scoping ---------------------------------------------------

    def test_lane_list_contains_only_the_users_station(self):
        self.client.force_login(self.master)

        response = self.client.get(reverse('yard:lane-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [lane['device_id'] for lane in response.json()['lanes']],
            ['gate-1'],
        )

    def test_another_stations_lane_is_indistinguishable_from_a_missing_one(self):
        self.client.force_login(self.master)

        foreign = self.client.get(
            reverse('yard:lane-detail', args=['gate-9'])
        )
        missing = self.client.get(
            reverse('yard:lane-detail', args=['gate-does-not-exist'])
        )

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(foreign.json(), missing.json())
        self.assertEqual(foreign.json()['code'], 'NOT_FOUND')

    def test_another_stations_lane_cannot_be_mode_changed(self):
        self.client.force_login(self.outsider)

        response = self.change_mode(device_id='gate-1', direction='EXIT')

        self.assertEqual(response.status_code, 404)
        self.lane.refresh_from_db()
        self.assertEqual(self.lane.lane_mode, Direction.ENTRY)

    def test_lane_detail_never_exposes_the_device_key(self):
        self.client.force_login(self.master)

        response = self.client.get(
            reverse('yard:lane-detail', args=['gate-1'])
        )

        body = response.content.decode().lower()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('api_key', body)
        self.assertNotIn(self.lane.api_key_hash.lower(), body)

    # --- roles -------------------------------------------------------------

    def test_a_viewer_can_read_lanes(self):
        self.client.force_login(self.viewer)

        self.assertEqual(
            self.client.get(reverse('yard:lane-list')).status_code, 200
        )
        self.assertEqual(
            self.client.get(
                reverse('yard:lane-detail', args=['gate-1'])
            ).status_code,
            200,
        )

    def test_a_viewer_cannot_change_a_lane_mode(self):
        self.client.force_login(self.viewer)

        response = self.change_mode(direction='EXIT')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'ROLE_NOT_PERMITTED')
        self.lane.refresh_from_db()
        self.assertEqual(self.lane.lane_mode, Direction.ENTRY)

    def test_a_master_can_change_a_lane_mode_and_the_operator_is_recorded(self):
        self.login()

        response = self.change_mode(direction='EXIT', note='evening departure')

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body['changed'])
        self.assertEqual(body['lane_mode'], Direction.EXIT)
        self.assertEqual(body['change']['set_by'], 'master')

        self.lane.refresh_from_db()
        self.assertEqual(self.lane.lane_mode, Direction.EXIT)

        change = LaneModeChange.objects.filter(
            device=self.lane
        ).order_by('-id').first()
        self.assertEqual(change.direction, Direction.EXIT)
        self.assertEqual(change.set_by, self.master)
        self.assertEqual(change.note, 'evening departure')

        detail = self.client.get(
            reverse('yard:lane-detail', args=['gate-1'])
        ).json()
        self.assertEqual(detail['recent_mode_changes'][0]['set_by'], 'master')

    def test_repeating_the_current_mode_is_a_noop_without_an_audit_row(self):
        self.login()
        before = LaneModeChange.objects.filter(device=self.lane).count()

        response = self.change_mode(direction='ENTRY')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['changed'])
        self.assertIsNone(response.json()['change'])
        self.assertEqual(
            LaneModeChange.objects.filter(device=self.lane).count(), before
        )

    # --- lane-mode state rules --------------------------------------------

    def test_a_mode_change_while_a_movement_is_open_applies_to_the_next_one(self):
        movement = self.open_movement(direction=Direction.ENTRY, idle_for=0)
        self.login()

        response = self.change_mode(direction='EXIT')

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body['changed'])
        self.assertEqual(body['applies_to'], 'NEXT_MOVEMENT')
        self.assertEqual(body['open_movement']['id'], movement.pk)
        self.assertEqual(body['open_movement']['direction'], Direction.ENTRY)

        # The live movement keeps the direction and the authorising change it
        # was opened with; nothing rewrites history.
        movement.refresh_from_db()
        self.assertEqual(movement.state, Movement.State.OPEN)
        self.assertEqual(movement.direction, Direction.ENTRY)

        # The next bucket, opened after this one closes, uses the new mode.
        self.lane.refresh_from_db()
        close_movement(movement, closed_at=timezone.now())
        next_movement, created = assign_event_to_movement(
            self.lane, timezone.now()
        )
        self.assertTrue(created)
        self.assertEqual(next_movement.direction, Direction.EXIT)

    def test_an_idle_movement_is_closed_before_the_mode_changes(self):
        movement = self.open_movement(direction=Direction.ENTRY, idle_for=30)
        self.login()

        response = self.change_mode(direction='EXIT')

        self.assertEqual(response.status_code, 200, response.content)
        movement.refresh_from_db()
        self.assertEqual(movement.state, Movement.State.CLOSED)
        self.lane.refresh_from_db()
        self.assertEqual(self.lane.lane_mode, Direction.EXIT)

    def test_a_disabled_lane_cannot_change_mode(self):
        self.lane.is_active = False
        self.lane.save(update_fields=('is_active', 'updated_at'))
        self.login()

        response = self.change_mode(direction='EXIT')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'LANE_INACTIVE')

    def test_an_invalid_direction_is_rejected(self):
        self.login()

        response = self.change_mode(direction='SIDEWAYS')

        self.assertEqual(response.status_code, 400)
        self.assertIn('direction', response.json())

    def test_a_mode_change_requires_csrf(self):
        self.client.force_login(self.master)

        response = self.client.post(
            reverse('yard:lane-mode', args=['gate-1']),
            data=json.dumps({'direction': 'EXIT'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'CSRF_FAILED')
        self.lane.refresh_from_db()
        self.assertEqual(self.lane.lane_mode, Direction.ENTRY)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class CreateStaffCommandTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(code='NDLS', name='New Delhi')

    def create(self, *args):
        return call_command(
            'create_staff',
            '--station',
            'NDLS',
            '--noinput',
            *args,
            stdout=StringIO(),
        )

    def test_creates_a_station_bound_dashboard_account(self):
        self.create(
            '--username',
            'alice',
            '--password',
            PASSWORD,
            '--role',
            StaffProfile.Role.YARD_MASTER,
        )

        user = User.objects.get(username='alice')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        profile = user.staff_profile
        self.assertEqual(profile.station, self.station)
        self.assertEqual(profile.role, StaffProfile.Role.YARD_MASTER)
        self.assertTrue(profile.is_active)

    def test_rerunning_updates_the_profile_without_duplicating_it(self):
        self.create(
            '--username',
            'alice',
            '--password',
            PASSWORD,
            '--role',
            StaffProfile.Role.VIEWER,
        )
        self.create('--username', 'alice', '--role', StaffProfile.Role.ADMIN)

        user = User.objects.get(username='alice')
        self.assertEqual(StaffProfile.objects.filter(user=user).count(), 1)
        self.assertEqual(user.staff_profile.role, StaffProfile.Role.ADMIN)
        self.assertTrue(user.check_password(PASSWORD))

    def test_an_unknown_station_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command(
                'create_staff',
                '--username',
                'alice',
                '--station',
                'NOPE',
                '--password',
                PASSWORD,
                '--noinput',
                verbosity=0,
            )

    def test_a_new_account_without_a_password_is_rejected_when_noninteractive(self):
        with self.assertRaises(CommandError):
            call_command(
                'create_staff',
                '--username',
                'alice',
                '--station',
                'NDLS',
                '--noinput',
                verbosity=0,
            )

    def test_a_weak_password_is_rejected(self):
        with self.assertRaises(CommandError):
            self.create('--username', 'alice', '--password', '12345678')

    def test_the_admin_role_does_not_grant_the_django_admin_site(self):
        self.create(
            '--username',
            'alice',
            '--password',
            PASSWORD,
            '--role',
            StaffProfile.Role.ADMIN,
        )

        user = User.objects.get(username='alice')
        self.assertEqual(user.staff_profile.role, StaffProfile.Role.ADMIN)
        self.assertFalse(user.is_staff)
