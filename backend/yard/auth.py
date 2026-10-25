"""Device-key authentication for ingestion.

Device keys are stored with Django's password hasher (PBKDF2 by default), which
costs roughly a second per verification on the prototype machine. A batch of
events must not pay that cost per event, so a successful verification is cached
for a short period.

The cache entry holds the device primary key and a fingerprint of the stored
hash. Rotating the key changes ``api_key_hash``, which invalidates the entry
immediately without needing an explicit cache purge.

Device identity is deliberately separate from yard-master (session) identity.
"""

import hashlib

from django.conf import settings
from django.core.cache import cache

from .exceptions import DeviceAuthFailed, DeviceAuthRequired, DeviceInactive
from .models import Device

DEVICE_ID_HEADER = 'X-Device-ID'
DEVICE_KEY_HEADER = 'X-Api-Key'

DEFAULT_CACHE_SECONDS = 60


def _cache_key(device_id, raw_key):
    digest = hashlib.sha256(
        f'{device_id}\x00{raw_key}'.encode('utf-8')
    ).hexdigest()
    return f'yard:device-auth:{digest}'


def authenticate_device(request):
    """Return the active ``Device`` for this request or raise a typed error."""
    device_id = (request.headers.get(DEVICE_ID_HEADER) or '').strip()
    raw_key = (request.headers.get(DEVICE_KEY_HEADER) or '').strip()

    if not device_id or not raw_key:
        raise DeviceAuthRequired()

    cache_key = _cache_key(device_id, raw_key)
    cached = cache.get(cache_key)

    device = None
    if cached:
        device = Device.objects.filter(pk=cached['pk']).first()
        if device is not None and device.api_key_hash != cached['hash']:
            # Rotated key: the fingerprint no longer matches, verify for real.
            device = None

    if device is None:
        device = Device.objects.filter(device_id=device_id).first()
        if device is None or not device.check_api_key(raw_key):
            raise DeviceAuthFailed()

        ttl = getattr(settings, 'DEVICE_AUTH_CACHE_SECONDS', DEFAULT_CACHE_SECONDS)
        if ttl:
            cache.set(
                cache_key,
                {'pk': device.pk, 'hash': device.api_key_hash},
                ttl,
            )

    if not device.is_active:
        raise DeviceInactive()

    return device
