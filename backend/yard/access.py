"""Yard-master session identity, station scoping, and role checks.

Device (firmware) identity lives in ``yard.auth`` and is deliberately separate:
a dashboard session can never authenticate ingestion, and a device key can
never authenticate a dashboard request.

Every dashboard account is bound to exactly one station through ``StaffProfile``.
There is no administrator exception: an ``ADMIN`` role means "full rights *at
this station*", not "every station". A row belonging to another station is
reported as 404 rather than 403, so one station cannot confirm that another
station's lane exists.
"""

from django.http import Http404
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission

from .exceptions import (
    CsrfFailed,
    NoStaffProfile,
    RoleNotPermitted,
    StaffInactive,
    StationInactive,
)
from .models import StaffProfile


class YardSessionAuthentication(SessionAuthentication):
    """Session authentication that answers 401, not 403, when logged out.

    DRF turns an unauthenticated ``NotAuthenticated`` into a 403 when the
    authenticator has no ``WWW-Authenticate`` header. A browser dashboard has to
    tell "please log in" (401) apart from "logged in but not allowed" (403), so
    this authenticator declares a scheme. Browsers do not prompt for a
    non-Basic scheme, so nothing pops up.

    CSRF failures are translated to the dashboard's stable ``CSRF_FAILED`` code
    instead of DRF's generic ``PERMISSION_DENIED``.
    """

    def authenticate_header(self, request):
        return 'Session'

    def enforce_csrf(self, request):
        try:
            super().enforce_csrf(request)
        except PermissionDenied as exc:
            raise CsrfFailed(str(exc.detail)) from exc


def enforce_csrf(request):
    """Apply the same CSRF check to a view that does not authenticate.

    ``login`` and ``logout`` must work before a session exists, so they disable
    DRF authentication. That also disables the automatic CSRF enforcement, and
    login CSRF is a real attack: it can silently sign a victim into the
    attacker's account. This restores the check explicitly.
    """
    YardSessionAuthentication().enforce_csrf(request)


def staff_profile_for(user):
    """Return the active staff profile, or raise a typed 401/403."""
    if user is None or not getattr(user, 'is_authenticated', False):
        raise NotAuthenticated()

    if not user.is_active:
        raise StaffInactive()

    try:
        profile = user.staff_profile
    except StaffProfile.DoesNotExist:
        raise NoStaffProfile() from None

    if not profile.is_active:
        raise StaffInactive()
    if not profile.station.is_active:
        raise StationInactive()

    return profile


def station_for(user):
    """The station this account may see.

    This is the scoping primitive every dashboard view must use. It raises
    rather than returning ``None`` so a forgotten check cannot silently widen
    access to every station.
    """
    return staff_profile_for(user).station


def get_station_object_or_404(model, station, **lookup):
    """Fetch one station-owned row, or 404.

    Another station's row and a nonexistent row are indistinguishable on
    purpose: a 403 would leak the existence of the other station's objects.
    """
    try:
        return model.objects.get(station=station, **lookup)
    except model.DoesNotExist:
        raise Http404() from None


def is_operator(profile):
    return profile.role in (
        StaffProfile.Role.ADMIN,
        StaffProfile.Role.YARD_MASTER,
    )


class IsStationStaff(BasePermission):
    """Authenticated, active, and attached to an active station."""

    message = 'A station staff profile is required.'

    def has_permission(self, request, view):
        # Raises the specific error (401 / NO_STAFF_PROFILE / STAFF_INACTIVE /
        # STATION_INACTIVE) instead of returning False, so the dashboard can
        # show why access was refused.
        request.staff_profile = staff_profile_for(request.user)
        return True


class IsYardOperator(IsStationStaff):
    """A station-bound account whose role may change yard state."""

    def has_permission(self, request, view):
        super().has_permission(request, view)
        if not is_operator(request.staff_profile):
            raise RoleNotPermitted()
        return True
