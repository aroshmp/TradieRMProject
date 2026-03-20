from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import (
    Customer, Technician, Job, JobPart, ScheduleBlock,
    Invoice, ClientRequest, AIResponseSuggestion,
)


class JobPartInline(admin.TabularInline):
    model          = JobPart
    extra          = 0
    fields         = ['name', 'quantity', 'unit_cost', 'total_cost']
    readonly_fields = ['total_cost']


class AIResponseSuggestionInline(admin.TabularInline):
    model          = AIResponseSuggestion
    extra          = 0
    fields         = ['approval_status', 'reviewed_by_role', 'reviewed_at', 'sent_at']
    readonly_fields = ['approval_status', 'reviewed_at', 'sent_at']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display  = ['id', 'first_name', 'last_name', 'email', 'phone', 'created_at']
    search_fields = ['first_name', 'last_name', 'email']


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display  = ['id', 'first_name', 'last_name', 'email', 'phone', 'is_active']
    search_fields = ['first_name', 'last_name', 'email']
    list_filter   = ['is_active']


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display  = ['id', 'title', 'customer', 'technician', 'status', 'scheduled_start', 'source']
    list_filter   = ['status', 'source']
    search_fields = ['title', 'customer__last_name', 'technician__last_name']
    inlines       = [JobPartInline]
    ordering      = ['-created_at']


@admin.register(JobPart)
class JobPartAdmin(admin.ModelAdmin):
    list_display  = ['id', 'job', 'name', 'quantity', 'unit_cost', 'total_cost']
    search_fields = ['name', 'job__title']


@admin.register(ScheduleBlock)
class ScheduleBlockAdmin(admin.ModelAdmin):
    list_display = ['id', 'technician', 'block_type', 'start_time', 'end_time', 'job']
    list_filter  = ['block_type', 'technician']
    ordering     = ['technician', 'start_time']


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display     = ['id', 'job', 'status', 'labour_hours', 'parts_total', 'grand_total', 'due_date']
    list_filter      = ['status']
    readonly_fields  = ['parts_total', 'labour_total', 'grand_total', 'created_at', 'updated_at']


@admin.register(ClientRequest)
class ClientRequestAdmin(admin.ModelAdmin):
    list_display     = ['id', 'contact_name', 'contact_email', 'status', 'acknowledged_at', 'created_at']
    list_filter      = ['status']
    readonly_fields  = ['acknowledged_at', 'raw_payload', 'source_ip', 'created_at', 'updated_at']
    inlines          = [AIResponseSuggestionInline]


@admin.register(AIResponseSuggestion)
class AIResponseSuggestionAdmin(admin.ModelAdmin):
    list_display    = ['id', 'client_request', 'approval_status', 'reviewed_by_role', 'reviewed_at', 'sent_at']
    list_filter     = ['approval_status', 'reviewed_by_role']
    readonly_fields = ['created_at', 'updated_at', 'reviewed_at', 'sent_at']