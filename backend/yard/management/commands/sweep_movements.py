"""Close movements that have gone quiet.

Run periodically (cron, systemd timer, or a thread in the demo server). The
command is idempotent: closing an already-closed movement is a no-op, so a
double run or an overlapping sweep cannot double-apply anything.
"""

from django.core.management.base import BaseCommand

from yard.movements import close_idle_movements


class Command(BaseCommand):
    help = (
        'Close open movements whose last evidence is older than '
        'MOVEMENT_GAP_SECONDS.'
    )

    def handle(self, *args, **options):
        closed = close_idle_movements()
        if closed:
            self.stdout.write(
                self.style.SUCCESS(f'Closed {closed} idle movement(s).')
            )
        else:
            self.stdout.write('No idle movements to close.')
