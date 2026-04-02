"""
tradiePrototype/models.py

All database models for TradieRM.

Model inventory:
    Customer            -- UC1, UC2
    Technician          -- UC6
    Job                 -- UC1, UC2, UC9
    Inventory           -- UC5
    JobInventory        -- Links Job and Inventory (parts used on a job)
    Booking             -- UC3, UC4, UC7
    ScheduleBlock       -- UC3, UC7 (travel + job time blocks)
    Invoice             -- UC9 (auto-generated on job completion)
    ClientRequest       -- UC8 (inbound from external website)
    AIResponseSuggestion -- BR4, BR5
    UserProfile         -- Role-based access control
"""

from django.db import models
from django.contrib.auth.models import User


class Customer(models.Model):
    """
    UC1, UC2 -- Stores customer contact details.
    Created either from a ClientRequest (UC1) or manually by the administrator (UC2).
    """
    first_name = models.CharField(max_length=100)
    last_name  = models.CharField(max_length=100)
    email      = models.EmailField(unique=True)
    phone      = models.CharField(max_length=20, blank=True)
    address    = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Technician(models.Model):
    """
    UC6 -- Stores technician profile and credentials.
    Created exclusively by administrators. On creation, a Django User account
    is provisioned with a temporary password equal to the technician's phone number.
    home_address is used as the origin point for distance calculation (UC7).
    hourly_rate is copied to the Invoice at generation time to preserve historical data.
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
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Job(models.Model):
    """
    UC1, UC2, UC9 -- Central work order record.

    Status lifecycle:
        Pending -> Allocated -> In Progress -> Completed
                             -> Suspended   (admin_feedback or technician_feedback required)
                             -> Cancelled   (admin_feedback or technician_feedback required)

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

    customer   = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='jobs')
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

    # Feedback fields -- mandatory when status is Suspended or Cancelled (UC9).
    admin_feedback      = models.TextField(blank=True)
    technician_feedback = models.TextField(blank=True)

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
        """Returns True if the current status requires a feedback field to be filled."""
        return self.status in (self.Status.SUSPENDED, self.Status.CANCELLED)


class Inventory(models.Model):
    """
    UC5 -- Standalone inventory record for spare parts and materials.

    name is unique to prevent duplicate entries (UC5, step 7).
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
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['name']
        verbose_name_plural = 'Inventory'

    def __str__(self):
        return f"{self.name} (qty: {self.quantity})"

    def save(self, *args, **kwargs):
        """
        Automatically set status based on quantity before saving.
        Ensures the status field always reflects the actual stock level.
        """
        self.status = self.Status.IN_STOCK if self.quantity > 0 else self.Status.OUT_OF_STOCK
        super().save(*args, **kwargs)


class JobInventory(models.Model):
    """
    Links a Job to an Inventory item and records the quantity used.
    Created when an administrator assigns parts from inventory to a job.
    quantity_used is used when calculating parts_cost on the invoice.
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
        """Total cost for this line: quantity used multiplied by unit cost."""
        return self.quantity_used * self.inventory.cost


class Booking(models.Model):
    """
    UC3, UC4, UC7 -- Scheduling record linking a job to a date, time, and location.

    Created in Pending status (UC3 or UC4).
    Transitions to Confirmed when a technician is allocated (UC7).

    distance stores road distance in kilometres between the technician's
    home address and the customer's physical address. Calculated via
    OpenRouteService during allocation (UC7) and used in invoice generation.

    booking_token and token_expires_at support the unauthenticated
    customer-facing booking form link (UC4).
    """

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        REJECTED  = 'rejected',  'Rejected'

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

    # Road distance in km, populated during technician allocation (UC7).
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


class ScheduleBlock(models.Model):
    """
    UC3, UC7 -- A single time block on a technician's calendar.

    Each allocated job produces a JOB block (time on site).
    A TRAVEL block may be added to represent transit time if applicable.
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


class Invoice(models.Model):
    """
    UC9 -- Financial document generated automatically when a job is completed.

    All rate fields are copied from the technician and system settings at
    generation time to preserve a historical snapshot independent of future
    rate changes.

    Calculation:
        labour_cost   = hours_taken x hourly_rate
        distance_cost = distance x distance_rate
        parts_cost    = sum of all JobInventory line totals
        subtotal      = labour_cost + distance_cost + parts_cost
        markup        = subtotal x 0.20  (20%)
        total_cost    = subtotal + markup
    """

    class Status(models.TextChoices):
        DRAFT   = 'draft',   'Draft'
        SENT    = 'sent',    'Sent'
        PAID    = 'paid',    'Paid'
        OVERDUE = 'overdue', 'Overdue'

    job        = models.OneToOneField(Job, on_delete=models.PROTECT, related_name='invoice')
    technician = models.ForeignKey(
        Technician, on_delete=models.PROTECT,
        null=True, blank=True, related_name='invoices'
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    hours_taken  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hourly_rate  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    labour_cost  = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    distance      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    distance_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    distance_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    parts_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    subtotal   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    markup     = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    notes          = models.TextField(blank=True)
    date_generated = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_generated']

    def __str__(self):
        return f"Invoice #{self.pk} for Job #{self.job_id} ({self.status})"

    def calculate_totals(self):
        """
        Recalculate all derived cost fields.
        Must be called before saving whenever any input field changes.
        """
        self.labour_cost   = self.hours_taken * self.hourly_rate
        self.distance_cost = self.distance * self.distance_rate
        self.parts_cost    = sum(
            ji.line_total
            for ji in self.job.job_inventory.select_related('inventory').all()
        )
        self.subtotal   = self.labour_cost + self.distance_cost + self.parts_cost
        self.markup     = self.subtotal / 5  # 20% markup
        self.total_cost = self.subtotal + self.markup
        return self


class ClientRequest(models.Model):
    """
    UC8 -- Inbound job request received via the public webhook endpoint.

    Status lifecycle:
        Unprocessed -> Processed (after UC1 creates the customer and job records)

    raw_payload stores the original JSON for audit purposes.
    acknowledged_at is set when the confirmation email is dispatched (BR3).
    """

    class Status(models.TextChoices):
        UNPROCESSED = 'unprocessed', 'Unprocessed'
        PROCESSED   = 'processed',   'Processed'

    contact_name  = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    subject       = models.CharField(max_length=255, blank=True)
    message       = models.TextField()
    source_ip     = models.GenericIPAddressField(null=True, blank=True)
    raw_payload   = models.JSONField(default=dict)
    status        = models.CharField(
        max_length=15, choices=Status.choices, default=Status.UNPROCESSED
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"ClientRequest #{self.pk} from {self.contact_email} ({self.status})"


class AIResponseSuggestion(models.Model):
    """
    BR4, BR5 -- AI-generated response suggestion for a ClientRequest.

    Always created in PENDING status.
    Must be explicitly approved before it can be sent (BR5).
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
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

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


class UserProfile(models.Model):
    """
    Extends Django's built-in User model with a role field.

    Administrators are created via createsuperuser only.
    Technicians are created by administrators via UC6.
    Role determines which API endpoints and UI views are accessible.
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