"""Wire format for device ingestion.

The firmware speaks ``event_type``; the database calls the same field ``kind``.
Both spellings are accepted on input, ``event_type`` being the documented one,
so an older v3-shaped script keeps working without a database rename.

Validation here is deliberately per event. A single malformed event must not
poison a queue: the firmware would retry the whole batch forever.
"""

import re

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import MovementEvent
from .uid import canonical_uid

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
