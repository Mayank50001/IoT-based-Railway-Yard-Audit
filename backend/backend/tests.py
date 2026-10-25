from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    MOVEMENT_GAP_SECONDS=20,
    VALIDATION_POLICY='RFID_PRIMARY_COUNTS_V4',
)
class HealthTests(TestCase):
    def test_healthy_database(self):
        response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'status': 'ok',
            'database': 'connected',
            'movement_gap_seconds': 20,
            'validation_policy': 'RFID_PRIMARY_COUNTS_V4',
        })

    def test_database_failure_returns_503(self):
        with patch(
            'backend.views.connection.cursor',
            side_effect=DatabaseError('Database unavailable'),
        ):
            response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['database'], 'unavailable')

    def test_only_get_is_allowed(self):
        response = self.client.post(reverse('health'))

        self.assertEqual(response.status_code, 405)