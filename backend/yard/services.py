from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Direction, LaneModeChange


@transaction.atomic
def set_lane_mode(device, direction, *, user=None, note=''):
    """Set a lane's operating direction and record who decided it.

    A lane (device) is a physical crossing point, so one device can serve both
    entry and exit at different times. The direction is not inferred from stock
    state; it is an explicit, audited operator decision.
    """
    if direction not in Direction.values:
        raise ValidationError(
            {'direction': f'Unsupported direction: {direction!r}.'}
        )

    device.lane_mode = direction
    device.save(update_fields=('lane_mode', 'updated_at'))

    return LaneModeChange.objects.create(
        device=device,
        direction=direction,
        set_by=user,
        note=note,
    )
