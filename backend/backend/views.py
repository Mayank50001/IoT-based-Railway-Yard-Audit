from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(request):
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