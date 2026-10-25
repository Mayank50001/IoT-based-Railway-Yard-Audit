"""Create repeatable demo data for the RFID-primary (v4) policy.

Master data is upserted, so the command can be re-run safely. Movements,
inventory and plans are only removed when --reset is passed, because deleting
audit history is otherwise never the seed command's business.
"""

import secrets

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from yard.models import (
    Device,
    Direction,
    InventoryRecord,
    LaneModeChange,
    Movement,
    MovementPlan,
    PlanAssignment,
    RollingStock,
    Station,
)
from yard.services import set_lane_mode

STATION_CODE = 'NDLS'
LANE_ID = 'gate-1'

FLEET = (
    {
        'number': 'WDM3A-1001',
        'rfid_uid': 'FA37E63F',
        'kind': RollingStock.Kind.LOCO,
        'home_shed': 'NDLS',
    },
    {
        'number': 'WAP7-3020',
        'rfid_uid': 'A0A00002',
        'kind': RollingStock.Kind.LOCO,
        'home_shed': 'NDLS',
    },
    {
        'number': 'NR-C-2201',
        'rfid_uid': '0E78E13F',
        'kind': RollingStock.Kind.COACH,
    },
    {
        'number': 'NR-C-2202',
        'rfid_uid': 'C0A00002',
        'kind': RollingStock.Kind.COACH,
    },
    {
        'number': 'NR-C-2203',
        'rfid_uid': 'C0A00003',
        'kind': RollingStock.Kind.COACH,
    },
    {
        'number': 'NR-C-2204',
        'rfid_uid': 'C0A00004',
        'kind': RollingStock.Kind.COACH,
    },
    {
        'number': 'NR-C-2205',
        'rfid_uid': 'C0A00005',
        'kind': RollingStock.Kind.COACH,
    },
    {
        'number': 'NR-C-2206',
        'rfid_uid': 'C0A00006',
        'kind': RollingStock.Kind.COACH,
    },
    {
        'number': 'NR-W-3301',
        'rfid_uid': 'B0B00001',
        'kind': RollingStock.Kind.WAGON,
    },
    {
        'number': 'NR-W-3302',
        'rfid_uid': 'B0B00002',
        'kind': RollingStock.Kind.WAGON,
    },
)

PLANS = (
    {
        # Matches the two physical tags on the bench.
        'plan_code': 'NDLS-IN-BENCH',
        'direction': Direction.ENTRY,
        'lead': 'WDM3A-1001',
        'locos': 1,
        'coaches': 1,
        'wagons': 0,
        'assign': ('NR-C-2201',),
        'origin': 'Bench',
        'destination': 'NDLS yard',
    },
    {
        # Full simulator scenario: 1 locomotive + 4 distinct coaches.
        'plan_code': 'NDLS-IN-FULL',
        'direction': Direction.ENTRY,
        'lead': 'WAP7-3020',
        'locos': 1,
        'coaches': 4,
        'wagons': 0,
        'assign': ('NR-C-2202', 'NR-C-2203', 'NR-C-2204', 'NR-C-2205'),
        'origin': 'Delhi Cantt',
        'destination': 'NDLS yard',
    },
    {
        # Run after an entry so the coaches are recorded IN_YARD.
        'plan_code': 'NDLS-OUT-FULL',
        'direction': Direction.EXIT,
        'lead': 'WAP7-3020',
        'locos': 1,
        'coaches': 4,
        'wagons': 0,
        'assign': ('NR-C-2202', 'NR-C-2203', 'NR-C-2204', 'NR-C-2205'),
        'origin': 'NDLS yard',
        'destination': 'Ghaziabad',
    },
)


class Command(BaseCommand):
    help = 'Create or refresh repeatable demo data for the NDLS yard.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--api-key',
            default=None,
            help='Set a known API key for the demo lane instead of generating one.',
        )
        parser.add_argument(
            '--rotate-key',
            action='store_true',
            help='Generate a new API key for the demo lane and print it once.',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help=(
                'Delete demo movements, inventory and plans for NDLS before '
                'seeding. Destructive: use only for a demo rehearsal.'
            ),
        )

    def handle(self, *args, **options):
        expected_start = timezone.now().replace(second=0, microsecond=0)

        with transaction.atomic():
            if options['reset']:
                self._reset()

            station, _ = Station.objects.update_or_create(
                code=STATION_CODE,
                defaults={
                    'name': 'New Delhi',
                    'division': 'Delhi',
                    'zone': 'NR',
                    'is_active': True,
                },
            )

            device, raw_key, key_was_generated = self._ensure_lane(
                station,
                options,
            )
            fleet = self._ensure_fleet()
            plans = self._ensure_plans(station, fleet, expected_start)

        self._report(station, device, fleet, plans, raw_key, key_was_generated)

    def _reset(self):
        station = Station.objects.filter(code=STATION_CODE).first()
        if station is None:
            return

        movements = Movement.objects.filter(station=station).count()
        Movement.objects.filter(station=station).delete()
        inventory, _ = InventoryRecord.objects.filter(station=station).delete()
        plans, _ = MovementPlan.objects.filter(station=station).delete()

        self.stdout.write(
            self.style.WARNING(
                f'Reset: deleted {movements} movements, {plans} plans, '
                f'and {inventory} inventory rows for {STATION_CODE}.'
            )
        )

    def _ensure_lane(self, station, options):
        device = Device.objects.filter(device_id=LANE_ID).first()
        raw_key = None
        key_was_generated = False

        if device is None:
            raw_key = options['api_key'] or secrets.token_urlsafe(24)
            key_was_generated = options['api_key'] is None
            device = Device(
                device_id=LANE_ID,
                station=station,
                label='NDLS yard gate 1',
                lane_mode=Direction.ENTRY,
            )
            device.set_api_key(raw_key)
            device.save()
            LaneModeChange.objects.create(
                device=device,
                direction=Direction.ENTRY,
                note='Initial demo lane mode',
            )
            return device, raw_key, key_was_generated

        if options['api_key']:
            raw_key = options['api_key']
            device.set_api_key(raw_key)
            device.save(update_fields=('api_key_hash', 'updated_at'))
        elif options['rotate_key']:
            raw_key = secrets.token_urlsafe(24)
            key_was_generated = True
            device.set_api_key(raw_key)
            device.save(update_fields=('api_key_hash', 'updated_at'))

        if device.station_id != station.id:
            device.station = station
            device.save(update_fields=('station', 'updated_at'))

        if not device.is_active:
            device.is_active = True
            device.save(update_fields=('is_active', 'updated_at'))

        if device.lane_mode != Direction.ENTRY:
            set_lane_mode(
                device,
                Direction.ENTRY,
                note='Seed reset lane mode',
            )

        return device, raw_key, key_was_generated

    def _ensure_fleet(self):
        fleet = {}
        for item in FLEET:
            stock, _ = RollingStock.objects.update_or_create(
                number=item['number'],
                defaults={
                    'rfid_uid': item['rfid_uid'],
                    'kind': item['kind'],
                    'home_shed': item.get('home_shed', ''),
                    'division': item.get('division', 'Delhi'),
                    'zone': item.get('zone', 'NR'),
                    'is_active': True,
                },
            )
            fleet[stock.number] = stock
        return fleet

    def _ensure_plans(self, station, fleet, expected_start):
        plans = []
        for spec in PLANS:
            # Status is deliberately not reset: a plan already used by a
            # movement must keep its lifecycle.
            plan, _ = MovementPlan.objects.update_or_create(
                plan_code=spec['plan_code'],
                defaults={
                    'station': station,
                    'direction': spec['direction'],
                    'expected_lead_loco': fleet[spec['lead']],
                    'expected_locos': spec['locos'],
                    'expected_coaches': spec['coaches'],
                    'expected_wagons': spec['wagons'],
                    'expected_start': expected_start,
                    'origin': spec['origin'],
                    'destination': spec['destination'],
                },
            )
            for number in spec['assign']:
                PlanAssignment.objects.get_or_create(
                    plan=plan,
                    rolling_stock=fleet[number],
                )
            plans.append(plan)
        return plans

    def _report(self, station, device, fleet, plans, raw_key, generated):
        locos = sum(1 for s in fleet.values() if s.kind == RollingStock.Kind.LOCO)
        coaches = sum(1 for s in fleet.values() if s.kind == RollingStock.Kind.COACH)
        wagons = sum(1 for s in fleet.values() if s.kind == RollingStock.Kind.WAGON)

        self.stdout.write(self.style.SUCCESS('Demo seed complete.'))
        self.stdout.write(f'  Station: {station.code} - {station.name}')
        self.stdout.write(
            f'  Lane: {device.device_id} (lane_mode={device.lane_mode})'
        )
        self.stdout.write(
            f'  Fleet: {locos} locos, {coaches} coaches, {wagons} wagons'
        )
        self.stdout.write(
            '  Plans: ' + ', '.join(plan.plan_code for plan in plans)
        )

        if raw_key:
            label = 'generated' if generated else 'set from --api-key'
            self.stdout.write(
                self.style.WARNING(
                    f'  Device API key ({label}, shown once): {raw_key}'
                )
            )
