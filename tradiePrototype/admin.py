"""
tradiePrototype/admin.py

Django admin registrations for all TradieRM models.

Provides list views, search, filtering, and inline editing for all models
managed through the Django admin panel.

Registration order mirrors the model dependency graph:
    Customer, Technician, Job, Inventory, JobInventory, Booking,
    ScheduleBlock, Invoice, Notification, ClientRequest,
    AIResponseSuggestion, UserProfile.
"""

from django.contrib import admin

from .models import (
    Customer,
    Technician,
    Job,
    Inventory,
    JobInventory,
    Booking,
    ScheduleBlock,
    Invoice,
    Notification,
    ClientRequest,
    AIResponseSuggestion,
    UserProfile,
)


# ---------------------------------------------------------------------------
# Inline classes
# ---------------------------------------------------------------------------

class JobInventoryInline(admin.TabularInline):
    """
    Displays inventory items assigned to a job directly on the Job admin page.
    line_total is a computed property on the model and must be read-only.
    """

    model           = JobInventory
    extra           = 0
    fields          = ['inventory', 'quantity_used', 'line_total']
    readonly_fields = ['line_total']


class BookingInline(admin.TabularInline):
    """
    Displays bookings linked to a job directly on the Job admin page.
    distance is populated automatically during allocation and must be read-only.
    """

    model           = Booking
    extra           = 0
    fields          = ['physical_address', 'date', 'time', 'status', 'distance', 'technician']
    readonly_fields = ['distance']


class AIResponseSuggestionInline(admin.TabularInline):
    """
    Displays AI suggestions linked to a client request on the ClientRequest admin page.
    Status and review timestamps are managed by the view layer and must be read-only.
    """

    model           = AIResponseSuggestion
    extra           = 0
    fields          = ['approval_status', 'reviewed_by_role', 'reviewed_at', 'sent_at']
    readonly_fields = ['approval_status', 'reviewed_at', 'sent_at']


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """
    UC2, UC7, UC8, UC9 -- Customer records.

    status reflects the soft-delete state set by UC8 (Delete Customer).
    Active records are shown by default; inactive records are retained
    as an audit log.
    """

    list_display    = ['id', 'first_name', 'last_name', 'email_address',
                       'telephone_number', 'status', 'created_at']
    search_fields   = ['first_name', 'last_name', 'email_address']
    list_filter     = ['status']
    ordering        = ['last_name', 'first_name']
    readonly_fields = ['created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Technician
# ---------------------------------------------------------------------------

@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    """
    UC11, UC13 -- Technician records.
    is_active reflects soft-delete state set by UC13 (Delete Technician).
    """

    # NEW
    list_display = ['id', 'first_name', 'last_name', 'email_address', 'telephone_number', 'skill', 'hourly_rate',
                    'status']
    search_fields   = ['first_name', 'last_name', 'email_address']
    list_filter     = ['status', 'gender']
    ordering        = ['last_name', 'first_name']
    readonly_fields = ['created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """
    UC2, UC16, UC17, UC23, UC24 -- Job records.
    start_time and end_time are set automatically by the view layer (UC23, UC24)
    and are read-only here to prevent manual corruption of invoice calculations.
    """

    list_display    = ['id', 'subject', 'customer', 'technician', 'status', 'source', 'start_time', 'end_time', 'created_at']
    list_filter     = ['status', 'source']
    search_fields   = ['subject', 'customer__last_name', 'technician__last_name']
    ordering        = ['-created_at']
    readonly_fields = ['start_time', 'end_time', 'created_at', 'updated_at']
    inlines         = [JobInventoryInline, BookingInline]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    """
    UC18, UC19, UC20 -- Inventory (spare parts and materials) records.
    status is derived automatically from quantity on save and must be read-only.
    """

    list_display    = ['id', 'name', 'quantity', 'cost', 'status', 'created_at']
    search_fields   = ['name']
    list_filter     = ['status']
    ordering        = ['name']
    readonly_fields = ['status', 'created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Job Inventory
# ---------------------------------------------------------------------------

@admin.register(JobInventory)
class JobInventoryAdmin(admin.ModelAdmin):
    """
    UC21, UC22 -- Parts assigned to a job.
    line_total is a computed property on the model and must be read-only.
    """

    list_display    = ['id', 'job', 'inventory', 'quantity_used', 'line_total']
    search_fields   = ['inventory__name', 'job__subject']
    readonly_fields = ['line_total']


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """
    UC3, UC4, UC8, UC9, UC10, UC15 -- Booking (scheduling) records.
    distance is calculated via OpenRouteService during allocation and must be read-only.
    booking_token and token_expires_at are managed by the view layer (UC4).
    """

    list_display    = ['id', 'job', 'customer', 'technician', 'date', 'time', 'status', 'distance']
    list_filter     = ['status']
    search_fields   = ['customer__last_name', 'job__subject', 'physical_address']
    ordering        = ['date', 'time']
    readonly_fields = ['distance', 'booking_token', 'token_expires_at', 'created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Schedule Block
# ---------------------------------------------------------------------------

@admin.register(ScheduleBlock)
class ScheduleBlockAdmin(admin.ModelAdmin):
    """
    UC26, UC27 -- Technician calendar time blocks (job and travel).
    """

    list_display = ['id', 'technician', 'block_type', 'start_time', 'end_time', 'job']
    list_filter  = ['block_type', 'technician']
    ordering     = ['technician', 'start_time']


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """
    UC24, UC25 -- Invoice records.

    All derived cost fields are read-only because they are calculated by
    Invoice.calculate_totals() and must not be edited directly.

    Editable fields (set by the administrator during UC25 review):
        hours_taken, distance_rate, service_charge_percentage, notes.

    Read-only derived fields:
        labour_cost, distance_cost, parts_cost, subtotal,
        service_charge, total_cost, date_generated, updated_at.
    """

    list_display    = [
        'id', 'job', 'technician', 'status',
        'hours_taken', 'labour_cost', 'distance_cost',
        'parts_cost', 'service_charge', 'total_cost', 'date_generated',
    ]
    list_filter     = ['status']
    ordering        = ['-date_generated']
    readonly_fields = [
        'labour_cost', 'distance_cost', 'parts_cost',
        'subtotal', 'service_charge', 'total_cost',
        'date_generated', 'updated_at',
    ]


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """
    UC24, step 10 -- In-system administrator notifications.

    Created automatically when a technician completes a job.
    read_at is set by the mark_as_read() model method and must be read-only.
    """

    list_display    = ['id', 'recipient', 'notification_type', 'job', 'invoice', 'is_read', 'created_at']
    list_filter     = ['is_read', 'notification_type']
    search_fields   = ['recipient__username', 'message']
    ordering        = ['-created_at']
    readonly_fields = ['read_at', 'created_at']


# ---------------------------------------------------------------------------
# Client Request
# ---------------------------------------------------------------------------

@admin.register(ClientRequest)
class ClientRequestAdmin(admin.ModelAdmin):
    """
    UC1 -- Inbound job requests received via the public webhook endpoint.
    acknowledged_at, raw_payload, and source_ip are set by the view layer.
    """

    list_display    = ['id', 'first_name', 'last_name', 'email_address', 'status', 'acknowledged_at', 'date_received']
    list_filter     = ['status']
    search_fields   = ['first_name', 'last_name', 'email_address', 'subject']
    ordering        = ['-date_received']
    readonly_fields = ['acknowledged_at', 'raw_payload', 'source_ip', 'date_received', 'updated_at']
    inlines         = [AIResponseSuggestionInline]


# ---------------------------------------------------------------------------
# AI Response Suggestion
# ---------------------------------------------------------------------------

@admin.register(AIResponseSuggestion)
class AIResponseSuggestionAdmin(admin.ModelAdmin):
    """
    BR4, BR5 -- AI-generated response suggestions (formally descoped).
    Retained for audit purposes. All timestamp fields are read-only.
    """

    list_display    = ['id', 'client_request', 'approval_status', 'reviewed_by_role', 'reviewed_at', 'sent_at']
    list_filter     = ['approval_status', 'reviewed_by_role']
    ordering        = ['-created_at']
    readonly_fields = ['created_at', 'updated_at', 'reviewed_at', 'sent_at']


# ---------------------------------------------------------------------------
# User Profile
# ---------------------------------------------------------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """
    RBAC -- Role assignments for all system users.
    """

    list_display  = ['user', 'role', 'phone']
    list_filter   = ['role']
    search_fields = ['user__username', 'user__email']