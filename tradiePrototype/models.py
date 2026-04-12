"""
tradiePrototype/models.py

All database models for TradieRM.

Model inventory:
    Customer             -- UC1, UC2
    Technician           -- UC11, UC12, UC13, UC14
    Job                  -- UC2, UC4, UC15, UC16, UC17, UC23, UC24
    Inventory            -- UC18, UC19, UC20
    JobInventory         -- UC21, UC22 (parts assigned to a job)
    Booking              -- UC3, UC4, UC8, UC9, UC10
    ScheduleBlock        -- UC26, UC27 (technician timetable blocks)
    Invoice              -- UC24, UC25 (auto-created on completion, reviewed by admin)
    Notification         -- UC24 (in-system admin alert for completed jobs)
    ClientRequest        -- UC1 (inbound from external website)
    AIResponseSuggestion -- BR4, BR5 (descoped; retained for audit)
    UserProfile          -- Role-based access control
"""

from django.db import models
from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

class Customer(models.Model):
    """
    UC1, UC2 -- Stores customer contact details.

    Created either from a ClientRequest (UC1) or manually by the administrator (UC2).
    Soft-delete is implemented via is_active (UC6). Setting is_active to False marks
    the record as Inactive and excludes it from standard list queries while
    retaining it as an audit log.
    """

    first_name = models.CharField(max_length=100)
    last_name  = models.CharField(max_length=100)
    email      = models.EmailField(unique=True)
    phone      = models.CharField(max_length=20, blank=True)
    address    = models.TextField(blank=True)
    is_active  = models.BooleanField(default=True)  # UC6 -- soft delete flag

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


# ---------------------------------------------------------------------------
# Technician
# ---------------------------------------------------------------------------

class Technician(models.Model):
    """
    UC11 -- Stores technician profile and credentials.

    Created exclusively by administrators via UC11. On creation, a Django User
    account is provisioned with a temporary password equal to the technician's
    phone number and a welcome email is dispatched.

    home_address is the origin point for road distance calculation (UC15).
    hourly_rate is copied to the Invoice at generation time (UC24) to preserve
    a historical snapshot independent of future rate changes.
    """

    class Gender(models.TextChoices):
        MALE   = 'male',   'Male'
        FEMALE = 'female', 'Female'
        OTHER  = 'other',  'Other'

    first_name   = models.CharField(max_length=100)
    last_name    = models.CharField(max_length=100)
    email        = models.EmailField(unique=True)
    phone        = models.CharField(max_length=20, blank=True)
    gender       = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    home_address = models.TextField(blank=True)
    skill        = models.CharField(max_length=255, blank=True)
    hourly_rate  = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    is_active    = models.BooleanField(default=True)  # UC13 -- soft delete flag

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

class Job(models.Model):
    """
    UC2, UC4, UC15, UC16, UC17, UC23, UC24 -- Central work order record.

    Status lifecycle (UC16, UC23, UC24):
        Pending -> Allocated  (UC15 -- technician allocated via booking)
        Allocated -> In Progress  (UC23 -- technician starts the job)
        In Progress -> Completed  (UC24 -- technician completes the job)
        Allocated/In Progress -> Suspended  (admin_feedback required)
        Allocated/In Progress -> Cancelled  (admin_feedback required)

    start_time is recorded when the technician transitions the job to In Progress (UC23).
    end_time is recorded when the technician transitions the job to Completed (UC24).
    hours_taken on the Invoice is derived from end_time - start_time (UC24, step 9).

    source records how the job entered the system.
    client_request links back to the originating ClientRequest if applicable (UC1).
    """

    class Status(models.TextChoices):
        PENDING     = 'pending',     'Pending'
        ALLOCATED   = 'allocated',   'Allocated'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED   = 'completed',   'Completed'
        SUSPENDED   = 'suspended',   'Suspended'
        CANCELLED   = 'cancelled',   'Cancelled'

    class Source(models.TextChoices):
        MANUAL  = 'manual',  'Manual (Admin)'
        WEBHOOK = 'webhook', 'Webhook / API'

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='jobs'
    )
    technician = models.ForeignKey(
        Technician, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='jobs'
    )
    client_request = models.ForeignKey(
        'ClientRequest', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='jobs'
    )

    subject        = models.CharField(max_length=255)
    client_message = models.TextField()
    status         = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDING
    )
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL
    )

    # Feedback fields -- mandatory when status is Suspended or Cancelled (UC16).
    admin_feedback      = models.TextField(blank=True)
    technician_feedback = models.TextField(blank=True)

    # Job execution timestamps recorded by the technician (UC23, UC24).
    # start_time: set when the technician transitions the job to In Progress.
    # end_time:   set when the technician transitions the job to Completed.
    # Both are used to derive hours_taken when creating the draft Invoice.
    start_time = models.DateTimeField(null=True, blank=True)
    end_time   = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Job #{self.pk} -- {self.subject} ({self.status})"

    @property
    def is_completed(self):
        """Returns True if the job status is Completed."""
        return self.status == self.Status.COMPLETED

    @property
    def requires_feedback(self):
        """Returns True if the current status mandates a feedback field."""
        return self.status in (self.Status.SUSPENDED, self.Status.CANCELLED)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class Inventory(models.Model):
    """
    UC18, UC19, UC20 -- Standalone inventory record for spare parts and materials.

    name is unique to prevent duplicate entries (UC18, step 7).
    Status is automatically managed by the save() method based on quantity.
    """

    class Status(models.TextChoices):
        IN_STOCK     = 'in_stock',     'In Stock'
        OUT_OF_STOCK = 'out_of_stock', 'Out of Stock'

    name        = models.CharField(max_length=255, unique=True)
    description = models.CharField(max_length=255, blank=True)
    quantity    = models.IntegerField(default=0)
    cost        = models.DecimalField(max_digits=10, decimal_places=2)
    status      = models.CharField(
        max_length=15, choices=Status.choices, default=Status.IN_STOCK
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['name']
        verbose_name_plural = 'Inventory'

    def __str__(self):
        return f"{self.name} (qty: {self.quantity})"

    def save(self, *args, **kwargs):
        """
        Automatically derive status from quantity before every save.
        Ensures the status field always reflects the actual stock level
        without requiring the caller to set it explicitly.
        """
        self.status = (
            self.Status.IN_STOCK if self.quantity > 0 else self.Status.OUT_OF_STOCK
        )
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# JobInventory
# ---------------------------------------------------------------------------

class JobInventory(models.Model):
    """
    UC21, UC22 -- Links a Job to an Inventory item and records quantity used.

    Admin-triggered (UC21): permitted when job status is Allocated, In Progress,
    or Completed.
    Technician-triggered (UC22): permitted only when job status is Allocated or
    In Progress.

    quantity_used drives the parts_cost calculation on the Invoice (UC24, step 9).
    unique_together prevents the same inventory item being added twice to one job.
    """

    job           = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='job_inventory')
    inventory     = models.ForeignKey(Inventory, on_delete=models.PROTECT, related_name='job_inventory')
    quantity_used = models.IntegerField()

    class Meta:
        verbose_name_plural = 'Job Inventory'
        unique_together     = ('job', 'inventory')

    def __str__(self):
        return f"Job #{self.job_id} -- {self.inventory.name} x{self.quantity_used}"

    @property
    def line_total(self):
        """Cost for this line: quantity_used multiplied by the inventory unit cost."""
        return self.quantity_used * self.inventory.cost


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

class Booking(models.Model):
    """
    UC3, UC4, UC8, UC9, UC10, UC15 -- Scheduling record linking a job to a
    date, time, and physical location.

    Created in Pending status (UC3 or UC4).
    Transitions to Confirmed when a technician is allocated (UC15).

    distance stores road distance in kilometres between the technician's
    home address and the customer's physical address. Calculated via
    OpenRouteService during allocation (UC15) and used in invoice generation
    (UC24, step 9).

    booking_token and token_expires_at support the unauthenticated
    customer-facing booking form link (UC4).
    """

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        REJECTED  = 'rejected',  'Rejected'
        INACTIVE  = 'inactive',  'Inactive'  # UC9 -- soft delete via administrator

    job      = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='bookings')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='bookings')
    technician = models.ForeignKey(
        Technician, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bookings'
    )

    physical_address = models.CharField(max_length=255)
    date             = models.DateField()
    time             = models.TimeField()
    status           = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )

    # Road distance in km, populated during technician allocation (UC15).
    distance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Signed token for the unauthenticated customer booking form link (UC4).
    booking_token    = models.CharField(max_length=255, blank=True, db_index=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'time']

    def __str__(self):
        return (
            f"Booking #{self.pk} -- Job #{self.job_id} "
            f"on {self.date} at {self.time} ({self.status})"
        )


# ---------------------------------------------------------------------------
# ScheduleBlock
# ---------------------------------------------------------------------------

class ScheduleBlock(models.Model):
    """
    UC26, UC27 -- A single time block on a technician's calendar.

    Each allocated job produces a JOB block representing time on site.
    A TRAVEL block may be added to represent transit time if applicable.
    Used exclusively for schedule display; not used for Invoice calculation.
    """

    class BlockType(models.TextChoices):
        JOB    = 'job',    'Job'
        TRAVEL = 'travel', 'Travel'

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='schedule_blocks'
    )
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE,
        null=True, blank=True, related_name='schedule_blocks'
    )
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE,
        null=True, blank=True, related_name='schedule_blocks'
    )
    block_type = models.CharField(max_length=10, choices=BlockType.choices)
    start_time = models.DateTimeField()
    end_time   = models.DateTimeField()
    notes      = models.TextField(blank=True)

    class Meta:
        ordering = ['technician', 'start_time']

    def __str__(self):
        return (
            f"{self.technician} | {self.block_type} | "
            f"{self.start_time:%Y-%m-%d %H:%M} -> {self.end_time:%H:%M}"
        )


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class Invoice(models.Model):
    """
    UC24, UC25 -- Financial document created automatically when a job is completed.

    Lifecycle:
        Draft  -- created automatically by the system when the technician completes
                  the job (UC24). All cost fields are pre-populated from available data.
        Sent   -- set by the administrator after reviewing, adjusting, and approving
                  the invoice (UC25). A PDF is generated and emailed to the customer.

    Cost calculation (UC25, step 7):
        labour_cost   = hours_taken x hourly_rate
        distance_cost = distance x distance_rate
        parts_cost    = sum of all JobInventory line totals for the job
        subtotal      = labour_cost + distance_cost + parts_cost
        service_charge = subtotal x (service_charge_percentage / 100)
        total_cost    = subtotal + service_charge

    All rate fields are copied from the technician profile and system settings
    at invoice creation time to preserve a historical snapshot independent of
    future rate changes (UC24, step 9).

    hours_taken and distance_rate are editable by the administrator before approval
    (UC25, step 5). service_charge_percentage is also editable and defaults to the
    system-configured value from settings.INVOICE_SERVICE_CHARGE_PERCENTAGE.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT  = 'sent',  'Sent'

    job = models.OneToOneField(
        Job, on_delete=models.PROTECT, related_name='invoice'
    )
    technician = models.ForeignKey(
        Technician, on_delete=models.PROTECT,
        null=True, blank=True, related_name='invoices'
    )

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )

    # Labour fields -- hourly_rate copied from Technician.hourly_rate at creation time.
    hours_taken = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    labour_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Distance fields -- distance copied from Booking.distance at creation time.
    # distance_rate defaults to settings.INVOICE_DISTANCE_RATE and is editable (UC25).
    distance      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    distance_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    distance_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Parts cost -- sum of all JobInventory line totals at invoice creation time.
    parts_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Subtotal and service charge (UC25, step 7).
    # service_charge_percentage defaults to settings.INVOICE_SERVICE_CHARGE_PERCENTAGE.
    # Stored on the invoice so it is editable per-invoice and historically preserved.
    subtotal                  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_charge_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    service_charge            = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_cost                = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    notes          = models.TextField(blank=True)
    date_generated = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_generated']

    def __str__(self):
        return f"Invoice #{self.pk} for Job #{self.job_id} ({self.status})"

    def calculate_totals(self):
        """
        Recalculate all derived cost fields from current input values.

        This method does NOT save the instance. The caller is responsible
        for calling save() after this method returns, allowing the caller
        to validate or inspect values before persisting.

        Formula (UC25, step 7):
            labour_cost    = hours_taken x hourly_rate
            distance_cost  = distance x distance_rate
            parts_cost     = sum of JobInventory line totals (read from DB)
            subtotal       = labour_cost + distance_cost + parts_cost
            service_charge = subtotal x (service_charge_percentage / 100)
            total_cost     = subtotal + service_charge
        """
        from decimal import Decimal

        self.labour_cost   = self.hours_taken * self.hourly_rate
        self.distance_cost = self.distance * self.distance_rate

        # Re-aggregate parts cost directly from JobInventory to ensure accuracy.
        self.parts_cost = sum(
            ji.line_total
            for ji in self.job.job_inventory.select_related('inventory').all()
        )

        self.subtotal = self.labour_cost + self.distance_cost + self.parts_cost

        # Divide by 100 to convert percentage integer/decimal to a multiplier.
        rate = Decimal(str(self.service_charge_percentage)) / Decimal('100')
        self.service_charge = self.subtotal * rate
        self.total_cost     = self.subtotal + self.service_charge

        return self


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

class Notification(models.Model):
    """
    UC24, step 10 -- In-system notification record for the administrator dashboard.

    Created automatically when a technician marks a job as Completed (UC24).
    The administrator is alerted that a completed job requires invoice finalisation.

    is_read is set to True when the administrator acknowledges the notification
    via the API. Unread notifications are surfaced on the dashboard.

    recipient is the Django User targeted by the notification. For UC24 this is
    always an administrator account, but the model is generic enough to support
    other notification types in the future.
    """

    class NotificationType(models.TextChoices):
        JOB_COMPLETED = 'job_completed', 'Job Completed -- Invoice Required'

    recipient         = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notifications'
    )
    notification_type = models.CharField(
        max_length=30, choices=NotificationType.choices
    )

    # Contextual foreign keys -- nullable to allow future notification types
    # that may not involve a job or invoice.
    job     = models.ForeignKey(
        Job, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='notifications'
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='notifications'
    )

    message  = models.TextField()
    is_read  = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    read_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        read_label = 'Read' if self.is_read else 'Unread'
        return (
            f"Notification #{self.pk} -> {self.recipient.username} "
            f"| {self.notification_type} | {read_label}"
        )

    def mark_as_read(self):
        """
        Mark this notification as read and record the timestamp.
        Saves the instance immediately.
        """
        from django.utils import timezone
        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=['is_read', 'read_at'])


# ---------------------------------------------------------------------------
# ClientRequest
# ---------------------------------------------------------------------------

class ClientRequest(models.Model):
    """
    UC1 -- Inbound job request received via the public webhook endpoint.

    Records are created exclusively by the webhook_intake view (UC1).
    Administrators process Unprocessed records to create Customer + Job records.
    The raw payload is stored for audit purposes regardless of processing outcome.
    """

    class Status(models.TextChoices):
        UNPROCESSED = 'unprocessed', 'Unprocessed'
        PROCESSED   = 'processed',   'Processed'

    source_ip     = models.GenericIPAddressField(null=True, blank=True)
    raw_payload   = models.JSONField(default=dict)
    subject       = models.CharField(max_length=255, blank=True)
    message       = models.TextField()
    contact_name  = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    status        = models.CharField(
        max_length=15, choices=Status.choices, default=Status.UNPROCESSED
    )

    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"ClientRequest #{self.pk} from {self.contact_name} "
            f"<{self.contact_email}> ({self.status})"
        )


# ---------------------------------------------------------------------------
# AIResponseSuggestion
# ---------------------------------------------------------------------------

class AIResponseSuggestion(models.Model):
    """
    BR4, BR5 -- AI-generated response suggestion for a client request.

    NOTE: This feature was formally descoped from the project. This model is
    retained in the schema for audit purposes only. No new records are created
    via the current application flow.

    Was always created in PENDING status and required explicit human approval
    before the suggested response could be sent to the client (BR5).
    """

    class ApprovalStatus(models.TextChoices):
        PENDING  = 'pending',  'Pending Review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        SENT     = 'sent',     'Sent to Client'

    class ReviewerRole(models.TextChoices):
        ADMINISTRATOR = 'administrator', 'Administrator'
        TECHNICIAN    = 'technician',    'Technician'

    client_request      = models.ForeignKey(
        ClientRequest, on_delete=models.CASCADE, related_name='ai_suggestions'
    )
    suggested_response  = models.TextField()
    final_response      = models.TextField(blank=True)
    approval_status     = models.CharField(
        max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING
    )
    reviewed_by_role    = models.CharField(
        max_length=20, choices=ReviewerRole.choices, blank=True
    )
    reviewed_by_user_id = models.IntegerField(null=True, blank=True)
    reviewed_at         = models.DateTimeField(null=True, blank=True)
    sent_at             = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"AISuggestion #{self.pk} for Request "
            f"#{self.client_request_id} ({self.approval_status})"
        )

    @property
    def is_sendable(self):
        """Returns True only if the suggestion has been explicitly approved (BR5)."""
        return self.approval_status == self.ApprovalStatus.APPROVED


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

class UserProfile(models.Model):
    """
    Extends Django's built-in User model with a role field for RBAC.

    Administrators are created via createsuperuser only.
    Technicians are created by administrators via UC11.
    Role determines which API endpoints and UI views the user can access.
    """

    class Role(models.TextChoices):
        ADMINISTRATOR = 'administrator', 'Administrator'
        TECHNICIAN    = 'technician',    'Technician'
        CUSTOMER      = 'customer',      'Customer'

    user    = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role    = models.CharField(max_length=20, choices=Role.choices)
    phone   = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    @property
    def is_admin(self):
        """Returns True if the user holds the Administrator role."""
        return self.role == self.Role.ADMINISTRATOR

    @property
    def is_technician(self):
        """Returns True if the user holds the Technician role."""
        return self.role == self.Role.TECHNICIAN

    @property
    def is_customer(self):
        """Returns True if the user holds the Customer role."""
        return self.role == self.Role.CUSTOMER