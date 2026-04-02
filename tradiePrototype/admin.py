"""
tradiePrototype/admin.py

Django admin registrations for all TradieRM models.

Provides list views, search, filtering, and inline editing
for all models managed through the Django admin panel.
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
    line_total is read-only as it is a computed property.
    """

    model           = JobInventory
    extra           = 0
    fields          = ['inventory', 'quantity_used', 'line_total']
    readonly_fields = ['line_total']


class BookingInline(admin.TabularInline):
    """
    Displays bookings linked to a job directly on the Job admin page.
    """

    model           = Booking
    extra           = 0
    fields          = ['physical_address', 'date', 'time', 'status', 'distance', 'technician']
    readonly_fields = ['distance']


class AIResponseSuggestionInline(admin.TabularInline):
    """
    Displays AI suggestions linked to a client request on the ClientRequest admin page.
    Status and review timestamps are read-only as they are managed by the view layer.
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
    list_display  = ['id', 'first_name', 'last_name', 'email', 'phone', 'created_at']
    search_fields = ['first_name', 'last_name', 'email']
    ordering      = ['last_name', 'first_name']


# ---------------------------------------------------------------------------
# Technician
# ---------------------------------------------------------------------------

@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display  = ['id', 'first_name', 'last_name', 'email', 'phone', 'skill', 'is_active']
    search_fields = ['first_name', 'last_name', 'email']
    list_filter   = ['is_active', 'gender']
    ordering      = ['last_name', 'first_name']


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display  = ['id', 'subject', 'customer', 'technician', 'status', 'source', 'created_at']
    list_filter   = ['status', 'source']
    search_fields = ['subject', 'customer__last_name', 'technician__last_name']
    ordering      = ['-created_at']
    inlines       = [JobInventoryInline, BookingInline]
    readonly_fields = ['created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display  = ['id', 'name', 'quantity', 'cost', 'status', 'created_at']
    search_fields = ['name']
    list_filter   = ['status']
    ordering      = ['name']
    readonly_fields = ['status', 'created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Job Inventory
# ---------------------------------------------------------------------------

@admin.register(JobInventory)
class JobInventoryAdmin(admin.ModelAdmin):
    list_display  = ['id', 'job', 'inventory', 'quantity_used', 'line_total']
    search_fields = ['inventory__name', 'job__subject']
    readonly_fields = ['line_total']


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
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
    list_display = ['id', 'technician', 'block_type', 'start_time', 'end_time', 'job']
    list_filter  = ['block_type', 'technician']
    ordering     = ['technician', 'start_time']


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display    = [
        'id', 'job', 'technician', 'status',
        'hours_taken', 'labour_cost', 'distance_cost',
        'parts_cost', 'total_cost', 'date_generated',
    ]
    list_filter     = ['status']
    ordering        = ['-date_generated']
    readonly_fields = [
        'labour_cost', 'distance_cost', 'parts_cost',
        'subtotal', 'markup', 'total_cost',
        'date_generated', 'updated_at',
    ]


# ---------------------------------------------------------------------------
# Client Request
# ---------------------------------------------------------------------------

@admin.register(ClientRequest)
class ClientRequestAdmin(admin.ModelAdmin):
    list_display    = ['id', 'contact_name', 'contact_email', 'status', 'acknowledged_at', 'created_at']
    list_filter     = ['status']
    search_fields   = ['contact_name', 'contact_email', 'subject']
    ordering        = ['-created_at']
    readonly_fields = ['acknowledged_at', 'raw_payload', 'source_ip', 'created_at', 'updated_at']
    inlines         = [AIResponseSuggestionInline]


# ---------------------------------------------------------------------------
# AI Response Suggestion
# ---------------------------------------------------------------------------

@admin.register(AIResponseSuggestion)
class AIResponseSuggestionAdmin(admin.ModelAdmin):
    list_display    = ['id', 'client_request', 'approval_status', 'reviewed_by_role', 'reviewed_at', 'sent_at']
    list_filter     = ['approval_status', 'reviewed_by_role']
    ordering        = ['-created_at']
    readonly_fields = ['created_at', 'updated_at', 'reviewed_at', 'sent_at']


# ---------------------------------------------------------------------------
# User Profile
# ---------------------------------------------------------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'role', 'phone']
    list_filter   = ['role']
    search_fields = ['user__username', 'user__email']