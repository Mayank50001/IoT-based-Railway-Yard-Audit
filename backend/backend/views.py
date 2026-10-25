from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    """Liveness probe.

    This is a plain Django view, outside DRF, so the global IsAuthenticated
    permission does not apply. It is intentionally public: a probe must work
    before anyone logs in, and it exposes only configuration and database
    reachability, no yard data.
    """
    configuration = {
        'movement_gap_seconds': settings.MOVEMENT_GAP_SECONDS,
        'validation_policy': settings.VALIDATION_POLICY,
    }

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse(
            {
                **configuration,
                'status': 'error',
                'database': 'unavailable',
            },
            status=503,
        )

    return JsonResponse({
        **configuration,
        'status': 'ok',
        'database': 'connected',
    })