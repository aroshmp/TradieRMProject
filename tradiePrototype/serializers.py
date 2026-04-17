"""
tradiePrototype/serializers.py

All DRF serializers for TradieRM.

Serializer inventory:
    CustomerSerializer
    TechnicianSerializer              -- read
    TechnicianCreateSerializer        -- write (UC11 admin creation)
    InventorySerializer               -- UC18, UC19
    JobInventorySerializer            -- UC21, UC22
    JobSerializer                     -- read (full detail)
    JobCreateSerializer               -- write (UC2, UC4)
    JobStatusUpdateSerializer         -- write (UC16, UC23, UC24)
    BookingSerializer                 -- read
    BookingCreateSerializer           -- write (UC3)
    BookingTokenSubmitSerializer      -- write (UC4 unauthenticated)
    ScheduleBlockSerializer           -- read
    TechnicianScheduleEntrySerializer -- read (UC26, UC27 schedule line item)
    InvoiceSerializer                 -- read (full detail)
    InvoiceRecalculateSerializer      -- write (UC25 recalculate action)
    InvoiceApproveSerializer          -- write (UC25 approve action)
    NotificationSerializer            -- read (UC24 dashboard alerts)
    ClientRequestSerializer           -- read
    ClientRequestProcessSerializer    -- write (UC1 validation)
    WebhookInboundSerializer          -- write (UC1 payload validation)
    AIResponseSuggestionSerializer
    ApproveResponseSerializer
"""

from rest_framework import serializers
from django.contrib.auth.models import User

from .models import (
    Booking,
    Customer,
    Technician,
    Job,
    Inventory,
    JobInventory,
    ScheduleBlock,
    Invoice,
    Notification,
    ClientRequest,
    AIResponseSuggestion,
    UserProfile,
)


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

class CustomerSerializer(serializers.ModelSerializer):
    """
    UC2, UC5, UC7, UC8, UC9 -- Full read/write serializer for Customer records.

    Exposes all database fields using the approved field names from the
    Database Dictionary. The full_name computed property is included as a
    read-only convenience field for list display.
    """

    full_name = serializers.ReadOnlyField()

    class Meta:
        model = Customer
        fields = [
            'id',
            'first_name',
            'last_name',
            'full_name',
            'telephone_number',
            'physical_address',
            'email_address',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def validate_email_address(self, value):
        """
        Enforce email_address uniqueness on both create and update.
        Excludes the current instance on update to allow saving without
        changing the email address.
        """
        qs = Customer.objects.filter(email_address__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'A customer with this email address already exists.'
            )
        return value.lower()

    def validate_telephone_number(self, value):
        """Enforce the 15-character max length from the Database Dictionary."""
        if len(value) > 15:
            raise serializers.ValidationError(
                'Telephone number must not exceed 15 characters.'
            )
        return value


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
    UC13 -- Write serializer for administrator-created technician records.

    Accepts all profile fields plus a username for login account creation.
    Password is set programmatically by the view (temp password = telephone_number).
    """

    username = serializers.CharField(
        write_only=True,
        help_text="Username for the technician's login account.",
    )

    class Meta:
        model  = Technician
        fields = [
            'first_name', 'last_name', 'gender', 'physical_address',
            'telephone_number', 'email_address', 'skill',
            'hourly_rate', 'username',
        ]

    def validate_username(self, value):
        """Reject if the username is already taken by any User account."""
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError(
                "A user with this username already exists."
            )
        return value

    def validate_email_address(self, value):
        """Reject if the email is already registered to an existing technician."""
        if Technician.objects.filter(email_address=value).exists():
            raise serializers.ValidationError(
                "A technician with this email address already exists."
            )
        return value


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class InventorySerializer(serializers.ModelSerializer):
    """
    UC18, UC19 -- Full read/write serializer for Inventory records.

    status is read-only -- managed automatically by the model save() method
    based on the current quantity value.
    """

    class Meta:
        model            = Inventory
        fields           = '__all__'
        read_only_fields = ['status', 'created_at', 'updated_at']

    def validate_name(self, value):
        """
        UC18, step 7 -- Reject if an inventory item with this name already exists.
        Case-insensitive check to prevent near-duplicate entries.
        Excludes the current instance during updates.
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
    """
    UC21, UC22 -- Serializer for JobInventory with computed line total
    and inventory display fields.
    """

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

    customer_detail and technician_detail provide nested read-only objects
    for display purposes. The writable FK fields (customer, technician) remain
    as integer IDs so JobCreateSerializer is unaffected.

    job_inventory includes all assigned parts for full detail views (UC17, UC21, UC22).
    start_time and end_time are exposed read-only so the frontend can display
    job duration information (UC23, UC24).
    """

    customer_detail   = CustomerSerializer(source='customer', read_only=True)
    technician_detail = TechnicianSerializer(source='technician', read_only=True)
    job_inventory     = JobInventorySerializer(many=True, read_only=True)
    is_completed      = serializers.ReadOnlyField()
    requires_feedback = serializers.ReadOnlyField()

    class Meta:
        model  = Job
        fields = '__all__'


class JobCreateSerializer(serializers.ModelSerializer):
    """
    UC2, UC4 -- Write serializer for creating a new Job record.

    Status is always forced to Pending by the view and is not accepted
    from input. start_time and end_time are excluded -- they are set
    only by the view layer during UC23 and UC24 transitions.
    """

    class Meta:
        model = Job
        fields = ['customer', 'job_title', 'subject', 'client_message', 'source', 'client_request']

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
    UC16, UC23, UC24 -- Validates a job status transition request.

    Enforces the documented status transition rules:
        Pending     -> Allocated    (UC15 -- handled by BookingViewSet.allocate, not here)
        Allocated   -> In Progress  (UC23 -- technician only)
        In Progress -> Completed    (UC24 -- technician only)
        Allocated/In Progress -> Suspended  (admin_feedback required)
        Allocated/In Progress -> Cancelled  (admin_feedback required)

    The role field is injected by the view from the authenticated user's
    UserProfile and is not supplied by the client directly.

    Transition guard rules by role:
        Technician:
            - May only move to In Progress from Allocated (UC23).
            - May only move to Completed from In Progress (UC24).
            - May move to Suspended or Cancelled with technician_feedback.
        Administrator:
            - May move to Suspended or Cancelled from Allocated or In Progress
              with admin_feedback.
            - May not directly trigger In Progress or Completed transitions
              (those are technician-only actions per the use cases).
    """

    new_status          = serializers.ChoiceField(choices=Job.Status.choices)
    admin_feedback      = serializers.CharField(required=False, allow_blank=True)
    technician_feedback = serializers.CharField(required=False, allow_blank=True)

    # Injected by the view -- not accepted from client input.
    role           = serializers.ChoiceField(choices=UserProfile.Role.choices, write_only=True)
    current_status = serializers.CharField(write_only=True)

    def validate(self, data):
        """
        Enforce status transition guards and feedback requirements.

        Raises ValidationError with a descriptive message if:
            - A technician attempts In Progress from a non-Allocated job (UC23).
            - A technician attempts Completed from a non-In Progress job (UC24).
            - Suspended or Cancelled is requested without the required feedback field.
        """
        new_status     = data.get('new_status')
        current_status = data.get('current_status')
        role           = data.get('role')

        # -- UC23: Technician can only start (In Progress) an Allocated job.
        if (
            new_status == Job.Status.IN_PROGRESS
            and role == UserProfile.Role.TECHNICIAN
            and current_status != Job.Status.ALLOCATED
        ):
            raise serializers.ValidationError(
                {
                    'new_status': (
                        f"The job must be Allocated before it can be set to In Progress. "
                        f"Current status is '{current_status}'."
                    )
                }
            )

        # -- UC24: Technician can only complete an In Progress job.
        if (
            new_status == Job.Status.COMPLETED
            and role == UserProfile.Role.TECHNICIAN
            and current_status != Job.Status.IN_PROGRESS
        ):
            raise serializers.ValidationError(
                {
                    'new_status': (
                        f"The job must be In Progress before it can be Completed. "
                        f"Current status is '{current_status}'."
                    )
                }
            )

        # -- UC16: Feedback is required when suspending or cancelling.
        if new_status in (Job.Status.SUSPENDED, Job.Status.CANCELLED):
            if role == UserProfile.Role.ADMINISTRATOR:
                if not data.get('admin_feedback', '').strip():
                    raise serializers.ValidationError(
                        {
                            'admin_feedback': (
                                'Admin feedback is required when suspending or cancelling a job.'
                            )
                        }
                    )
            elif role == UserProfile.Role.TECHNICIAN:
                if not data.get('technician_feedback', '').strip():
                    raise serializers.ValidationError(
                        {
                            'technician_feedback': (
                                'Technician feedback is required when suspending or cancelling a job.'
                            )
                        }
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

    The token identifies the booking. The customer supplies date, time, and
    physical address. All four fields are required.
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


class TechnicianScheduleEntrySerializer(serializers.Serializer):
    """
    UC26, UC27 -- Read serializer for a single entry in a technician schedule.

    Each entry represents one job on the technician's schedule and contains
    the fields specified in the use cases:
        Booking ID, Job ID, Customer Full Name, Customer Physical Address,
        Date, Time, Distance.

    is_in_progress distinguishes the current active job (shown at the top
    of the schedule) from upcoming Allocated jobs.
    """

    booking_id           = serializers.IntegerField()
    job_id               = serializers.IntegerField()
    customer_full_name   = serializers.CharField()
    customer_address     = serializers.CharField()
    date                 = serializers.DateField()
    time                 = serializers.TimeField()
    distance             = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True
    )
    job_status           = serializers.CharField()
    is_in_progress       = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class InvoiceSerializer(serializers.ModelSerializer):
    """
    UC24, UC25 -- Full read serializer for Invoice records.

    All derived cost fields are read-only. They are calculated by
    Invoice.calculate_totals() and must not be set directly via the API.

    customer and technician details are nested for display purposes.
    """

    # Nested read-only customer and technician details for the invoice display (UC25, step 4).
    customer_full_name  = serializers.SerializerMethodField()
    customer_address    = serializers.SerializerMethodField()
    customer_phone      = serializers.SerializerMethodField()
    technician_full_name = serializers.SerializerMethodField()
    job_subject         = serializers.ReadOnlyField(source='job.subject')

    class Meta:
        model            = Invoice
        fields           = '__all__'
        read_only_fields = [
            'labour_cost', 'distance_cost', 'parts_cost',
            'subtotal', 'service_charge', 'total_cost',
            'date_generated', 'updated_at',
            'customer_full_name', 'customer_address', 'customer_phone',
            'technician_full_name', 'job_subject',
        ]

    def get_customer_full_name(self, obj) -> str:
        """Return the customer's full name for invoice display (UC25, step 4)."""
        customer = obj.job.customer
        return f"{customer.first_name} {customer.last_name}"

    def get_customer_address(self, obj) -> str:
        """Return the customer's physical address (UC27, step 4)."""
        return obj.job.customer.physical_address

    def get_customer_phone(self, obj) -> str:
        """Return the customer's telephone number (UC27, step 4)."""
        return obj.job.customer.telephone_number

    def get_technician_full_name(self, obj) -> str:
        """Return the technician's full name for invoice display (UC25, step 10)."""
        if obj.technician:
            return f"{obj.technician.first_name} {obj.technician.last_name}"
        return ''


class InvoiceRecalculateSerializer(serializers.Serializer):
    """
    UC25, steps 5-7 -- Accepts editable invoice fields from the administrator
    and returns recalculated cost values without persisting changes.

    The view calls Invoice.calculate_totals() after applying these values and
    returns the recalculated Invoice data. The administrator can review the
    new totals before choosing to approve.

    All three fields are optional individually -- the administrator may update
    one or all of them. Any field not supplied retains the current value on
    the invoice instance.
    """

    hours_taken               = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        min_value=0,
        help_text="Updated hours taken. Must be >= 0.",
    )
    distance_rate             = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        min_value=0,
        help_text="Updated distance rate in dollars per kilometre.",
    )
    service_charge_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False,
        min_value=0,
        help_text="Updated service charge percentage (e.g. 10 for 10%).",
    )
    notes                     = serializers.CharField(required=False, allow_blank=True)


class InvoiceApproveSerializer(serializers.Serializer):
    """
    UC25, steps 8-12 -- Accepts the final editable field values from the
    administrator and validates them before the invoice is approved,
    the PDF is generated, and the email is sent.

    hours_taken is required and must be greater than zero (UC25, step 9a).
    The other editable fields are optional -- if not supplied, the current
    values on the invoice instance are used.
    """

    hours_taken               = serializers.DecimalField(
        max_digits=10, decimal_places=2,
        min_value=0,
        help_text="Hours taken. Must be greater than zero before approving.",
    )
    distance_rate             = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        min_value=0,
    )
    service_charge_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False,
        min_value=0,
    )
    notes                     = serializers.CharField(required=False, allow_blank=True)

    def validate_hours_taken(self, value):
        """
        UC25, step 9a -- Reject approval if hours_taken is zero.
        The invoice cannot be approved until labour time has been confirmed.
        """
        if value <= 0:
            raise serializers.ValidationError(
                "Hours Taken must be greater than zero before the invoice can be approved."
            )
        return value


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

class NotificationSerializer(serializers.ModelSerializer):
    """
    UC24, step 10 -- Read serializer for Notification records.

    Exposes all fields needed for the administrator dashboard to display
    pending invoice alerts. is_read and read_at reflect the current
    acknowledgement state.
    """

    class Meta:
        model  = Notification
        fields = [
            'id', 'recipient', 'notification_type',
            'job', 'invoice', 'message',
            'is_read', 'read_at', 'created_at',
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Client Request
# ---------------------------------------------------------------------------

class ClientRequestSerializer(serializers.ModelSerializer):
    """Read serializer for ClientRequest records."""

    class Meta:
        model = ClientRequest
        fields = '__all__'
        read_only_fields = ['status', 'acknowledged_at', 'date_received', 'updated_at']


class ClientRequestProcessSerializer(serializers.Serializer):
    """
    UC1 -- Validates that the required fields exist on a ClientRequest
    before it is converted into a Customer and Job record.

    All data is sourced from the ClientRequest instance passed in context,
    not from submitted input. The POST body for this action is intentionally empty.
    """

    def validate(self, data):
        """Check required fields on the ClientRequest instance in context."""
        request_obj = self.context.get('client_request')
        errors = {}

        if not getattr(request_obj, 'first_name', '').strip():
            errors['first_name'] = 'First name is required.'
        if not getattr(request_obj, 'email_address', '').strip():
            errors['email_address'] = 'Email address is required.'
        if not getattr(request_obj, 'subject', '').strip():
            errors['subject'] = 'Subject is required.'

        if errors:
            raise serializers.ValidationError(errors)

        return data


class WebhookInboundSerializer(serializers.Serializer):
    """
    UC1 -- Validates the inbound API payload from the external website.

    Required fields per UC1, step 3:
        first_name, last_name, email, subject, message.
    """

    first_name = serializers.CharField(max_length=100)
    last_name  = serializers.CharField(max_length=100)
    email      = serializers.EmailField()
    subject    = serializers.CharField(max_length=255)
    message    = serializers.CharField()
    phone      = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate(self, data):
        """Ensure no required field is blank after stripping whitespace."""
        required = ['first_name', 'last_name', 'email', 'subject', 'message']
        errors = {}
        for field in required:
            if not data.get(field, '').strip():
                errors[field] = f"{field.replace('_', ' ').title()} is required."
        if errors:
            raise serializers.ValidationError(errors)
        return data


# ---------------------------------------------------------------------------
# AI Response Suggestion (BR4, BR5 -- descoped, retained for audit)
# ---------------------------------------------------------------------------

class AIResponseSuggestionSerializer(serializers.ModelSerializer):
    """Read serializer for AIResponseSuggestion records."""

    class Meta:
        model            = AIResponseSuggestion
        fields           = '__all__'
        read_only_fields = [
            'approval_status', 'reviewed_at', 'sent_at',
            'created_at', 'updated_at',
        ]


class ApproveResponseSerializer(serializers.Serializer):
    """
    BR5 -- Validates an administrator's approval or rejection of an
    AI-generated response suggestion.

    NOTE: This feature was formally descoped. This serializer is retained
    for completeness only.
    """

    action       = serializers.ChoiceField(choices=['approve', 'reject'])
    final_response = serializers.CharField(
        required=False, allow_blank=True,
        help_text="The final response text to send if action is 'approve'.",
    )

    def validate(self, data):
        """Require final_response when approving."""
        if data.get('action') == 'approve' and not data.get('final_response', '').strip():
            raise serializers.ValidationError(
                {'final_response': 'A final response is required when approving a suggestion.'}
            )
        return data