"""Station-scoped read APIs for the dashboard and history views.

Every query is filtered by the session's station; a row from another station is
a 404 through ``get_station_object_or_404``. The dashboard reads stored
findings, inventory, and alerts rather than recomputing business rules.

Movement reads close idle buckets first. The ``sweep_movements`` command is the
scheduled path, but a demo may run without a scheduler, and a movement that
stays ``OPEN`` minutes after its last event would make the dashboard lie. The
cost is a write inside some GETs; that is an accepted prototype trade-off,
recorded in the implementation log.
"""

from django.db.models import Count
from rest_framework.response import Response
from rest_framework.views import APIView

from . import clock
from .access import (
    IsStationStaff,
    IsYardOperator,
    get_station_object_or_404,
)
from .models import Alert, Device, InventoryRecord, Movement, MovementFinding
from .movements import close_idle_movements
from .serializers import (
    AlertFilterSerializer,
    AlertSerializer,
    FindingFilterSerializer,
    InventoryFilterSerializer,
    InventoryRecordSerializer,
    MovementDetailSerializer,
    MovementFilterSerializer,
    MovementFindingSerializer,
    MovementSummarySerializer,
)


def settle(station):
    """Close this station's buckets whose evidence is already older than the gap."""
    close_idle_movements(now=clock.server_now(), station=station)


def _page(serializer_class, request):
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _paginated(queryset, data, serializer_class, key):
    total = queryset.count()
    rows = queryset[data['offset']:data['offset'] + data['limit']]
    return {
        'count': total,
        'limit': data['limit'],
        'offset': data['offset'],
        key: serializer_class(rows, many=True).data,
    }


class MovementListView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request):
        data = _page(MovementFilterSerializer, request)
        settle(request.staff_profile.station)

        movements = Movement.objects.filter(
            station=request.staff_profile.station
        ).select_related('device', 'plan')

        if data.get('state'):
            movements = movements.filter(state=data['state'])
        if data.get('direction'):
            movements = movements.filter(direction=data['direction'])
        if data.get('result'):
            movements = movements.filter(validation_result=data['result'])
        if data.get('device_id'):
            movements = movements.filter(device__device_id=data['device_id'])

        return Response(
            _paginated(
                movements.order_by('-started_at', '-id'),
                data,
                MovementSummarySerializer,
                'movements',
            )
        )


class MovementDetailView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request, pk):
        settle(request.staff_profile.station)
        movement = get_station_object_or_404(
            Movement,
            request.staff_profile.station,
            pk=pk,
        )
        return Response(MovementDetailSerializer(movement).data)


class FindingListView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request):
        data = _page(FindingFilterSerializer, request)

        findings = MovementFinding.objects.filter(
            movement__station=request.staff_profile.station
        ).select_related('movement')

        if data.get('severity'):
            findings = findings.filter(severity=data['severity'])
        if data.get('code'):
            findings = findings.filter(code=data['code'])
        if data.get('movement'):
            findings = findings.filter(movement_id=data['movement'])

        return Response(
            _paginated(
                findings.order_by('-movement__started_at', 'code', 'subject'),
                data,
                MovementFindingSerializer,
                'findings',
            )
        )


class AlertListView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request):
        data = _page(AlertFilterSerializer, request)

        alerts = Alert.objects.filter(
            station=request.staff_profile.station
        ).select_related('acknowledged_by')

        if data.get('severity'):
            alerts = alerts.filter(severity=data['severity'])
        if data.get('code'):
            alerts = alerts.filter(code=data['code'])
        if data.get('acknowledged') is True:
            alerts = alerts.filter(acknowledged_at__isnull=False)
        elif data.get('acknowledged') is False:
            alerts = alerts.filter(acknowledged_at__isnull=True)

        return Response(
            _paginated(
                alerts.order_by('-raised_at', '-id'),
                data,
                AlertSerializer,
                'alerts',
            )
        )


class AlertAcknowledgeView(APIView):
    """Acknowledge one alert. Repeating it is a no-op, not a re-attribution.

    Acknowledgement is a notification fact, not a status change on the
    finding: the finding still describes the movement.
    """

    permission_classes = (IsYardOperator,)

    def post(self, request, pk):
        alert = get_station_object_or_404(
            Alert,
            request.staff_profile.station,
            pk=pk,
        )
        if alert.acknowledged_at is None:
            alert.acknowledged_at = clock.server_now()
            alert.acknowledged_by = request.user
            alert.save(
                update_fields=(
                    'acknowledged_at',
                    'acknowledged_by',
                    'updated_at',
                )
            )
        return Response(AlertSerializer(alert).data)


class InventoryListView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request):
        data = _page(InventoryFilterSerializer, request)
        settle(request.staff_profile.station)

        inventory = InventoryRecord.objects.filter(
            station=request.staff_profile.station
        ).select_related('rolling_stock')

        if data.get('status'):
            inventory = inventory.filter(status=data['status'])
        if data.get('verification'):
            inventory = inventory.filter(verification=data['verification'])
        if data.get('q'):
            inventory = inventory.filter(rfid_uid__icontains=data['q'])

        return Response(
            _paginated(
                inventory.order_by('status', 'rfid_uid'),
                data,
                InventoryRecordSerializer,
                'inventory',
            )
        )


class OverviewView(APIView):
    """One compact snapshot for the dashboard's landing view."""

    permission_classes = (IsStationStaff,)

    def get(self, request):
        station = request.staff_profile.station
        settle(station)

        lanes = Device.objects.filter(station=station)
        by_mode = {'ENTRY': 0, 'EXIT': 0, 'UNSET': 0}
        for row in lanes.values('lane_mode').annotate(total=Count('id')):
            by_mode[row['lane_mode'] or 'UNSET'] = row['total']

        inventory = InventoryRecord.objects.filter(station=station)
        open_movements = (
            Movement.objects.filter(station=station, state=Movement.State.OPEN)
            .select_related('device', 'plan')
            .order_by('-started_at', '-id')
        )
        latest = (
            Movement.objects.filter(station=station)
            .select_related('device', 'plan')
            .order_by('-started_at', '-id')
            .first()
        )

        return Response(
            {
                'station': {'code': station.code, 'name': station.name},
                'lanes': {
                    'total': lanes.count(),
                    'active': lanes.filter(is_active=True).count(),
                    'by_mode': by_mode,
                },
                'inventory': {
                    'in_yard': inventory.filter(
                        status=InventoryRecord.Status.IN_YARD
                    ).count(),
                    'out': inventory.filter(
                        status=InventoryRecord.Status.OUT
                    ).count(),
                    'unverified': inventory.filter(
                        verification=InventoryRecord.Verification.UNVERIFIED
                    ).count(),
                },
                'alerts': {
                    'unacknowledged': Alert.objects.filter(
                        station=station, acknowledged_at__isnull=True
                    ).count(),
                },
                'open_movements': MovementSummarySerializer(
                    open_movements, many=True
                ).data,
                'latest_movement': (
                    MovementSummarySerializer(latest).data if latest else None
                ),
            }
        )
