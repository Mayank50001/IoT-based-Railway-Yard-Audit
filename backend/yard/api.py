"""DRF exception handling with machine-readable codes.

The firmware decides whether to retry an event from the HTTP status plus a
stable code. DRF's default body only carries a human string, so the code is
lifted into the body as well.
"""

from django.http import Http404
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, 'default_code', None) or getattr(exc, 'code', None)

    # DRF converts Http404 into NotFound internally, so the original exception
    # carries no code. Station scoping raises Http404 on purpose; give the
    # dashboard the same stable code a DRF NotFound would have produced.
    if isinstance(exc, Http404):
        code = 'NOT_FOUND'

    if not code:
        return response

    data = response.data
    if isinstance(data, dict) and set(data) == {'detail'}:
        response.data = {'detail': data['detail'], 'code': str(code).upper()}
    elif isinstance(data, list):
        response.data = {'detail': data, 'code': str(code).upper()}

    return response
