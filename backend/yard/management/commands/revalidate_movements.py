"""Validate movements that were closed before validation existed, or revalidate.

CP-04 closed movements without an outcome and left an INFO ``VALIDATION_PENDING``
finding. Running this command once after deploying CP-06 replaces those
placeholders with real findings and outcomes.

It is also the tool for a deliberate revalidation: findings are replaced and
alerts are not duplicated, so running it again is safe.
"""

from django.core.management.base import BaseCommand, CommandError

from yard.models import Movement
from yard.validation import validate_movement


class Command(BaseCommand):
    help = 'Validate closed movements that have no validation outcome yet.'

    def add_arguments(self, parser):
        parser.add_argument(
            'movement',
            nargs='*',
            type=int,
            help='Specific movement ids to validate. Omit to select by flags.',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Revalidate every closed movement, not only pending ones.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List what would be validated without changing anything.',
        )

    def handle(self, *args, **options):
        if options['movement'] and options['all']:
            raise CommandError('Pass movement ids or --all, not both.')

        queryset = Movement.objects.filter(state=Movement.State.CLOSED)

        if options['movement']:
            queryset = queryset.filter(pk__in=options['movement'])
            missing = set(options['movement']) - set(
                queryset.values_list('pk', flat=True)
            )
            if missing:
                raise CommandError(
                    'No closed movement with id(s): '
                    + ', '.join(str(pk) for pk in sorted(missing))
                )
        elif not options['all']:
            queryset = queryset.filter(validated_at__isnull=True)

        movements = list(queryset.order_by('started_at', 'pk'))
        if not movements:
            self.stdout.write('Nothing to validate.')
            return

        if options['dry_run']:
            for movement in movements:
                self.stdout.write(
                    f'would validate #{movement.pk} {movement.direction} '
                    f'{movement.device.device_id} at {movement.started_at:%Y-%m-%d %H:%M:%S}'
                )
            self.stdout.write(
                self.style.WARNING(
                    f'{len(movements)} movement(s) would be validated.'
                )
            )
            return

        counts = {}
        for movement in movements:
            validated = validate_movement(movement)
            counts[validated.validation_result] = (
                counts.get(validated.validation_result, 0) + 1
            )

        summary = ', '.join(
            f'{result} {count}' for result, count in sorted(counts.items())
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Validated {len(movements)} movement(s): {summary}.'
            )
        )
