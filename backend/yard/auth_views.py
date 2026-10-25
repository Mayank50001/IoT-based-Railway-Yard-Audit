"""Yard-master session endpoints: csrf, login, logout, me.

The dashboard is Django-served and same-origin, so a session cookie plus CSRF
is the whole authentication story. There are no API tokens for humans: a token
in browser storage is a larger attack surface for no benefit here, and a session
cookie can be revoked server-side by deactivating the account.

``login`` and ``logout`` disable DRF authentication because they must work
before a session exists (or after it was destroyed). They enforce CSRF
explicitly instead; see ``yard.access.enforce_csrf``.
"""

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.middleware.csrf import get_token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import (
    IsStationStaff,
    enforce_csrf,
    is_operator,
    staff_profile_for,
)
from .exceptions import InvalidCredentials
from .serializers import LoginSerializer
from .throttles import LoginThrottle


def _identity(profile):
    return {
        'user': {
            'username': profile.user.get_username(),
            'first_name': profile.user.first_name,
            'last_name': profile.user.last_name,
        },
        'station': {
            'code': profile.station.code,
            'name': profile.station.name,
        },
        'role': profile.role,
        'can_write': is_operator(profile),
    }


class CsrfView(APIView):
    """Issue a CSRF cookie before the first unsafe request.

    ``get_token()`` returns a masked token suitable for the ``X-CSRFToken``
    header and marks the request so ``CsrfViewMiddleware`` writes the cookie.
    The dashboard can read either the cookie or the JSON value.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request):
        return Response({'csrfToken': get_token(request)})


class LoginView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (LoginThrottle,)

    def post(self, request):
        enforce_csrf(request)

        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # ``authenticate`` returns None for an unknown user, a wrong password,
        # and a deactivated Django user alike. One generic 401 avoids telling an
        # attacker which usernames exist.
        user = authenticate(
            request._request,
            username=serializer.validated_data['username'],
            password=serializer.validated_data['password'],
        )
        if user is None:
            raise InvalidCredentials()

        # Check the staff profile *before* creating a session: an account that
        # cannot use the dashboard should not receive a working cookie.
        profile = staff_profile_for(user)

        django_login(request._request, user)
        return Response(_identity(profile))


class LogoutView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request):
        enforce_csrf(request)
        # AuthenticationMiddleware already populated the underlying HttpRequest,
        # so no DRF authenticator is needed to find the session to destroy.
        django_logout(request._request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = (IsStationStaff,)

    def get(self, request):
        return Response(_identity(request.staff_profile))
