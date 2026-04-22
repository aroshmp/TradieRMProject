"""
TradieRMProject/urls.py

Root URL configuration for the TradieRM backend.

All API endpoints are prefixed with /api/.
The DRF router handles standard resource URLs for all registered ViewSets.
Non-resource endpoints (auth, webhook, UC4 token submission) are registered manually.

Endpoint summary:

    Customer endpoints:
        /api/customers/                         CustomerViewSet.list / create
        /api/customers/{id}/                    CustomerViewSet.retrieve / update / destroy
        /api/customers/create-with-job/              UC2 -- combined customer + job create
        /api/customers/{id}/add-job-with-booking/    UC6 -- add job and booking to existing customer

    Technician endpoints:
        /api/technicians/                       TechnicianViewSet.list / create
        /api/technicians/{id}/                  TechnicianViewSet.retrieve / update / destroy

    Inventory endpoints:
        /api/inventory/                         InventoryViewSet.list / create
        /api/inventory/{id}/                    InventoryViewSet.retrieve / update / destroy

    Job Inventory endpoints:
        /api/job-inventory/                     JobInventoryViewSet.list / create
        /api/job-inventory/{id}/                JobInventoryViewSet.retrieve / update / destroy

    Job endpoints:
        /api/jobs/                              JobViewSet.list / create
        /api/jobs/{id}/                         JobViewSet.retrieve / update / destroy
        /api/jobs/{id}/update-status/           UC16, UC23, UC24 -- status transition

    Booking endpoints:
        /api/bookings/                          BookingViewSet.list / create
        /api/bookings/{id}/                     BookingViewSet.retrieve / update / destroy
        /api/bookings/{id}/send-request/        UC4  -- email customer booking link
        /api/bookings/{id}/reject/              UC10 -- reject a pending booking
        /api/bookings/{id}/allocate/            UC15 -- allocate technician to booking

    Schedule Block endpoints (raw blocks):
        /api/schedule/                          ScheduleBlockViewSet.list
        /api/schedule/{id}/                     ScheduleBlockViewSet.retrieve

    Technician Schedule endpoints:
        /api/technician-schedule/               UC26 -- list all technicians (admin)
        /api/technician-schedule/{id}/          UC26 -- schedule for one technician (admin)
        /api/technician-schedule/mine/          UC27 -- own schedule (technician)

    Invoice endpoints:
        /api/invoices/                          InvoiceViewSet.list
        /api/invoices/{id}/                     InvoiceViewSet.retrieve / update
        /api/invoices/{id}/recalculate/         UC25 -- recalculate cost fields (admin)
        /api/invoices/{id}/approve/             UC25 -- approve, generate PDF, send email

    Notification endpoints:
        /api/notifications/                     NotificationViewSet.list (admin, unread first)
        /api/notifications/{id}/                NotificationViewSet.retrieve
        /api/notifications/{id}/mark-read/      Mark one notification as read
        /api/notifications/mark-all-read/       Mark all unread notifications as read

    Client Request endpoints:
        /api/client-requests/                   ClientRequestViewSet.list
        /api/client-requests/{id}/              ClientRequestViewSet.retrieve
        /api/client-requests/{id}/process/      UC1 -- convert request to customer + job

    AI Suggestion endpoints (descoped, retained for audit):
        /api/ai-suggestions/                    AIResponseSuggestionViewSet.list
        /api/ai-suggestions/{id}/               AIResponseSuggestionViewSet.retrieve
        /api/ai-suggestions/{id}/approve/       BR5 -- approve suggestion
        /api/ai-suggestions/{id}/reject/        BR5 -- reject suggestion

    Authentication endpoints:
        /api/auth/login/                        Obtain token (public)
        /api/auth/logout/                       Invalidate token (authenticated)
        /api/auth/me/                           Current user identity (authenticated)

    Webhook and public endpoints:
        /api/webhook/job-request/               UC1 -- inbound webhook (public)
        /api/booking/submit/                    UC4 -- customer booking form submit (public)

    Django admin:
        /admin/
"""

from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

from tradiePrototype.viewsets import (
    CustomerViewSet,
    TechnicianViewSet,
    InventoryViewSet,
    JobInventoryViewSet,
    JobViewSet,
    BookingViewSet,
    ScheduleBlockViewSet,
    TechnicianScheduleViewSet,
    InvoiceViewSet,
    NotificationViewSet,
    ClientRequestViewSet,
    AIResponseSuggestionViewSet,
    webhook_intake,
    booking_token_submit,
    logout,
    me,
)

# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

router = DefaultRouter()

# Core resource ViewSets.
router.register(r'customers',       CustomerViewSet,       basename='customer')
router.register(r'technicians',     TechnicianViewSet,     basename='technician')
router.register(r'inventory',       InventoryViewSet,      basename='inventory')
router.register(r'job-inventory',   JobInventoryViewSet,   basename='jobinventory')
router.register(r'jobs',            JobViewSet,            basename='job')
router.register(r'bookings',        BookingViewSet,        basename='booking')

# Schedule ViewSets.
# ScheduleBlockViewSet: raw time blocks (existing, retained for compatibility).
# TechnicianScheduleViewSet: structured UC26/UC27 schedule responses.
router.register(r'schedule',             ScheduleBlockViewSet,       basename='schedule')
router.register(r'technician-schedule',  TechnicianScheduleViewSet,  basename='technician-schedule')

# Invoice and notification ViewSets.
router.register(r'invoices',       InvoiceViewSet,       basename='invoice')
router.register(r'notifications',  NotificationViewSet,  basename='notification')

# Client request and AI suggestion ViewSets.
router.register(r'client-requests', ClientRequestViewSet,        basename='clientrequest')
router.register(r'ai-suggestions',  AIResponseSuggestionViewSet, basename='aisuggestion')


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

urlpatterns = [
    # Django admin panel.
    path('admin/', admin.site.urls),

    # All router-generated API endpoints.
    path('api/', include(router.urls)),

    # Authentication endpoints.
    path('api/auth/login/',  obtain_auth_token, name='api-token-auth'),
    path('api/auth/logout/', logout,            name='api-logout'),
    path('api/auth/me/',     me,                name='api-me'),

    # UC1 -- Inbound job request from the external website. No authentication required.
    path('api/webhook/job-request/', webhook_intake, name='webhook-intake'),

    # UC4 -- Customer booking form submission via signed token link. No authentication required.
    path('api/booking/submit/', booking_token_submit, name='booking-token-submit'),
]