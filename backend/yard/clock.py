"""The server-side ingestion clock.

Server receive time is authoritative for movement segmentation and closure. It
is isolated in one function so tests can control it without sleeping and so a
future deployment can swap in a different clock source if needed.
"""

from django.utils import timezone


def server_now():
    return timezone.now()
