"""Domain models for the railway station yard monitoring prototype.

This is a passive, hand-operated tabletop auditing prototype. It does not
control railway infrastructure.
"""

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .uid import canonical_uid


class ValidationPolicy:
    RFID_PRIMARY_COUNTS_V4 = 'RFID_PRIMARY_COUNTS_V4'


class Direction(models.TextChoices):
    ENTRY = 'ENTRY', 'Entry'
    EXIT = 'EXIT', 'Exit'


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class Station(TimeStampedModel):
    code = models.CharField(max_length=12, unique=True)
    name = models.CharField(max_length=120)
    division = models.CharField(max_length=64, blank=True)
    zone = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('code',)

    def __str__(self):
        return f'{self.code} - {self.name}'


class StaffProfile(TimeStampedModel):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Administrator'
        YARD_MASTER = 'YARD_MASTER', 'Yard master'
        VIEWER = 'VIEWER', 'Viewer'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='staff_profile',
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name='staff',
    )
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.YARD_MASTER,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('station__code', 'user__username')

    def __str__(self):
        return f'{self.user} @ {self.station.code}'


class Device(TimeStampedModel):
    device_id = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=120, blank=True)
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name='devices',
    )
    lane_mode = models.CharField(
        max_length=8,
        choices=Direction.choices,
        null=True,
        blank=True,
        help_text=(
            'Current operating direction for this lane. Set by an operator; '
            'null until it has been set for the first time.'
        ),
    )
    api_key_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('station__code', 'device_id')

    def set_api_key(self, raw_key):
        if not raw_key:
            raise ValidationError('Device API key must not be empty.')
        self.api_key_hash = make_password(raw_key)

    def check_api_key(self, raw_key):
        if not raw_key or not self.api_key_hash:
            return False
        return check_password(raw_key, self.api_key_hash)

    @property
    def has_api_key(self):
        return bool(self.api_key_hash)

    def __str__(self):
        return f'{self.device_id} ({self.lane_mode or "mode unset"})'


class LaneModeChange(TimeStampedModel):
    """Audit record of an operator choosing a lane's operating direction."""

    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        related_name='lane_mode_changes',
    )
    direction = models.CharField(max_length=8, choices=Direction.choices)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='lane_mode_changes',
    )
    note = models.CharField(max_length=200, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-changed_at', '-id')
        indexes = [
            models.Index(
                fields=('device', '-changed_at'),
                name='lanemode_device_time_idx',
            ),
        ]

    def __str__(self):
        return f'{self.device.device_id} -> {self.direction}'


class RollingStock(TimeStampedModel):
    class Kind(models.TextChoices):
        LOCO = 'LOCO', 'Locomotive'
        COACH = 'COACH', 'Coach'
        WAGON = 'WAGON', 'Wagon'

    rfid_uid = models.CharField(
        max_length=32,
        unique=True,
        help_text='Canonical uppercase hex UID without separators.',
    )
    number = models.CharField(max_length=32, unique=True)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    home_shed = models.CharField(max_length=64, blank=True)
    division = models.CharField(max_length=64, blank=True)
    zone = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('kind', 'number')
        indexes = [
            models.Index(fields=('kind', 'is_active'), name='stock_kind_active_idx'),
        ]

    def clean(self):
        super().clean()
        if self.rfid_uid:
            try:
                self.rfid_uid = canonical_uid(self.rfid_uid)
            except ValidationError as exc:
                raise ValidationError({'rfid_uid': exc.messages})

    def save(self, *args, **kwargs):
        if self.rfid_uid:
            self.rfid_uid = canonical_uid(self.rfid_uid)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.number} ({self.kind})'


class MovementPlan(TimeStampedModel):
    class Status(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        ACTIVE = 'ACTIVE', 'Active'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    plan_code = models.CharField(max_length=64, unique=True)
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name='plans',
    )
    direction = models.CharField(max_length=8, choices=Direction.choices)
    expected_lead_loco = models.ForeignKey(
        RollingStock,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='lead_for_plans',
        limit_choices_to={'kind': RollingStock.Kind.LOCO},
    )
    expected_locos = models.PositiveSmallIntegerField(default=1)
    expected_coaches = models.PositiveSmallIntegerField(default=0)
    expected_wagons = models.PositiveSmallIntegerField(default=0)
    expected_start = models.DateTimeField()
    origin = models.CharField(max_length=120, blank=True)
    destination = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('expected_start', 'plan_code')
        indexes = [
            models.Index(
                fields=('station', 'direction', 'status', 'expected_start'),
                name='plan_lookup_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(expected_locos__gte=1)
                    | Q(expected_coaches__gte=1)
                    | Q(expected_wagons__gte=1)
                ),
                name='plan_expects_at_least_one_unit',
            ),
        ]

    @property
    def expected_total(self):
        return self.expected_locos + self.expected_coaches + self.expected_wagons

    def clean(self):
        super().clean()
        if (
            self.expected_lead_loco_id
            and self.expected_lead_loco.kind != RollingStock.Kind.LOCO
        ):
            raise ValidationError(
                {'expected_lead_loco': 'Expected lead unit must be a locomotive.'}
            )

    def __str__(self):
        return f'{self.plan_code} ({self.station.code} {self.direction})'


class PlanAssignment(TimeStampedModel):
    plan = models.ForeignKey(
        MovementPlan,
        on_delete=models.CASCADE,
        related_name='assignments',
    )
    rolling_stock = models.ForeignKey(
        RollingStock,
        on_delete=models.CASCADE,
        related_name='plan_assignments',
    )

    class Meta:
        ordering = ('plan_id', 'rolling_stock__number')
        constraints = [
            models.UniqueConstraint(
                fields=('plan', 'rolling_stock'),
                name='unique_plan_stock_assignment',
            ),
        ]

    def __str__(self):
        return f'{self.rolling_stock} -> {self.plan.plan_code}'


class Movement(TimeStampedModel):
    class State(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        CLOSED = 'CLOSED', 'Closed'

    class Outcome(models.TextChoices):
        VALID = 'VALID', 'Valid'
        FLAGGED = 'FLAGGED', 'Flagged'
        UNVERIFIED = 'UNVERIFIED', 'Unverified'

    class MatchSource(models.TextChoices):
        NONE = 'NONE', 'Not matched'
        LOCOMOTIVE_RFID = 'LOCOMOTIVE_RFID', 'Locomotive RFID'
        COACH_ASSIGNMENT = 'COACH_ASSIGNMENT', 'Coach assignment'

    class DirectionSource(models.TextChoices):
        OPERATOR = 'OPERATOR', 'Operator lane mode'
        PLAN = 'PLAN', 'Movement plan'
        INFERRED = 'INFERRED', 'Inventory inference'

    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name='movements',
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        related_name='movements',
    )
    direction = models.CharField(max_length=8, choices=Direction.choices)
    direction_source = models.CharField(
        max_length=12,
        choices=DirectionSource.choices,
        default=DirectionSource.OPERATOR,
    )
    lane_mode_change = models.ForeignKey(
        LaneModeChange,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='movements',
    )
    state = models.CharField(
        max_length=8,
        choices=State.choices,
        default=State.OPEN,
    )
    started_at = models.DateTimeField(default=timezone.now)
    last_evidence_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    plan = models.ForeignKey(
        MovementPlan,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='movements',
    )
    plan_match_source = models.CharField(
        max_length=24,
        choices=MatchSource.choices,
        default=MatchSource.NONE,
    )

    expected_lead_loco = models.ForeignKey(
        RollingStock,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='expected_for_movements',
    )
    expected_locos = models.PositiveSmallIntegerField(null=True, blank=True)
    expected_coaches = models.PositiveSmallIntegerField(null=True, blank=True)
    expected_wagons = models.PositiveSmallIntegerField(null=True, blank=True)

    validation_policy = models.CharField(
        max_length=32,
        default=ValidationPolicy.RFID_PRIMARY_COUNTS_V4,
    )
    validation_result = models.CharField(
        max_length=12,
        choices=Outcome.choices,
        null=True,
        blank=True,
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-started_at', '-id')
        indexes = [
            models.Index(
                fields=('station', 'state', '-started_at'),
                name='movement_station_state_idx',
            ),
            models.Index(fields=('device', 'state'), name='movement_device_state_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('device',),
                condition=Q(state='OPEN'),
                name='one_open_movement_per_lane',
            ),
            models.UniqueConstraint(
                fields=('plan',),
                condition=Q(plan__isnull=False),
                name='one_movement_per_plan',
            ),
        ]

    def clean(self):
        super().clean()
        if self.plan_id:
            if self.plan.station_id != self.station_id:
                raise ValidationError(
                    {'plan': 'Plan belongs to a different station.'}
                )
            if self.plan.direction != self.direction:
                raise ValidationError(
                    {'plan': 'Plan direction does not match the lane direction.'}
                )
        if self.lane_mode_change_id:
            if self.lane_mode_change.device_id != self.device_id:
                raise ValidationError(
                    {'lane_mode_change': 'Lane mode change belongs to a different device.'}
                )
            if self.lane_mode_change.direction != self.direction:
                raise ValidationError(
                    {'direction': 'Direction does not match the recorded lane mode.'}
                )
        if self.state == self.State.OPEN and self.closed_at:
            raise ValidationError(
                {'closed_at': 'Open movements cannot have a closure time.'}
            )
        if self.state == self.State.CLOSED and not self.closed_at:
            raise ValidationError(
                {'closed_at': 'Closed movements require a closure time.'}
            )

    def __str__(self):
        return f'Movement {self.pk} ({self.direction}, {self.state})'


class MovementEvent(TimeStampedModel):
    class Kind(models.TextChoices):
        RFID_DETECTED = 'RFID_DETECTED', 'RFID detected'
        PHYSICAL_PASSAGE = 'PHYSICAL_PASSAGE', 'Physical passage'

    event_id = models.CharField(
        max_length=64,
        unique=True,
        help_text='Stable device-generated identifier; identical on retry.',
    )
    movement = models.ForeignKey(
        Movement,
        on_delete=models.CASCADE,
        related_name='events',
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        related_name='events',
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    rfid_uid = models.CharField(max_length=32, blank=True)
    rolling_stock = models.ForeignKey(
        RollingStock,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='events',
    )
    sequence = models.BigIntegerField(null=True, blank=True)
    device_timestamp = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now, editable=False)

    passage_started_at = models.DateTimeField(null=True, blank=True)
    passage_ended_at = models.DateTimeField(null=True, blank=True)
    passage_duration_ms = models.PositiveIntegerField(null=True, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('received_at', 'id')
        indexes = [
            models.Index(fields=('movement', 'kind'), name='event_movement_kind_idx'),
            models.Index(fields=('device', 'received_at'), name='event_device_time_idx'),
            models.Index(fields=('movement', 'rfid_uid'), name='event_movement_uid_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind='RFID_DETECTED', rfid_uid__gt='')
                    | Q(kind='PHYSICAL_PASSAGE', rfid_uid='')
                ),
                name='event_kind_matches_uid',
            ),
        ]

    def __str__(self):
        return f'{self.kind} {self.event_id}'


class MovementFinding(TimeStampedModel):
    class Severity(models.TextChoices):
        INFO = 'INFO', 'Info'
        WARN = 'WARN', 'Warning'
        FAIL = 'FAIL', 'Failure'

    movement = models.ForeignKey(
        Movement,
        on_delete=models.CASCADE,
        related_name='findings',
    )
    code = models.CharField(max_length=48)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    subject = models.CharField(max_length=64, blank=True, default='')
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('code', 'subject')
        constraints = [
            models.UniqueConstraint(
                fields=('movement', 'code', 'subject'),
                name='unique_finding_per_subject',
            ),
        ]

    def __str__(self):
        return f'{self.code} ({self.severity})'


class Alert(TimeStampedModel):
    class Severity(models.TextChoices):
        WARNING = 'WARNING', 'Warning'
        CRITICAL = 'CRITICAL', 'Critical'

    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name='alerts',
    )
    movement = models.ForeignKey(
        Movement,
        on_delete=models.CASCADE,
        related_name='alerts',
    )
    code = models.CharField(max_length=48)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    subject = models.CharField(max_length=64, blank=True, default='')
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    raised_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='acknowledged_alerts',
    )

    class Meta:
        ordering = ('-raised_at', '-id')
        indexes = [
            models.Index(
                fields=('station', 'acknowledged_at'),
                name='alert_station_open_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('movement', 'code', 'subject'),
                name='unique_alert_per_subject',
            ),
        ]

    def __str__(self):
        return f'{self.code} ({self.severity})'


class InventoryRecord(TimeStampedModel):
    class Status(models.TextChoices):
        IN_YARD = 'IN_YARD', 'In yard'
        OUT = 'OUT', 'Out'

    class Verification(models.TextChoices):
        VERIFIED = 'VERIFIED', 'Verified'
        UNVERIFIED = 'UNVERIFIED', 'Unverified'

    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name='inventory_records',
    )
    rfid_uid = models.CharField(max_length=32)
    rolling_stock = models.ForeignKey(
        RollingStock,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='inventory_records',
    )
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.OUT,
    )
    verification = models.CharField(
        max_length=10,
        choices=Verification.choices,
        default=Verification.UNVERIFIED,
    )
    since = models.DateTimeField(default=timezone.now)
    last_movement = models.ForeignKey(
        Movement,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='inventory_updates',
    )

    class Meta:
        ordering = ('station__code', 'rfid_uid')
        indexes = [
            models.Index(
                fields=('station', 'status'),
                name='inventory_station_status_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('station', 'rfid_uid'),
                name='unique_inventory_uid_per_station',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.rfid_uid:
            self.rfid_uid = canonical_uid(self.rfid_uid)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.rfid_uid} @ {self.station.code} ({self.status})'


class MovementInventoryEffect(TimeStampedModel):
    """The frozen inventory state for one distinct UID in one movement.

    This row is created during validation *before* any inventory transition is
    written, so ``status_before`` is what the yard looked like before this
    movement. Every anomaly finding (``ENTRY_ALREADY_PRESENT``,
    ``UNEXPECTED_EXIT``, ``DIRECTION_CONFLICT``) is evaluated against this
    snapshot on every revalidation. Reading the live inventory row instead would
    mistake this movement's own update, or a later movement's, for the prior
    state.

    ``applied_at`` is the "transition already written" marker. A revalidation
    recomputes findings from the snapshot but never replays the transition, so a
    later movement's state can never be overwritten. ``status_before`` is null
    when no inventory row existed at all.
    """

    movement = models.ForeignKey(
        Movement,
        on_delete=models.CASCADE,
        related_name='inventory_effects',
    )
    rfid_uid = models.CharField(max_length=32)
    status_before = models.CharField(
        max_length=8,
        choices=InventoryRecord.Status.choices,
        null=True,
        blank=True,
        help_text='Inventory status before this movement; null when no row existed.',
    )
    status_after = models.CharField(
        max_length=8,
        choices=InventoryRecord.Status.choices,
    )
    applied_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Set once the transition has been applied; never cleared.',
    )

    class Meta:
        ordering = ('movement', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=('movement', 'rfid_uid'),
                name='unique_inventory_effect_per_movement_uid',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.rfid_uid:
            self.rfid_uid = canonical_uid(self.rfid_uid)
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f'{self.rfid_uid} {self.status_before or "ABSENT"} -> '
            f'{self.status_after} (movement {self.movement_id})'
        )
