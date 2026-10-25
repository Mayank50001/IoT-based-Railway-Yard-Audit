"""Create or update a station-bound yard-master account.

This creates a *dashboard* user: a Django user plus a ``StaffProfile`` that
binds it to exactly one station. It deliberately does not grant Django admin
access (``is_staff``/``is_superuser``): the admin site is a superuser operations
console and is not station-scoped, so it must not be handed out as if it were
the dashboard role of the same name.
"""

import getpass
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from yard.models import StaffProfile, Station

UserModel = get_user_model()


class Command(BaseCommand):
    help = (
        'Create or update a yard-master account bound to one station. '
        'The account can use the dashboard, not the Django admin site.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument(
            '--station',
            required=True,
            help='Station code, for example NDLS.',
        )
        parser.add_argument(
            '--role',
            choices=StaffProfile.Role.values,
            default=StaffProfile.Role.YARD_MASTER,
        )
        parser.add_argument(
            '--password',
            help=(
                'Password for a new account. Omit it to be prompted; use '
                '--noinput in scripts.'
            ),
        )
        parser.add_argument('--email', default='')
        parser.add_argument('--first-name', default='')
        parser.add_argument('--last-name', default='')
        parser.add_argument(
            '--noinput',
            action='store_true',
            help='Never prompt. A new account then requires --password.',
        )

    def handle(self, *args, **options):
        station = self._station(options['station'])
        username = options['username'].strip()
        if not username:
            raise CommandError('--username must not be empty.')

        user, created = UserModel.objects.get_or_create(username=username)
        user.email = options['email'] or user.email
        user.first_name = options['first_name'] or user.first_name
        user.last_name = options['last_name'] or user.last_name

        password = options['password']
        if created and password is None:
            password = self._prompt_password(options['noinput'])

        if password is not None:
            self._set_password(user, password)

        user.save()

        profile, profile_created = StaffProfile.objects.update_or_create(
            user=user,
            defaults={
                'station': station,
                'role': options['role'],
            },
        )

        state = 'Created' if created else 'Updated'
        profile_state = 'created' if profile_created else 'updated'
        self.stdout.write(
            self.style.SUCCESS(
                f'{state} user {username!r} as {profile.role} at '
                f'{station.code} (staff profile {profile_state}).'
            )
        )

        self._warn_about(user, profile, station)

    def _station(self, code):
        try:
            return Station.objects.get(code__iexact=code.strip())
        except Station.DoesNotExist:
            raise CommandError(f'No station with code {code!r}.')
        except Station.MultipleObjectsReturned:
            raise CommandError(
                f'More than one station matches code {code!r}; station codes '
                'must be unique regardless of case.'
            )

    def _prompt_password(self, noinput):
        if noinput or not sys.stdin.isatty():
            raise CommandError(
                'A new account needs --password (or an interactive terminal to '
                'prompt for one).'
            )
        first = getpass.getpass('Password: ')
        second = getpass.getpass('Password (again): ')
        if first != second:
            raise CommandError('Passwords did not match.')
        return first

    def _set_password(self, user, password):
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages))
        user.set_password(password)

    def _warn_about(self, user, profile, station):
        if not user.is_active:
            self.stdout.write(
                self.style.WARNING(
                    'This user is inactive and cannot sign in.'
                )
            )
        if not profile.is_active:
            self.stdout.write(
                self.style.WARNING(
                    'This staff profile is inactive; the account cannot sign in.'
                )
            )
        if not station.is_active:
            self.stdout.write(
                self.style.WARNING(
                    'This station is inactive; the account cannot sign in.'
                )
            )
        if user.is_staff or user.is_superuser:
            self.stdout.write(
                self.style.WARNING(
                    'This user also has Django admin access, which is not '
                    'station-scoped.'
                )
            )
