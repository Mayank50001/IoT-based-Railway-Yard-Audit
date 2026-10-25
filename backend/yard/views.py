"""HTTP surface for device ingestion.

``POST /api/ingest/events`` accepts one event or a small batch. The response is
per event so the firmware can tell a transport retry from a permanently bad
payload without re-reading the whole batch.

Whole-request errors mean "nothing was stored": authentication, envelope shape,
an identity claim that contradicts the authenticated device, or a lane whose
mode has not been set. Per-event errors mean "this event was skipped, the rest
were processed".
"""

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import clock
from .auth import authenticate_device
from .exceptions import DeviceIdMismatch, LaneModeNotSet
from .ingest import ingest_event
from .models import Device
from .movements import is_eligible_direction
from .serializers import EventSerializer, event_error_code, flatten_errors

DEFAULT_MAX_BATCH = 50


def _iso(value):
    return timezone.localtime(value).isoformat() if value else None


def _extract_events(data):
    if not isinstance(data, dict):
        raise ParseError('Send an event object or {"events": [...]}.')

    if 'events' in data and 'event_id' not in data:
        events = data['events']
        if not isinstance(events, list):
            raise ParseError('"events" must be a list of event objects.')
    else:
        events = [data]

    if not events:
        raise ParseError('At least one event is required.')

    max_batch = getattr(settings, 'INGEST_MAX_BATCH_SIZE', DEFAULT_MAX_BATCH)
    if len(events) > max_batch:
        raise ParseError(
            f'A batch may contain at most {max_batch} events; '
            f'received {len(events)}.'
        )
    return events


def _check_device_claims(events, device):
    """Reject a payload that claims to come from another device.

    Checked for the whole envelope before anything is stored, so a spoofed
    batch cannot leave half its events accepted.
    """
    for raw in events:
        if not isinstance(raw, dict):
            continue
        claimed = (raw.get('device_id') or '').strip()
        if claimed and claimed != device.device_id:
            raise DeviceIdMismatch()


def _messages(errors):
    parts = []

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        else:
            parts.append(str(value))

    walk(errors)
    return ' '.join(parts)


def _rejected(event_id, code, message, fields=None):
    error = {'code': code, 'message': message}
    if fields:
        error['fields'] = fields
    return {
        'event_id': event_id or '',
        'status': 'rejected',
        'error': error,
    }


def _ingest_one(device, raw, received_at):
    if not isinstance(raw, dict):
        return _rejected('', 'MALFORMED_EVENT', 'Each event must be a JSON object.')

    serializer = EventSerializer(data=raw)
    if not serializer.is_valid():
        fields = flatten_errors(serializer.errors)
        return _rejected(
            raw.get('event_id'),
            event_error_code(serializer.errors),
            _messages(fields),
            fields,
        )

    return ingest_event(
        device,
        serializer.validated_data,
        received_at,
        raw_payload=raw,
    )


class IngestEventsView(APIView):
    authentication_classes = ()
    permission_classes = ()
    parser_classes = (JSONParser,)

    def post(self, request):
        device = authenticate_device(request)
        events = _extract_events(request.data)
        _check_device_claims(events, device)

        if not is_eligible_direction(device.lane_mode):
            raise LaneModeNotSet()

        received_at = clock.server_now()
        results = [_ingest_one(device, raw, received_at) for raw in events]

        Device.objects.filter(pk=device.pk).update(last_seen_at=received_at)

        summary = {'accepted': 0, 'duplicate': 0, 'rejected': 0}
        for result in results:
            summary[result['status']] += 1

        return Response(
            {
                'device_id': device.device_id,
                'lane_mode': device.lane_mode,
                'received_at': _iso(received_at),
                'accepted': summary['accepted'],
                'duplicates': summary['duplicate'],
                'rejected': summary['rejected'],
                'results': results,
            }
        )
