"""
tradiePrototype/serializers.py

All DRF serializers for TradieRM.

Serializer inventory:
    CustomerSerializer
    TechnicianSerializer          -- read
    TechnicianCreateSerializer    -- write (UC6 admin creation)
    InventorySerializer           -- UC5
    JobInventorySerializer
    JobSerializer                 -- read (full detail)
    JobCreateSerializer           -- write (UC1, UC2)
    JobStatusUpdateSerializer     -- write (UC9)
    BookingSerializer             -- read
    BookingCreateSerializer       -- write (UC3)
    BookingTokenSubmitSerializer  -- write (UC4 unauthenticated)
    ScheduleBlockSerializer
    InvoiceSerializer
    ClientRequestSerializer       -- read
    ClientRequestProcessSerializer -- write (UC1 validation)
    WebhookInboundSerializer      -- write (UC8 payload validation)
    AIResponseSuggestionSerializer
    ApproveResponseSerializer
"""

from rest_framework import serializers
from django.contrib.auth.models import User

from .models import (
    Customer, Technician, Job, Inventory, JobInventory,
    Booking, ScheduleBlock, Invoice,
    ClientRequest, AIResponseSuggestion, UserProfile,
)


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

class CustomerSerializer(serializers.ModelSerializer):
    """Full read/write serializer for Customer records."""

    class Meta:
        model  = Customer
        fields = '__all__'


# ---------------------------------------------------------------------------
# Technician
# ---------------------------------------------------------------------------

class TechnicianSerializer(serializers.ModelSerializer):
    """Read serializer for Technician records."""

    class Meta:
        model  = Technician
        fields = '__all__'


class TechnicianCreateSerializer(serializers.ModelSerializer):
    """
    UC6 -- Write serializer for administrator-created technician records.
    Accepts all profile fields plus a username for login account creation.
    Password is set programmatically by the view (temp password = phone number).
    """

    username = serializers.CharField(
        write_only=True,
        help_text="Username for the technician's login account."
    )

    class Meta:
        model  = Technician
        fields = [
            'first_name', 'last_name', 'gender', 'home_address',
            'phone', 'email', 'skill', 'hourly_rate', 'username',
        ]

    def validate_username(self, value):
        """Reject if the username is already taken."""
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate_email(self, value):
        """Reject if the email is already registered to a technician."""
        if Technician.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "A technician with this email address already exists."
            )
        return value


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class InventorySerializer(serializers.ModelSerializer):
    """
    UC5 -- Full read/write serializer for Inventory records.
    status is read-only -- managed automatically by the model save() method.
    """

    class Meta:
        model            = Inventory
        fields           = '__all__'
        read_only_fields = ['status', 'created_at', 'updated_at']

    def validate_name(self, value):
        """
        UC5, step 7 -- Reject if an inventory item with this name already exists.
        Case-insensitive to prevent near-duplicate entries.
        """
        qs = Inventory.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "An inventory item with this name already exists."
            )
        return value


# ---------------------------------------------------------------------------
# Job Inventory
# ---------------------------------------------------------------------------

class JobInventorySerializer(serializers.ModelSerializer):
    """Serializer for JobInventory with computed line total and inventory detail."""

    line_total     = serializers.ReadOnlyField()
    inventory_name = serializers.ReadOnlyField(source='inventory.name')
    inventory_cost = serializers.ReadOnlyField(source='inventory.cost')

    class Meta:
        model  = JobInventory
        fields = [
            'id', 'job', 'inventory', 'inventory_name',
            'inventory_cost', 'quantity_used', 'line_total',
        ]


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

class JobSerializer(serializers.ModelSerializer):
    """
    Read serializer for Job records.

    customer_detail and technician_detail provide nested objects for
    display purposes. The writable customer and technician FK fields
    remain as integers so JobCreateSerializer is unaffected.

    Includes nested job inventory for full detail views.
    """

    # Nested read-only representations -- used by the frontend for display.
    customer_detail = CustomerSerializer(source='customer', read_only=True)
    technician_detail = TechnicianSerializer(source='technician', read_only=True)

    job_inventory = JobInventorySerializer(many=True, read_only=True)
    is_completed = serializers.ReadOnlyField()
    requires_feedback = serializers.ReadOnlyField()

    class Meta:
        model = Job
        fields = '__all__'


class JobCreateSerializer(serializers.ModelSerializer):
    """
    UC1, UC2 -- Write serializer for creating a new job record.
    Status is always forced to Pending by the view -- not accepted from input.
    """

    class Meta:
        model  = Job
        fields = ['customer', 'subject', 'client_message', 'source', 'client_request']

    def validate(self, data):
        """Enforce that subject and client_message are non-empty strings."""
        if not data.get('subject', '').strip():
            raise serializers.ValidationError({'subject': 'Subject is required.'})
        if not data.get('client_message', '').strip():
            raise serializers.ValidationError(
                {'client_message': 'Client message is required.'}
            )
        return data


class JobStatusUpdateSerializer(serializers.Serializer):
    """
    UC9 -- Validates a job status transition request.

    When the new status is Suspended or Cancelled, the appropriate feedback
    field must be supplied. The role parameter determines which field is required.
    """

    new_status          = serializers.ChoiceField(choices=Job.Status.choices)
    admin_feedback      = serializers.CharField(required=False, allow_blank=True)
    technician_feedback = serializers.CharField(required=False, allow_blank=True)
    role                = serializers.ChoiceField(
        choices=UserProfile.Role.choices, write_only=True
    )

    def validate(self, data):
        """Enforce feedback requirements for Suspended and Cancelled transitions (UC9)."""
        new_status = data.get('new_status')
        role       = data.get('role')

        if new_status in (Job.Status.SUSPENDED, Job.Status.CANCELLED):
            if role == UserProfile.Role.ADMINISTRATOR:
                if not data.get('admin_feedback', '').strip():
                    raise serializers.ValidationError(
                        {'admin_feedback': 'Admin feedback is required when suspending or cancelling a job.'}
                    )
            elif role == UserProfile.Role.TECHNICIAN:
                if not data.get('technician_feedback', '').strip():
                    raise serializers.ValidationError(
                        {'technician_feedback': 'Technician feedback is required when suspending or cancelling a job.'}
                    )
        return data


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

class BookingSerializer(serializers.ModelSerializer):
    """Read serializer for Booking records."""

    class Meta:
        model            = Booking
        fields           = '__all__'
        read_only_fields = [
            'status', 'distance', 'booking_token',
            'token_expires_at', 'created_at', 'updated_at',
        ]


class BookingCreateSerializer(serializers.ModelSerializer):
    """
    UC3 -- Write serializer for administrator-created bookings.
    Status is always set to Pending by the view.
    """

    class Meta:
        model  = Booking
        fields = ['job', 'customer', 'physical_address', 'date', 'time']

    def validate(self, data):
        """Ensure all required booking fields are present and non-empty."""
        if not data.get('physical_address', '').strip():
            raise serializers.ValidationError(
                {'physical_address': 'Physical address is required.'}
            )
        if not data.get('date'):
            raise serializers.ValidationError({'date': 'Booking date is required.'})
        if not data.get('time'):
            raise serializers.ValidationError({'time': 'Booking time is required.'})
        return data


class BookingTokenSubmitSerializer(serializers.Serializer):
    """
    UC4 -- Validates the customer's unauthenticated booking form submission.
    The token identifies the booking. The customer supplies date, time, and address.
    """

    token            = serializers.CharField()
    physical_address = serializers.CharField(max_length=255)
    date             = serializers.DateField()
    time             = serializers.TimeField()

    def validate(self, data):
        """Ensure physical_address is not blank."""
        if not data.get('physical_address', '').strip():
            raise serializers.ValidationError(
                {'physical_address': 'Physical address is required.'}
            )
        return data


# ---------------------------------------------------------------------------
# Schedule Block
# ---------------------------------------------------------------------------

class ScheduleBlockSerializer(serializers.ModelSerializer):
    """Read serializer for ScheduleBlock records."""

    class Meta:
        model  = ScheduleBlock
        fields = '__all__'


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class InvoiceSerializer(serializers.ModelSerializer):
    """
    Read/write serializer for Invoice records.
    All calculated cost fields are read-only.
    """

    class Meta:
        model            = Invoice
        fields           = '__all__'
        read_only_fields = [
            'labour_cost', 'distance_cost', 'parts_cost',
            'subtotal', 'markup', 'total_cost',
            'date_generated', 'updated_at',
        ]


# ---------------------------------------------------------------------------
# Client Request
# ---------------------------------------------------------------------------

class ClientRequestSerializer(serializers.ModelSerializer):
    """Read serializer for ClientRequest records."""

    class Meta:
        model            = ClientRequest
        fields           = '__all__'
        read_only_fields = ['status', 'acknowledged_at', 'created_at', 'updated_at']


class ClientRequestProcessSerializer(serializers.Serializer):
    """
    UC1 -- Validates that the required fields exist on a ClientRequest
    before it is converted into a Customer and Job record.
    All data is sourced from the ClientRequest passed in context, not from submitted input.
    """

    def validate(self, data):
        """Check required fields on the ClientRequest instance in context."""
        request_obj = self.context.get('client_request')
        errors = {}

        if not getattr(request_obj, 'contact_name', '').strip():
            errors['contact_name'] = 'Name is required.'
        if not getattr(request_obj, 'contact_email', '').strip():
            errors['contact_email'] = 'Email address is required.'
        if not getattr(request_obj, 'subject', '').strip():
            errors['subject'] = 'Subject is required.'
        if not getattr(request_obj, 'message', '').strip():
            errors['message'] = 'Client message is required.'

        if errors:
            raise serializers.ValidationError(errors)

        return data


class WebhookInboundSerializer(serializers.Serializer):
    """
    UC8 -- Validates the raw JSON payload from the external website contact form.
    name, email, and message are required. phone and subject are optional.
    """

    name    = serializers.CharField(max_length=200)
    email   = serializers.EmailField()
    phone   = serializers.CharField(max_length=20, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=255, required=False, allow_blank=True)
    message = serializers.CharField()


# ---------------------------------------------------------------------------
# AI Response Suggestion
# ---------------------------------------------------------------------------

class AIResponseSuggestionSerializer(serializers.ModelSerializer):
    """Read serializer for AIResponseSuggestion records."""

    is_sendable = serializers.ReadOnlyField()

    class Meta:
        model            = AIResponseSuggestion
        fields           = '__all__'
        read_only_fields = ['approval_status', 'reviewed_at', 'sent_at', 'created_at', 'updated_at']


class ApproveResponseSerializer(serializers.Serializer):
    """
    BR5 -- Validates an approval submission.
    The reviewer supplies their edited final response and role.
    """

    final_response   = serializers.CharField()
    reviewed_by_role = serializers.ChoiceField(
        choices=AIResponseSuggestion.ReviewerRole.choices
    )