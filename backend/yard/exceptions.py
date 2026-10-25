"""Typed API errors for device ingestion.

Every error carries a machine-readable ``code`` because the firmware has to make
retry decisions from it. A 409 ``LANE_MODE_NOT_SET`` is retryable after an
operator acts; a 400 is a firmware bug; a 401/403 is a provisioning problem.
"""

from rest_framework.exceptions import APIException


class DeviceAuthRequired(APIException):
    status_code = 401
    default_detail = 'X-Device-ID and X-Api-Key headers are required.'
    default_code = 'DEVICE_AUTH_REQUIRED'


class DeviceAuthFailed(APIException):
    status_code = 401
    default_detail = 'Device credentials were not accepted.'
    default_code = 'DEVICE_AUTH_FAILED'


class DeviceInactive(APIException):
    status_code = 403
    default_detail = 'This device is disabled.'
    default_code = 'DEVICE_INACTIVE'


class DeviceIdMismatch(APIException):
    status_code = 403
    default_detail = 'Payload device_id does not match the authenticated device.'
    default_code = 'DEVICE_ID_MISMATCH'


class LaneModeNotSet(APIException):
    status_code = 409
    default_detail = (
        'The lane mode is not set. An operator must choose ENTRY or EXIT before '
        'events can be accepted. Retry after the mode is set.'
    )
    default_code = 'LANE_MODE_NOT_SET'


class EventIdDeviceConflict(APIException):
    status_code = 409
    default_detail = 'This event_id was already recorded by a different device.'
    default_code = 'EVENT_ID_DEVICE_MISMATCH'
