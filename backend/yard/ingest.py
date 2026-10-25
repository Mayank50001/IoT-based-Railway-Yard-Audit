"""Transactional event ingestion.

One event is one transaction. An accepted event is stored and assigned to a
movement bucket atomically; a rejected event stores nothing. Transport retries
are resolved by ``event_id`` before any storage happens, and the unique index is
the final arbiter when two identical requests race.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import EventIdDeviceConflict
from .models import MovementEvent, RollingStock
from .movements import assign_event_to_movement


def ingest_event(device, attrs, received_at, raw_payload=None):
    """Persist one validated event and return its per-event result."""
    event_id = attrs['event_id']

    existing = (
        MovementEvent.objects.filter(event_id=event_id)
        .select_related('movement', 'rolling_stock')
        .first()
    )
    if existing is not None:
        return _duplicate_result(existing, device)

    stock = None
    uid = attrs.get('rfid_uid') or ''
    if uid:
        stock = RollingStock.objects.filter(rfid_uid=uid).first()

    metadata = attrs.get('metadata') or {}

    last_error = None
    for _attempt in range(2):
        try:
            with transaction.atomic():
                movement, movement_created = assign_event_to_movement(
                    device,
                    received_at,
                )
                event = MovementEvent.objects.create(
                    event_id=event_id,
                    movement=movement,
                    device=device,
                    kind=attrs['event_type'],
                    rfid_uid=uid,
                    rolling_stock=stock,
                    sequence=attrs.get('sequence'),
                    device_timestamp=attrs.get('timestamp'),
                    received_at=received_at,
                    passage_started_at=metadata.get('started_at'),
                    passage_ended_at=metadata.get('ended_at'),
                    passage_duration_ms=metadata.get('duration_ms'),
                    raw_payload=raw_payload if raw_payload is not None else {},
                )
        except IntegrityError as exc:
            last_error = exc
            # Two races are possible here: the same event_id was stored by a
            # concurrent retry, or another request opened the lane's movement
            # first. A duplicate is answered directly; otherwise the second
            # attempt sees the committed movement and joins it.
            existing = (
                MovementEvent.objects.filter(event_id=event_id)
                .select_related('movement', 'rolling_stock')
                .first()
            )
            if existing is not None:
                return _duplicate_result(existing, device)
            continue
        else:
            last_error = None
            break

    if last_error is not None:
        raise last_error

    return {
        'event_id': event.event_id,
        'status': 'accepted',
        'kind': event.kind,
        'rfid_uid': event.rfid_uid,
        'rolling_stock': stock.number if stock else None,
        'movement_id': movement.pk,
        'movement_created': movement_created,
        'received_at': _iso(received_at),
    }


def _duplicate_result(event, device):
    if event.device_id != device.pk:
        raise EventIdDeviceConflict()

    return {
        'event_id': event.event_id,
        'status': 'duplicate',
        'kind': event.kind,
        'rfid_uid': event.rfid_uid,
        'rolling_stock': (
            event.rolling_stock.number if event.rolling_stock_id else None
        ),
        'movement_id': event.movement_id,
        'received_at': _iso(event.received_at),
    }


def _iso(value):
    return timezone.localtime(value).isoformat() if value else None
