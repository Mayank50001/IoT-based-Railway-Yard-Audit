"""Django-served dashboard pages.

The pages are deliberately thin: they prove a session, resolve the account's
station, and render a shell. Data comes from the station-scoped JSON API so the
dashboard and any future client share one set of scoping and validation rules.

Two boundaries are enforced here as well as in the API:

* A page for another station's movement is a 404, not a page that fails later.
* An account without an active station profile gets an explanatory 403 page
  instead of a shell that would only produce API errors.

Timestamps remain UTC in the database and API; the browser renders them in IST.
"""

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from rest_framework.exceptions import APIException

from .access import get_station_object_or_404, is_operator, staff_profile_for
from .models import Movement


class DashboardPage(TemplateView):
    """Base page: require a session and an active, station-bound profile."""

    active_page = ''

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            query = urlencode({'next': request.get_full_path()})
            return redirect(f"{reverse('dashboard:login')}?{query}")

        try:
            self.profile = staff_profile_for(request.user)
        except APIException as exc:
            return render(
                request,
                'yard/denied.html',
                {
                    'code': getattr(exc, 'default_code', 'ACCESS_DENIED'),
                    'detail': str(exc.detail),
                    'username': request.user.get_username(),
                },
                status=exc.status_code,
            )

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        username = self.profile.user.get_username()
        context.update(
            {
                'active_page': self.active_page,
                'profile': self.profile,
                'station': self.profile.station,
                'can_write': is_operator(self.profile),
                'role_label': self.profile.get_role_display(),
                'username': username,
                'display_name': self.profile.user.get_full_name() or username,
                'movement_gap_seconds': settings.MOVEMENT_GAP_SECONDS,
            }
        )
        return context


class LoginPage(TemplateView):
    """Public sign-in page. An existing session goes straight to the yard."""

    template_name = 'yard/login.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('dashboard:overview')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The browser validates this again before navigating. Keeping it in the
        # query string means a deep link survives the login round trip.
        context['next'] = self.request.GET.get('next') or '/'
        return context


@require_POST
def logout_page(request):
    """End the session from the dashboard's plain HTML form.

    The JSON logout endpoint exists for API clients. The page form is a normal
    same-origin POST, so Django's CSRF middleware protects it; there is no
    reason to duplicate the check or to exempt the view.
    """
    django_logout(request)
    return redirect('dashboard:login')


class OverviewPage(DashboardPage):
    template_name = 'yard/overview.html'
    active_page = 'overview'


class LanesPage(DashboardPage):
    template_name = 'yard/lanes.html'
    active_page = 'lanes'


class MovementsPage(DashboardPage):
    template_name = 'yard/movements.html'
    active_page = 'movements'


class MovementPage(DashboardPage):
    """One movement's audit trail.

    Resolving the movement here is what makes a foreign station's id a 404
    before any JavaScript runs. The API repeats the check for its own callers.
    """

    template_name = 'yard/movement_detail.html'
    active_page = 'movements'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movement'] = get_station_object_or_404(
            Movement,
            self.profile.station,
            pk=self.kwargs['pk'],
        )
        return context


class InventoryPage(DashboardPage):
    template_name = 'yard/inventory.html'
    active_page = 'inventory'


class AlertsPage(DashboardPage):
    template_name = 'yard/alerts.html'
    active_page = 'alerts'
