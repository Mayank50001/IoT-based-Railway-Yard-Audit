from django import forms
from django.contrib import admin

from .models import (
    Alert,
    Device,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementEvent,
    MovementFinding,
    MovementInventoryEffect,
    MovementPlan,
    PlanAssignment,
    RollingStock,
    StaffProfile,
    Station,
)
from .services import set_lane_mode


class DeviceAdminForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Leave blank to keep the current key. Stored as a hash.',
    )

    class Meta:
        model = Device
        fields = ('device_id', 'label', 'station', 'lane_mode', 'is_active')

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and not cleaned.get('api_key'):
            self.add_error('api_key', 'An API key is required for a new device.')
        return cleaned

    def save(self, commit=True):
        device = super().save(commit=False)
        raw_key = self.cleaned_data.get('api_key')
        if raw_key:
            device.set_api_key(raw_key)
        if commit:
            device.save()
            self.save_m2m()
        return device


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'division', 'zone', 'is_active')
    list_filter = ('is_active', 'zone')
    search_fields = ('code', 'name')


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'station', 'role', 'is_active')
    list_filter = ('station', 'role', 'is_active')


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    form = DeviceAdminForm
    list_display = ('device_id', 'station', 'lane_mode', 'is_active', 'last_seen_at')
    list_filter = ('station', 'lane_mode', 'is_active')
    search_fields = ('device_id', 'label')
    readonly_fields = ('last_seen_at', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            previous = (
                Device.objects.filter(pk=obj.pk)
                .values_list('lane_mode', flat=True)
                .first()
            )

        super().save_model(request, obj, form, change)

        if obj.lane_mode and obj.lane_mode != previous:
            set_lane_mode(
                obj,
                obj.lane_mode,
                user=request.user,
                note='Set from Django admin',
            )


@admin.register(LaneModeChange)
class LaneModeChangeAdmin(admin.ModelAdmin):
    list_display = ('device', 'direction', 'set_by', 'changed_at', 'note')
    list_filter = ('device__station', 'direction')
    readonly_fields = (
        'device',
        'direction',
        'set_by',
        'note',
        'changed_at',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RollingStock)
class RollingStockAdmin(admin.ModelAdmin):
    list_display = ('number', 'kind', 'rfid_uid', 'home_shed', 'is_active')
    list_filter = ('kind', 'is_active', 'zone')
    search_fields = ('number', 'rfid_uid')


class PlanAssignmentInline(admin.TabularInline):
    model = PlanAssignment
    extra = 1
    autocomplete_fields = ('rolling_stock',)


@admin.register(MovementPlan)
class MovementPlanAdmin(admin.ModelAdmin):
    list_display = (
        'plan_code',
        'station',
        'direction',
        'expected_lead_loco',
        'expected_locos',
        'expected_coaches',
        'expected_wagons',
        'expected_start',
        'status',
    )
    list_filter = ('station', 'direction', 'status')
    search_fields = ('plan_code', 'origin', 'destination')
    inlines = (PlanAssignmentInline,)


class MovementEventInline(admin.TabularInline):
    model = MovementEvent
    extra = 0
    can_delete = False
    fields = ('kind', 'rfid_uid', 'event_id', 'device_timestamp', 'received_at')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class MovementFindingInline(admin.TabularInline):
    model = MovementFinding
    extra = 0
    fields = ('code', 'severity', 'subject', 'message')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class MovementInventoryEffectInline(admin.TabularInline):
    """The frozen pre-movement snapshot and the one transition it authorised."""

    model = MovementInventoryEffect
    extra = 0
    fields = ('rfid_uid', 'status_before', 'status_after', 'applied_at')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Movement)
class MovementAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'station',
        'device',
        'direction',
        'state',
        'validation_result',
        'started_at',
        'last_evidence_at',
        'closed_at',
    )
    list_filter = ('station', 'direction', 'state', 'validation_result')
    # Validation is authoritative: its output is not hand-edited. ``plan`` stays
    # editable on purpose, as the current manual workaround for a wrong bind.
    readonly_fields = (
        'created_at',
        'updated_at',
        'validated_at',
        'validation_policy',
        'validation_result',
        'plan_match_source',
        'expected_locos',
        'expected_coaches',
        'expected_wagons',
        'expected_lead_loco',
    )
    inlines = (
        MovementEventInline,
        MovementFindingInline,
        MovementInventoryEffectInline,
    )


@admin.register(InventoryRecord)
class InventoryRecordAdmin(admin.ModelAdmin):
    list_display = (
        'station',
        'rfid_uid',
        'rolling_stock',
        'status',
        'verification',
        'since',
    )
    list_filter = ('station', 'status', 'verification')
    search_fields = ('rfid_uid',)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'station',
        'movement',
        'severity',
        'raised_at',
        'acknowledged_at',
    )
    list_filter = ('station', 'severity', 'acknowledged_at')
    search_fields = ('code', 'message')
