"""Station-scoped lane reads and the audited lane-mode control.

A lane is a physical crossing point. Its direction is an operator decision that
is appended to ``LaneModeChange`` with ``set_by``, never inferred from inventory
or from a device payload. These endpoints are the first real consumers of the
session identity, so they also demonstrate the station boundary: another
station's lane is a 404, not a 403.
"""

from rest_framework.response import Response
from rest_framework.views import APIView

from . import clock
from .access import (
    IsStationStaff,
    IsYardOperator,
    get_station_object_or_404,
)
from .exceptions import LaneInactive
from .models import Device
from .movements import close_idle_movements, open_movement_for
from .serializers import (
    LaneDetailSerializer,
    LaneModeChangeSerializer,
    LaneModeSerializer,
    LaneSummarySerializer,
    open_movement_payload,
)
from .services import set_lane_mode


class LaneListView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request):
        lanes = Device.objects.filter(
            station=request.staff_profile.station
        ).order_by('device_id')
        return Response({'lanes': LaneSummarySerializer(lanes, many=True).data})


class LaneDetailView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request, device_id):
        device = get_station_object_or_404(
            Device,
            request.staff_profile.station,
            device_id=device_id,
        )
        return Response(LaneDetailSerializer(device).data)


class LaneModeView(APIView):
    """Change a lane's operating direction, recording who decided it.

    Repeating the current mode is a no-op and writes no audit row: nothing
    changed.

    A change is allowed while a movement is open, exactly as v4 requires: the
    open movement keeps the direction and the ``lane_mode_change`` it was opened
    with, and the new mode applies to the next movement. The response says so
    explicitly, because "I flipped the lane" and "the train I am watching
    flipped" are different facts. An idle bucket is closed first so the response
    reflects the current moment rather than waiting for the sweep.
    """

    permission_classes = (IsYardOperator,)

    def post(self, request, device_id):
        device = get_station_object_or_404(
            Device,
            request.staff_profile.station,
            device_id=device_id,
        )
        if not device.is_active:
            raise LaneInactive()

        serializer = LaneModeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        direction = serializer.validated_data['direction']
        note = serializer.validated_data['note']

        if device.lane_mode == direction:
            return Response(
                _state(
                    device,
                    changed=False,
                    change=None,
                    open_movement=open_movement_for(device),
                )
            )

        # This closes idle buckets on every lane, which is exactly what the
        # periodic sweep would do a few seconds later; doing it here keeps the
        # reported open movement honest without blocking the change.
        close_idle_movements(now=clock.server_now())

        change = set_lane_mode(
            device,
            direction,
            user=request.user,
            note=note,
        )
        return Response(
            _state(
                device,
                changed=True,
                change=change,
                open_movement=open_movement_for(device),
            )
        )


def _state(device, *, changed, change, open_movement):
    return {
        'device_id': device.device_id,
        'lane_mode': device.lane_mode,
        'changed': changed,
        # A mode change never rewrites an open movement; it is an instruction
        # for the next bucket.
        'applies_to': 'NEXT_MOVEMENT' if changed else None,
        'open_movement': open_movement_payload(open_movement),
        'change': (
            LaneModeChangeSerializer(change).data if change is not None else None
        ),
    }
