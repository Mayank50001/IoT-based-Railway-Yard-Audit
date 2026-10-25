"""Movement buckets and closure.

A movement is one consist passing one lane. Buckets are separated by server
receive time, not by device clocks: a gap of more than ``MOVEMENT_GAP_SECONDS``
of silence starts a new movement.

Only closure *mechanics* live here. Validation, findings and inventory effects
belong to the validation service, which runs at closure. Until that service
exists (CP-06), a closed movement keeps ``validation_result = NULL`` and carries
an INFO ``VALIDATION_PENDING`` finding so the gap is visible rather than
silently treated as verified.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction

from . import clock
from .exceptions import LaneModeNotSet
from .models import Device, Direction, Movement, MovementFinding

VALIDATION_PENDING = 'VALIDATION_PENDING'


def gap_seconds():
    return getattr(settings, 'MOVEMENT_GAP_SECONDS', 20)


def is_eligible_direction(value):
    return value in Direction.values


@transaction.atomic
def assign_event_to_movement(device, received_at):
    """Return ``(movement, created)`` for an event received at ``received_at``.

    The device row is locked so two concurrent batches on the same lane
    serialise. SQLite ignores ``SELECT ... FOR UPDATE``, but its writers are
    serialised and the partial unique index blocks a second open movement; the
    ingestion service retries once if a racing request won that insert.
    """
    Device.objects.select_for_update().get(pk=device.pk)

    movement = (
        Movement.objects.select_for_update()
        .filter(device=device, state=Movement.State.OPEN)
        .order_by('-started_at', '-id')
        .first()
    )
    created = False

    if movement is None:
        movement = _open_movement(device, received_at)
        created = True
    elif _is_idle(movement, received_at):
        close_movement(movement, closed_at=movement.last_evidence_at)
        movement = _open_movement(device, received_at)
        created = True

    # A backlog or clock correction can deliver an event older than the last
    # evidence. Evidence time must never move backwards.
    if received_at > movement.last_evidence_at:
        movement.last_evidence_at = received_at
        movement.save(update_fields=('last_evidence_at', 'updated_at'))

    return movement, created


def _is_idle(movement, received_at):
    return received_at - movement.last_evidence_at > timedelta(seconds=gap_seconds())


def _open_movement(device, received_at):
    if not is_eligible_direction(device.lane_mode):
        # Ingestion checks this before storing anything; this is a guard for
        # any other caller.
        raise LaneModeNotSet()

    change = (
        device.lane_mode_changes.filter(direction=device.lane_mode)
        .order_by('-changed_at', '-id')
        .first()
    )

    return Movement.objects.create(
        station=device.station,
        device=device,
        direction=device.lane_mode,
        direction_source=Movement.DirectionSource.OPERATOR,
        lane_mode_change=change,
        state=Movement.State.OPEN,
        started_at=received_at,
        last_evidence_at=received_at,
    )


def close_movement(movement, closed_at=None):
    """Close an open movement exactly once. Returns True if this call did it.

    The state change is a conditional SQL update, so two sweeps (or a sweep and
    an ingestion) racing on the same movement cannot both apply it or duplicate
    the closure finding.
    """
    closed_at = closed_at or clock.server_now()

    updated = Movement.objects.filter(
        pk=movement.pk,
        state=Movement.State.OPEN,
    ).update(
        state=Movement.State.CLOSED,
        closed_at=closed_at,
        updated_at=clock.server_now(),
    )

    if not updated:
        movement.state = Movement.State.CLOSED
        return False

    movement.state = Movement.State.CLOSED
    movement.closed_at = closed_at

    MovementFinding.objects.get_or_create(
        movement=movement,
        code=VALIDATION_PENDING,
        subject='',
        defaults={
            'severity': MovementFinding.Severity.INFO,
            'message': (
                'Movement closed. RFID-primary validation is not implemented '
                'yet; this finding is replaced when the movement is validated.'
            ),
        },
    )
    return True


def close_idle_movements(now=None):
    """Close every movement whose last evidence is older than the gap."""
    now = now or clock.server_now()
    cutoff = now - timedelta(seconds=gap_seconds())

    candidates = Movement.objects.filter(
        state=Movement.State.OPEN,
        last_evidence_at__lt=cutoff,
    ).select_related('device')

    closed = 0
    with transaction.atomic():
        for movement in candidates.iterator():
            if close_movement(movement, closed_at=movement.last_evidence_at):
                closed += 1
    return closed


def open_movement_for(device):
    return (
        Movement.objects.filter(device=device, state=Movement.State.OPEN)
        .order_by('-started_at', '-id')
        .first()
    )
