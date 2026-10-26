"""Tests for the Django-served dashboard.

The dashboard must not become a second, weaker access path. These tests cover
the page-level redirect, the staff-profile denial, station scoping on a movement
page, and the read-only role view. Data rendering is exercised through the API
tests; here the pages are shells.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Device,
    Direction,
    Movement,
    StaffProfile,
    Station,
)

User = get_user_model()
PASSWORD = 'demo-pass-123'


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class DashboardTests(TestCase):
    def setUp(self):
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

        self.lane = Device.objects.create(
            device_id='gate-1',
            label='Gate 1',
            station=self.station,
            lane_mode=Direction.ENTRY,
            api_key_hash='not-used-in-these-tests',
        )
        self.other_lane = Device.objects.create(
            device_id='gate-9',
            station=self.other_station,
            lane_mode=Direction.EXIT,
            api_key_hash='not-used-in-these-tests',
        )

        now = timezone.now()
        self.movement = Movement.objects.create(
            station=self.station,
            device=self.lane,
            direction=Direction.ENTRY,
            state=Movement.State.CLOSED,
            started_at=now - timedelta(minutes=2),
            last_evidence_at=now - timedelta(minutes=1),
            closed_at=now,
        )
        self.other_movement = Movement.objects.create(
            station=self.other_station,
            device=self.other_lane,
            direction=Direction.EXIT,
            state=Movement.State.CLOSED,
            started_at=now - timedelta(minutes=4),
            last_evidence_at=now - timedelta(minutes=3),
            closed_at=now - timedelta(minutes=2),
        )

    def _user(self, username, station, role):
        user = User.objects.create_user(username=username, password=PASSWORD)
        StaffProfile.objects.create(user=user, station=station, role=role)
        return user

    def test_anonymous_overview_redirects_to_login_with_next(self):
        response = self.client.get(reverse('dashboard:overview'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('dashboard:login')}?next=%2F",
        )

    def test_anonymous_movement_page_keeps_full_path_in_next(self):
        response = self.client.get(
            reverse('dashboard:movement', args=[self.movement.pk])
        )

        self.assertEqual(response.status_code, 302)
        encoded = f"%2Fmovements%2F{self.movement.pk}%2F"
        self.assertEqual(
            response.url,
            f"{reverse('dashboard:login')}?next={encoded}",
        )

    def test_login_page_renders_form_and_csrf_shell(self):
        response = self.client.get(reverse('dashboard:login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="login-form"')
        self.assertContains(response, 'name="csrf-token"')
        self.assertEqual(response.context['next'], '/')

    def test_login_page_keeps_next_target(self):
        response = self.client.get(
            reverse('dashboard:login') + '?next=/movements/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-next="/movements/"')

    def test_authenticated_login_page_redirects_to_overview(self):
        self.client.force_login(self.master)

        response = self.client.get(reverse('dashboard:login'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:overview'))

    def test_overview_renders_station_and_operator_identity(self):
        self.client.force_login(self.master)

        response = self.client.get(reverse('dashboard:overview'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'NDLS')
        self.assertContains(response, 'New Delhi')
        self.assertContains(response, 'Yard master')
        self.assertContains(response, 'data-page="overview"')
        self.assertContains(response, 'data-can-write="true"')

    def test_viewer_pages_are_read_only(self):
        self.client.force_login(self.viewer)

        for name in ('lanes', 'movements', 'inventory', 'alerts'):
            with self.subTest(page=name):
                response = self.client.get(reverse(f'dashboard:{name}'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-can-write="false"')

    def test_account_without_staff_profile_gets_explained_403(self):
        user = User.objects.create_user('orphan', password=PASSWORD)
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard:overview'))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'NO_STAFF_PROFILE', status_code=403)
        self.assertContains(response, 'no station staff profile', status_code=403)

    def test_inactive_profile_gets_explained_403(self):
        self.viewer.staff_profile.is_active = False
        self.viewer.staff_profile.save(update_fields=('is_active',))
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('dashboard:overview'))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'STAFF_INACTIVE', status_code=403)

    def test_inactive_station_gets_explained_403(self):
        self.station.is_active = False
        self.station.save(update_fields=('is_active',))
        self.client.force_login(self.master)

        response = self.client.get(reverse('dashboard:overview'))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'STATION_INACTIVE', status_code=403)

    def test_movement_page_loads_own_station_movement(self):
        self.client.force_login(self.master)

        response = self.client.get(
            reverse('dashboard:movement', args=[self.movement.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['movement'], self.movement)
        self.assertContains(
            response, f'data-movement-id="{self.movement.pk}"'
        )

    def test_movement_page_hides_another_station_movement(self):
        self.client.force_login(self.outsider)

        response = self.client.get(
            reverse('dashboard:movement', args=[self.movement.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_logout_requires_post_and_ends_the_session(self):
        self.client.force_login(self.master)

        self.assertEqual(
            self.client.get(reverse('dashboard:logout')).status_code,
            405,
        )

        response = self.client.post(reverse('dashboard:logout'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:login'))

        after = self.client.get(reverse('dashboard:overview'))
        self.assertEqual(after.status_code, 302)

    def test_movement_api_exposes_direction_source(self):
        self.client.force_login(self.master)

        response = self.client.get(
            f"/api/movements/{self.movement.pk}/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['direction_source'], 'OPERATOR')

    def test_dashboard_assets_are_discoverable(self):
        self.assertIsNotNone(finders.find('yard/dashboard.js'))
        self.assertIsNotNone(finders.find('yard/dashboard.css'))
