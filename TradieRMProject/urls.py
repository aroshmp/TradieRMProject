"""
TradieRMProject/urls.py

Root URL configuration for the TradieRM backend.

All API endpoints are prefixed with /api/.
The DRF router handles standard resource URLs for all registered ViewSets.
Non-resource endpoints (auth, webhook) are registered manually below the router.

Endpoint summary:
    /api/customers/             -- CustomerViewSet       (admin only)
    /api/technicians/           -- TechnicianViewSet     (admin only)
    /api/jobs/                  -- JobViewSet            (admin, customer)
    /api/job-parts/             -- JobPartViewSet        (admin only)
    /api/schedule/              -- ScheduleBlockViewSet  (admin, technician)
    /api/invoices/              -- InvoiceViewSet        (admin only)
    /api/client-requests/       -- ClientRequestViewSet  (admin only)
    /api/ai-suggestions/        -- AIResponseSuggestionViewSet (admin, technician)

    /api/auth/login/            -- Obtain auth token (POST, public)
    /api/auth/logout/           -- Invalidate auth token (POST, authenticated)
    /api/auth/me/               -- Current user identity and role (GET, authenticated)

    /api/webhook/job-request/   -- Inbound job request from external website (POST, public)

    /admin/                     -- Django admin panel
"""

from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

from tradiePrototype.views import (
    CustomerViewSet,
    TechnicianViewSet,
    JobViewSet,
    JobPartViewSet,
    ScheduleBlockViewSet,
    InvoiceViewSet,
    ClientRequestViewSet,
    AIResponseSuggestionViewSet,
    webhook_intake,
    logout,
    me,
)

# Register all ViewSets with the default router.
# The router automatically generates list, detail, and action URLs for each.
router = DefaultRouter()
router.register(r'customers',       CustomerViewSet,             basename='customer')
router.register(r'technicians',     TechnicianViewSet,           basename='technician')
router.register(r'jobs',            JobViewSet,                  basename='job')
router.register(r'job-parts',       JobPartViewSet,              basename='jobpart')
router.register(r'schedule',        ScheduleBlockViewSet,        basename='schedule')
router.register(r'invoices',        InvoiceViewSet,              basename='invoice')
router.register(r'client-requests', ClientRequestViewSet,        basename='clientrequest')
router.register(r'ai-suggestions',  AIResponseSuggestionViewSet, basename='aisuggestion')

urlpatterns = [
    # Django admin panel.
    path('admin/', admin.site.urls),

    # All router-generated API endpoints.
    path('api/', include(router.urls)),

    # Authentication endpoints.
    # Login uses DRF's built-in token view. Logout and identity check are custom.
    path('api/auth/login/',  obtain_auth_token, name='api-token-auth'),
    path('api/auth/logout/', logout,            name='api-logout'),
    path('api/auth/me/',     me,                name='api-me'),

    # Webhook endpoint for inbound job requests from the external website.
    # No authentication required -- validated by payload structure only.
    path('api/webhook/job-request/', webhook_intake, name='webhook-intake'),
]