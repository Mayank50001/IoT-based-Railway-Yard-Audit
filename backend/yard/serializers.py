"""Wire formats for ingestion and the dashboard.

The firmware speaks ``event_type``; the database calls the same field ``kind``.
Both spellings are accepted on input, ``event_type`` being the documented one,
so an older v3-shaped script keeps working without a database rename.

Ingestion validation is deliberately per event. A single malformed event must
not poison a queue: the firmware would retry the whole batch forever.

The lane serializers never expose ``api_key_hash``; a provisioning secret must
not travel to a browser once it has been set.
"""

import re

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Alert,
    Device,
    Direction,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementFinding,
    MovementInventoryEffect,
    RollingStock,
)
from .uid import canonical_uid
from .validation import composition_for

EVENT_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')

EVENT_ID_HELP = (
    'Stable identifier, identical on every retry. Recommended: '
    '<device_id>-<boot_id>-<sequence>, e.g. gate-1-7f3a2c-000042.'
)


class PassageMetadataSerializer(serializers.Serializer):
    """Audit data about the ultrasonic interruption.

    Only the timing fields affect the event record. Range readings are kept for
    diagnosis and are never used for per-unit pairing.
    """

    started_at = serializers.DateTimeField(required=False, allow_null=True)
    ended_at = serializers.DateTimeField(required=False, allow_null=True)
    duration_ms = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
    )
    min_distance_cm = serializers.IntegerField(required=False, allow_null=True)
    peak_distance_cm = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        started = attrs.get('started_at')
        ended = attrs.get('ended_at')
        if started and ended and ended < started:
            raise serializers.ValidationError(
                {'ended_at': 'ended_at cannot be earlier than started_at.'}
            )
        return attrs


class EventSerializer(serializers.Serializer):
    event_id = serializers.RegexField(
        EVENT_ID_PATTERN,
        max_length=64,
        error_messages={
            'invalid': (
                'event_id must be 1-64 characters of letters, digits, '
                'dot, underscore, colon or hyphen, starting with a letter '
                'or digit.'
            ),
        },
        help_text=EVENT_ID_HELP,
    )
    event_type = serializers.ChoiceField(choices=MovementEvent.Kind.choices)
    device_id = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
    )
    timestamp = serializers.DateTimeField(required=False, allow_null=True)
    sequence = serializers.IntegerField(required=False, allow_null=True)
    rfid_uid = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=True,
    )
    metadata = PassageMetadataSerializer(required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and 'event_type' not in data and 'kind' in data:
            data = {**data, 'event_type': data['kind']}
        return super().to_internal_value(data)

    def validate(self, attrs):
        kind = attrs['event_type']
        uid = attrs.get('rfid_uid') or ''

        if kind == MovementEvent.Kind.RFID_DETECTED and not uid:
            raise serializers.ValidationError(
                {'rfid_uid': 'rfid_uid is required for RFID_DETECTED.'}
            )
        if kind == MovementEvent.Kind.PHYSICAL_PASSAGE and uid:
            raise serializers.ValidationError(
                {'rfid_uid': 'rfid_uid must be empty for PHYSICAL_PASSAGE.'}
            )

        if uid:
            try:
                attrs['rfid_uid'] = canonical_uid(uid)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {'rfid_uid': exc.messages}
                ) from exc

        return attrs


def event_error_code(errors):
    """Turn a DRF error mapping into one stable code for the firmware."""
    if 'rfid_uid' in errors:
        text = ' '.join(str(item) for item in errors['rfid_uid'])
        if 'required' in text:
            return 'MISSING_RFID_UID'
        if 'empty' in text:
            return 'UNEXPECTED_RFID_UID'
        return 'INVALID_UID'
    if 'event_type' in errors:
        return 'INVALID_EVENT_TYPE'
    if 'event_id' in errors:
        return 'INVALID_EVENT_ID'
    if 'timestamp' in errors:
        return 'INVALID_DEVICE_TIMESTAMP'
    if 'metadata' in errors:
        return 'INVALID_PASSAGE_METADATA'
    return 'INVALID_EVENT'


def flatten_errors(errors):
    """Reduce DRF's nested error structure to JSON-safe lists of strings."""
    flat = {}
    for field, value in errors.items():
        if isinstance(value, dict):
            flat[field] = flatten_errors(value)
        elif isinstance(value, (list, tuple)):
            flat[field] = [str(item) for item in value]
        else:
            flat[field] = [str(value)]
    return flat


# --- Session -----------------------------------------------------------------


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,
        style={'input_type': 'password'},
    )


# --- Lanes -------------------------------------------------------------------


class LaneSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = (
            'device_id',
            'label',
            'lane_mode',
            'is_active',
            'last_seen_at',
        )


class LaneModeChangeSerializer(serializers.ModelSerializer):
    set_by = serializers.SerializerMethodField()

    class Meta:
        model = LaneModeChange
        fields = ('id', 'direction', 'set_by', 'note', 'changed_at')

    def get_set_by(self, obj):
        return obj.set_by.get_username() if obj.set_by else None


class LaneDetailSerializer(LaneSummarySerializer):
    open_movement = serializers.SerializerMethodField()
    recent_mode_changes = serializers.SerializerMethodField()

    class Meta(LaneSummarySerializer.Meta):
        fields = LaneSummarySerializer.Meta.fields + (
            'open_movement',
            'recent_mode_changes',
        )

    def get_open_movement(self, obj):
        movement = (
            obj.movements.filter(state=Movement.State.OPEN)
            .order_by('-started_at', '-id')
            .first()
        )
        return open_movement_payload(movement)

    def get_recent_mode_changes(self, obj):
        changes = obj.lane_mode_changes.select_related('set_by').order_by(
            '-changed_at', '-id'
        )[:10]
        return LaneModeChangeSerializer(changes, many=True).data


def open_movement_payload(movement):
    """Smallest useful description of a lane's current bucket."""
    if movement is None:
        return None
    return {
        'id': movement.pk,
        'direction': movement.direction,
        'started_at': movement.started_at,
        'last_evidence_at': movement.last_evidence_at,
    }


class LaneModeSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=Direction.choices)
    note = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        default='',
    )


# --- Read API ----------------------------------------------------------------


class PageQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(
        min_value=1, max_value=200, required=False, default=50
    )
    offset = serializers.IntegerField(min_value=0, required=False, default=0)


class MovementFilterSerializer(PageQuerySerializer):
    state = serializers.ChoiceField(
        choices=Movement.State.choices, required=False
    )
    direction = serializers.ChoiceField(
        choices=Direction.choices, required=False
    )
    result = serializers.ChoiceField(
        choices=Movement.Outcome.choices, required=False
    )
    device_id = serializers.CharField(max_length=64, required=False)


class InventoryFilterSerializer(PageQuerySerializer):
    status = serializers.ChoiceField(
        choices=InventoryRecord.Status.choices, required=False
    )
    verification = serializers.ChoiceField(
        choices=InventoryRecord.Verification.choices, required=False
    )
    q = serializers.CharField(
        max_length=64, required=False, allow_blank=True, trim_whitespace=True
    )


class AlertFilterSerializer(PageQuerySerializer):
    severity = serializers.ChoiceField(
        choices=Alert.Severity.choices, required=False
    )
    acknowledged = serializers.BooleanField(required=False)
    code = serializers.CharField(max_length=48, required=False)


class FindingFilterSerializer(PageQuerySerializer):
    severity = serializers.ChoiceField(
        choices=MovementFinding.Severity.choices, required=False
    )
    code = serializers.CharField(max_length=48, required=False)
    movement = serializers.IntegerField(min_value=1, required=False)


class MovementFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovementFinding
        fields = (
            'id',
            'code',
            'severity',
            'subject',
            'message',
            'details',
            'created_at',
            'updated_at',
        )


class AlertSerializer(serializers.ModelSerializer):
    movement_id = serializers.IntegerField(read_only=True)
    acknowledged_by = serializers.SerializerMethodField()

    class Meta:
        model = Alert
        fields = (
            'id',
            'movement_id',
            'code',
            'severity',
            'subject',
            'message',
            'details',
            'raised_at',
            'acknowledged_at',
            'acknowledged_by',
        )

    def get_acknowledged_by(self, obj):
        return (
            obj.acknowledged_by.get_username()
            if obj.acknowledged_by
            else None
        )


class MovementEventReadSerializer(serializers.ModelSerializer):
    rolling_stock = serializers.SerializerMethodField()

    class Meta:
        model = MovementEvent
        fields = (
            'event_id',
            'kind',
            'rfid_uid',
            'rolling_stock',
            'sequence',
            'device_timestamp',
            'received_at',
            'passage_started_at',
            'passage_ended_at',
            'passage_duration_ms',
        )

    def get_rolling_stock(self, obj):
        if obj.rolling_stock is None:
            return None
        return {
            'number': obj.rolling_stock.number,
            'kind': obj.rolling_stock.kind,
        }


class InventoryEffectSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovementInventoryEffect
        fields = (
            'rfid_uid',
            'status_before',
            'status_after',
            'applied_at',
        )


class MovementSummarySerializer(serializers.ModelSerializer):
    """Fields every movement payload shares."""

    device_id = serializers.SerializerMethodField()
    plan_code = serializers.SerializerMethodField()
    expected_counts = serializers.SerializerMethodField()

    class Meta:
        model = Movement
        fields = (
            'id',
            'device_id',
            'direction',
            'direction_source',
            'state',
            'validation_result',
            'plan_code',
            'plan_match_source',
            'expected_counts',
            'started_at',
            'last_evidence_at',
            'closed_at',
            'validated_at',
        )

    def get_device_id(self, obj):
        return obj.device.device_id

    def get_plan_code(self, obj):
        return obj.plan.plan_code if obj.plan else None

    def get_expected_counts(self, obj):
        if obj.plan_id is None:
            return None
        return {
            'locos': obj.expected_locos,
            'coaches': obj.expected_coaches,
            'wagons': obj.expected_wagons,
        }


class MovementDetailSerializer(MovementSummarySerializer):
    station = serializers.SerializerMethodField()
    observed_counts = serializers.SerializerMethodField()
    counts_agree = serializers.SerializerMethodField()
    findings = serializers.SerializerMethodField()
    alerts = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    inventory_effects = serializers.SerializerMethodField()

    class Meta(MovementSummarySerializer.Meta):
        fields = MovementSummarySerializer.Meta.fields + (
            'station',
            'observed_counts',
            'counts_agree',
            'findings',
            'alerts',
            'events',
            'inventory_effects',
        )

    def get_station(self, obj):
        return {'code': obj.station.code, 'name': obj.station.name}

    def _composition(self, obj):
        if not hasattr(obj, '_composition_cache'):
            obj._composition_cache = composition_for(obj)
        return obj._composition_cache

    def get_observed_counts(self, obj):
        composition = self._composition(obj)
        return {
            'locos': len(composition.stocks(RollingStock.Kind.LOCO)),
            'coaches': len(composition.stocks(RollingStock.Kind.COACH)),
            'wagons': len(composition.stocks(RollingStock.Kind.WAGON)),
            'distinct_uids': len(composition.distinct_uids),
            'unknown_uids': len(composition.unknown_uids),
            'rfid_observations': composition.rfid_observation_count,
            'passages': composition.passage_count,
        }

    def get_counts_agree(self, obj):
        expected = self.get_expected_counts(obj)
        if expected is None:
            return None
        observed = self.get_observed_counts(obj)
        return all(
            expected[key] == observed[key]
            for key in ('locos', 'coaches', 'wagons')
        )

    def get_findings(self, obj):
        return MovementFindingSerializer(
            obj.findings.order_by('severity', 'code', 'subject'), many=True
        ).data

    def get_alerts(self, obj):
        return AlertSerializer(
            obj.alerts.select_related('acknowledged_by').order_by(
                '-raised_at', '-id'
            ),
            many=True,
        ).data

    def get_events(self, obj):
        return MovementEventReadSerializer(
            obj.events.select_related('rolling_stock').order_by(
                'received_at', 'id'
            ),
            many=True,
        ).data

    def get_inventory_effects(self, obj):
        return InventoryEffectSerializer(
            obj.inventory_effects.order_by('id'), many=True
        ).data


class InventoryRecordSerializer(serializers.ModelSerializer):
    rolling_stock = serializers.SerializerMethodField()
    last_movement_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = InventoryRecord
        fields = (
            'id',
            'rfid_uid',
            'rolling_stock',
            'status',
            'verification',
            'since',
            'last_movement_id',
            'updated_at',
        )

    def get_rolling_stock(self, obj):
        if obj.rolling_stock is None:
            return None
        return {
            'number': obj.rolling_stock.number,
            'kind': obj.rolling_stock.kind,
            'is_active': obj.rolling_stock.is_active,
        }
