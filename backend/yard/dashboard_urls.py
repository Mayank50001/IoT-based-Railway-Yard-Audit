"""HTML routes for the operator dashboard.

Mounted at the site root so the demo URL is simply the host name. The JSON API
stays under ``/api/`` and the Django admin under ``/admin/``.
"""

from django.urls import path

from . import dashboard_views as views

app_name = 'dashboard'

urlpatterns = [
    path('', views.OverviewPage.as_view(), name='overview'),
    path('login/', views.LoginPage.as_view(), name='login'),
    path('logout/', views.logout_page, name='logout'),
    path('lanes/', views.LanesPage.as_view(), name='lanes'),
    path('movements/', views.MovementsPage.as_view(), name='movements'),
    path('movements/<int:pk>/', views.MovementPage.as_view(), name='movement'),
    path('inventory/', views.InventoryPage.as_view(), name='inventory'),
    path('alerts/', views.AlertsPage.as_view(), name='alerts'),
]
