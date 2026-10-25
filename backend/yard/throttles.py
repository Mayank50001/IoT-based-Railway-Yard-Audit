"""Throttles for the session surface.

Only login is throttled. Reads are cheap and already require a session, so
rate-limiting them would mostly punish a dashboard that polls. Login is the one
unauthenticated write, and it is the one worth guessing passwords against.
"""

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle

DEFAULT_LOGIN_RATE = '10/min'


class LoginThrottle(SimpleRateThrottle):
    """Per-client-IP limit on login attempts.

    Keyed by IP rather than username on purpose: keying by username would let an
    attacker spray one password across many accounts, and the IP is what an
    operator can actually recognise in a small yard installation. The trade-off
    is that everyone behind one NAT shares the budget; at 10/min that is fine
    for a handful of yard masters.
    """

    scope = 'login'

    def get_rate(self):
        return getattr(settings, 'LOGIN_THROTTLE_RATE', DEFAULT_LOGIN_RATE)

    def get_ident(self, request):
        # DRF's default trusts X-Forwarded-For, which any client can spoof and
        # thereby reset its own login budget. This deployment talks to the
        # device/LAN directly, so the socket address is the honest identity.
        # If a reverse proxy is ever added, this must read a header the proxy
        # sets and strips, not one the client controls.
        return request.META.get('REMOTE_ADDR') or 'unknown'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }
