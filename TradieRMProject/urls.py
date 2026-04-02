"""
TradieRMProject/urls.py

Root URL configuration for the TradieRM backend.

All API endpoints are prefixed with /api/.
The DRF router handles standard resource URLs for all registered ViewSets.
Non-resource endpoints (auth, webhook, UC4 token submission) are registered manually.

Endpoint summary:

    Resource endpoints (router-generated):
        /api/customers/                         CustomerViewSet       (admin)
        /api/customers/create-with-job/         UC2 combined create   (admin)
        /api/technicians/                       TechnicianViewSet     (admin)
        /api/inventory/                         InventoryViewSet      (admin)
        /api/job-inventory/                     JobInventoryViewSet   (admin)
        /api/jobs/                              JobViewSet            (admin, technician, customer)
        /api/jobs/{id}/update-status/           UC9 status update     (admin, technician)
        /api/bookings/                          BookingViewSet        (admin)
        /api/bookings/{id}/send-request/        UC4 email link        (admin)
        /api/bookings/{id}/allocate/            UC7 allocate          (admin)
        /api/schedule/                          ScheduleBlockViewSet  (admin, technician)
        /api/invoices/                          InvoiceViewSet        (admin)
        /api/client-requests/                   ClientRequestViewSet  (admin)
        /api/client-requests/{id}/process/      UC1 process request   (admin)
        /api/ai-suggestions/                    AIResponseSuggestionViewSet (admin, technician)

    Authentication endpoints:
        /api/auth/login/                        Obtain token          (public)
        /api/auth/logout/                       Invalidate token      (authenticated)
        /api/auth/me/                           Current user identity (authenticated)

    Webhook and public endpoints:
        /api/webhook/job-request/               UC8 inbound webhook   (public)
        /api/booking/submit/                    UC4 token form submit (public)

    Django admin:
        /admin/
"""

from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

from tradiePrototype.views import (
    CustomerViewSet,
    TechnicianViewSet,
    InventoryViewSet,
    JobInventoryViewSet,
    JobViewSet,
    BookingViewSet,
    ScheduleBlockViewSet,
    InvoiceViewSet,
    ClientRequestViewSet,
    AIResponseSuggestionViewSet,
    webhook_intake,
    booking_token_submit,
    logout,
    me,
)

# Register all ViewSets with the DRF router.
router = DefaultRouter()
router.register(r'customers',       CustomerViewSet,             basename='customer')
router.register(r'technicians',     TechnicianViewSet,           basename='technician')
router.register(r'inventory',       InventoryViewSet,            basename='inventory')
router.register(r'job-inventory',   JobInventoryViewSet,         basename='jobinventory')
router.register(r'jobs',            JobViewSet,                  basename='job')
router.register(r'bookings',        BookingViewSet,              basename='booking')
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
    path('api/auth/login/',  obtain_auth_token, name='api-token-auth'),
    path('api/auth/logout/', logout,            name='api-logout'),
    path('api/auth/me/',     me,                name='api-me'),

    # UC8 -- Inbound job request from the external website. No authentication required.
    path('api/webhook/job-request/', webhook_intake, name='webhook-intake'),

    # UC4 -- Customer booking form submission via signed token link. No authentication required.
    path('api/booking/submit/', booking_token_submit, name='booking-token-submit'),
]