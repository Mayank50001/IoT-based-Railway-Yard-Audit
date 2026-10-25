from django.urls import path, re_path

from .views import IngestEventsView

app_name = 'yard'

urlpatterns = [
    path('ingest/events', IngestEventsView.as_view(), name='ingest-events'),
    # Accept the trailing-slash form as well: APPEND_SLASH would redirect a
    # POST and lose the body, which looks like a mysterious 500 to firmware.
    re_path(r'^ingest/events/$', IngestEventsView.as_view()),
]
