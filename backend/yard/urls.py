from django.urls import path, re_path

from .auth_views import CsrfView, LoginView, LogoutView, MeView
from .lane_views import LaneDetailView, LaneListView, LaneModeView
from .read_views import (
    AlertAcknowledgeView,
    AlertListView,
    FindingListView,
    InventoryListView,
    MovementDetailView,
    MovementListView,
    OverviewView,
)
from .views import IngestEventsView

app_name = 'yard'

# Trailing slashes are optional rather than redirected: APPEND_SLASH turns a
# body-carrying POST without a slash into a redirect, and some clients drop the
# body when following it. Device ids are restricted to an unambiguous pattern
# so ``/lanes/<id>/mode`` cannot be swallowed by the detail route.
DEVICE_ID = r'(?P<device_id>[A-Za-z0-9][A-Za-z0-9._:-]*)'

urlpatterns = [
    path('ingest/events', IngestEventsView.as_view(), name='ingest-events'),
    # Accept the trailing-slash form as well: APPEND_SLASH would redirect a
    # POST and lose the body, which looks like a mysterious 500 to firmware.
    re_path(r'^ingest/events/$', IngestEventsView.as_view()),

    re_path(r'^auth/csrf/?$', CsrfView.as_view(), name='auth-csrf'),
    re_path(r'^auth/login/?$', LoginView.as_view(), name='auth-login'),
    re_path(r'^auth/logout/?$', LogoutView.as_view(), name='auth-logout'),
    re_path(r'^auth/me/?$', MeView.as_view(), name='auth-me'),

    re_path(r'^lanes/?$', LaneListView.as_view(), name='lane-list'),
    re_path(rf'^lanes/{DEVICE_ID}/mode/?$', LaneModeView.as_view(), name='lane-mode'),
    re_path(rf'^lanes/{DEVICE_ID}/?$', LaneDetailView.as_view(), name='lane-detail'),

    re_path(r'^overview/?$', OverviewView.as_view(), name='overview'),
    re_path(r'^movements/?$', MovementListView.as_view(), name='movement-list'),
    re_path(
        r'^movements/(?P<pk>\d+)/?$',
        MovementDetailView.as_view(),
        name='movement-detail',
    ),
    re_path(r'^findings/?$', FindingListView.as_view(), name='finding-list'),
    re_path(r'^alerts/?$', AlertListView.as_view(), name='alert-list'),
    re_path(
        r'^alerts/(?P<pk>\d+)/acknowledge/?$',
        AlertAcknowledgeView.as_view(),
        name='alert-acknowledge',
    ),
    re_path(
        r'^inventory/?$', InventoryListView.as_view(), name='inventory-list'
    ),
]
