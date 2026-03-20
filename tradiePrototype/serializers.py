"""
core/serializers.py – All serializers in one place.
"""

from rest_framework import serializers
from .models import (
    Customer, Technician, Job, JobPart, ScheduleBlock,
    Invoice, ClientRequest, AIResponseSuggestion,
)


# ── Customers & Technicians ───────────────────────────────────────────────────

class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Customer
        fields = '__all__'


class TechnicianSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Technician
        fields = '__all__'


# ── Jobs & Parts ──────────────────────────────────────────────────────────────

class JobPartSerializer(serializers.ModelSerializer):
    total_cost = serializers.ReadOnlyField()

    class Meta:
        model  = JobPart
        fields = '__all__'


class JobSerializer(serializers.ModelSerializer):
    parts        = JobPartSerializer(many=True, read_only=True)
    is_completed = serializers.ReadOnlyField()

    class Meta:
        model  = Job
        fields = '__all__'


class JobCreateSerializer(serializers.ModelSerializer):
    """Used for creating a job (manual or via webhook)."""
    class Meta:
        model  = Job
        fields = ['customer', 'title', 'description', 'job_address', 'scheduled_start',
                  'scheduled_end', 'source']


# ── Schedule ──────────────────────────────────────────────────────────────────

class ScheduleBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ScheduleBlock
        fields = '__all__'


# ── Invoices ──────────────────────────────────────────────────────────────────

class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model         = Invoice
        fields        = '__all__'
        read_only_fields = ['parts_total', 'labour_total', 'grand_total', 'created_at', 'updated_at']


# ── Communications ────────────────────────────────────────────────────────────

class ClientRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model         = ClientRequest
        fields        = '__all__'
        read_only_fields = ['status', 'acknowledged_at', 'created_at', 'updated_at']


class WebhookInboundSerializer(serializers.Serializer):
    """Validates the raw payload from an external website contact form (BR2)."""
    name    = serializers.CharField(max_length=200)
    email   = serializers.EmailField()
    phone   = serializers.CharField(max_length=20, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=255, required=False, allow_blank=True)
    message = serializers.CharField()


class AIResponseSuggestionSerializer(serializers.ModelSerializer):
    is_sendable = serializers.ReadOnlyField()

    class Meta:
        model         = AIResponseSuggestion
        fields        = '__all__'
        read_only_fields = ['approval_status', 'reviewed_at', 'sent_at', 'created_at', 'updated_at']


class ApproveResponseSerializer(serializers.Serializer):
    """Used when an admin or technician approves an AI suggestion (BR5)."""
    final_response   = serializers.CharField()
    reviewed_by_role = serializers.ChoiceField(choices=AIResponseSuggestion.ReviewerRole.choices)