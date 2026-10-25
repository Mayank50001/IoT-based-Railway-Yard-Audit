"""Typed API errors with stable, machine-readable codes.

The firmware decides whether to retry an event from the HTTP status plus a
stable code, and the dashboard decides what to show from the same code. A 409
``LANE_MODE_NOT_SET`` is retryable after an operator acts; a 400 is a firmware
bug; a 401/403 is a credentials or permissions problem.

Device (firmware) errors and yard-master (session) errors are deliberately
separate: a dashboard account can never authenticate ingestion, and a device
key can never authenticate a dashboard request.
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


# --- Yard-master session errors ---------------------------------------------


class InvalidCredentials(APIException):
    status_code = 401
    default_detail = 'Username or password was not accepted.'
    default_code = 'INVALID_CREDENTIALS'


class CsrfFailed(APIException):
    status_code = 403
    default_detail = (
        'CSRF verification failed. Fetch /api/auth/csrf again and repeat the '
        'request with the X-CSRFToken header.'
    )
    default_code = 'CSRF_FAILED'


class NoStaffProfile(APIException):
    status_code = 403
    default_detail = (
        'This account has no station staff profile. Ask an administrator to '
        'create one before signing in.'
    )
    default_code = 'NO_STAFF_PROFILE'


class StaffInactive(APIException):
    status_code = 403
    default_detail = 'This staff account is inactive.'
    default_code = 'STAFF_INACTIVE'


class StationInactive(APIException):
    status_code = 403
    default_detail = "This account's station is inactive."
    default_code = 'STATION_INACTIVE'


class RoleNotPermitted(APIException):
    status_code = 403
    default_detail = "This account's role does not allow that action."
    default_code = 'ROLE_NOT_PERMITTED'


# --- Lane errors -------------------------------------------------------------


class LaneInactive(APIException):
    status_code = 409
    default_detail = 'This lane is disabled; enable it before changing its mode.'
    default_code = 'LANE_INACTIVE'
