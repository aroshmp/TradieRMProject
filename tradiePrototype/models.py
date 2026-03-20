from django.db import models

# Create your models here.
"""
core/models.py

All models for the CRM prototype in one place:
  - Customer        (BR1 – US1.1)
  - Technician      (BR1 – US1.2)
  - Job             (BR1 – US1.3, BR2 – US2.2, BR7 – US7.1/7.2)
  - JobPart         (BR1 – US1.4)
  - ScheduleBlock   (BR6 – US6.1/6.2/6.3)
  - Invoice         (BR10 – US10.1/10.2)
  - ClientRequest   (BR2 – US2.2, BR3 – US3.1)
  - AIResponseSuggestion (BR4 – US4.1/4.2, BR5 – US5.1/5.2)
"""

from django.db import models


# ── Customers ─────────────────────────────────────────────────────────────────

class Customer(models.Model):
    """US1.1 – Manage Customers"""
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


# ── Technicians ───────────────────────────────────────────────────────────────

class Technician(models.Model):
    """US1.2 – Manage Technicians"""
    first_name   = models.CharField(max_length=100)
    last_name    = models.CharField(max_length=100)
    email        = models.EmailField(unique=True)
    phone        = models.CharField(max_length=20, blank=True)
    home_address = models.TextField(blank=True)   # used for travel-time calculation (BR6)
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


# ── Jobs ──────────────────────────────────────────────────────────────────────

class Job(models.Model):
    """US1.3 – Manage Jobs | US2.2 – Receive via Webhook | US7.1/7.2 – Booking"""

    class Status(models.TextChoices):
        PENDING     = 'pending',     'Pending'
        BOOKED      = 'booked',      'Booked'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED   = 'completed',   'Completed'
        CANCELLED   = 'cancelled',   'Cancelled'

    class Source(models.TextChoices):
        MANUAL        = 'manual',        'Manual (Admin)'
        WEBHOOK       = 'webhook',       'Webhook / API'
        CLIENT_PORTAL = 'client_portal', 'Client Portal'

    customer   = models.ForeignKey(Customer,   on_delete=models.PROTECT,  related_name='jobs')
    technician = models.ForeignKey(Technician, on_delete=models.SET_NULL, related_name='jobs',
                                   null=True, blank=True)

    title       = models.CharField(max_length=255)
    description = models.TextField()
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source      = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)

    # Booking fields (BR7)
    scheduled_start = models.DateTimeField(null=True, blank=True)
    scheduled_end   = models.DateTimeField(null=True, blank=True)

    # Set by the scheduler service (BR6)
    travel_time_minutes = models.PositiveIntegerField(default=0)
    job_address         = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Job #{self.pk} – {self.title} ({self.status})"

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED


class JobPart(models.Model):
    """US1.4 – Manage Job Parts"""
    job         = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='parts')
    name        = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    quantity    = models.PositiveIntegerField(default=1)
    unit_cost   = models.DecimalField(max_digits=10, decimal_places=2)

    # @property
    # def total_cost(self):
    #     return self.quantity * self.unit_cost
    #
    # def __str__(self):
    #     return f"{self.name} x{self.quantity} @ ${self.unit_cost}"

    @property
    def total_cost(self):
        if self.quantity is None or self.unit_cost is None:
            return 0
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.name} x{self.quantity} @ ${self.unit_cost}"


# ── Schedule ──────────────────────────────────────────────────────────────────

class ScheduleBlock(models.Model):
    """
    US6.1/6.2/6.3 – Technician timetable with travel time.
    One record per travel leg or job window on a technician's calendar.
    """

    class BlockType(models.TextChoices):
        JOB    = 'job',    'Job'
        TRAVEL = 'travel', 'Travel'

    technician = models.ForeignKey(Technician, on_delete=models.CASCADE, related_name='schedule_blocks')
    job        = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='schedule_blocks',
                                   null=True, blank=True)
    block_type = models.CharField(max_length=10, choices=BlockType.choices)
    start_time = models.DateTimeField()
    end_time   = models.DateTimeField()
    notes      = models.TextField(blank=True)

    class Meta:
        ordering = ['technician', 'start_time']

    def __str__(self):
        return (
            f"{self.technician} | {self.block_type} | "
            f"{self.start_time:%Y-%m-%d %H:%M} → {self.end_time:%H:%M}"
        )


# ── Invoices ──────────────────────────────────────────────────────────────────

class Invoice(models.Model):
    """US10.1/10.2 – Generate invoice detailing labour and parts"""

    class Status(models.TextChoices):
        DRAFT   = 'draft',   'Draft'
        SENT    = 'sent',    'Sent'
        PAID    = 'paid',    'Paid'
        OVERDUE = 'overdue', 'Overdue'

    job    = models.OneToOneField(Job, on_delete=models.PROTECT, related_name='invoice')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    labour_hours = models.DecimalField(max_digits=6,  decimal_places=2, default=0)
    labour_rate  = models.DecimalField(max_digits=8,  decimal_places=2, default=0)
    parts_total  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    labour_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_rate     = models.DecimalField(max_digits=5,  decimal_places=4, default=0.1)  # 10%
    grand_total  = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    payment_instructions = models.TextField(blank=True)
    notes                = models.TextField(blank=True)
    issued_at            = models.DateTimeField(null=True, blank=True)
    due_date             = models.DateField(null=True, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Invoice #{self.pk} for Job #{self.job_id} ({self.status})"

    def calculate_totals(self):
        """Recalculate all totals from job parts and labour."""
        self.parts_total  = sum(p.total_cost for p in self.job.parts.all())
        self.labour_total = self.labour_hours * self.labour_rate
        subtotal          = self.parts_total + self.labour_total
        self.grand_total  = subtotal + (subtotal * self.tax_rate)
        return self


# ── Communications ────────────────────────────────────────────────────────────

class ClientRequest(models.Model):
    """
    US2.2 – Inbound job request received via webhook/API.
    US3.1 – Tracks whether the auto-confirmation has been sent.
    """

    class Status(models.TextChoices):
        RECEIVED     = 'received',     'Received'
        ACKNOWLEDGED = 'acknowledged', 'Acknowledged'
        PROCESSING   = 'processing',   'Processing'
        RESOLVED     = 'resolved',     'Resolved'

    customer      = models.ForeignKey(Customer, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='requests')
    source_ip     = models.GenericIPAddressField(null=True, blank=True)
    raw_payload   = models.JSONField(default=dict)
    subject       = models.CharField(max_length=255, blank=True)
    message       = models.TextField()
    contact_name  = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)

    # Timestamp when auto-confirmation was sent (BR3)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"ClientRequest #{self.pk} from {self.contact_email} ({self.status})"


class AIResponseSuggestion(models.Model):
    """
    US4.1/4.2 – AI-generated response suggestion for admins and technicians.
    US5.1/5.2 – Always stored as PENDING; never sent without human approval.
    """

    class ApprovalStatus(models.TextChoices):
        PENDING  = 'pending',  'Pending Review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        SENT     = 'sent',     'Sent to Client'

    class ReviewerRole(models.TextChoices):
        ADMINISTRATOR = 'administrator', 'Administrator'
        TECHNICIAN    = 'technician',    'Technician'

    client_request      = models.ForeignKey(ClientRequest, on_delete=models.CASCADE,
                                             related_name='ai_suggestions')
    suggested_response  = models.TextField()
    final_response      = models.TextField(blank=True)   # editable before sending
    approval_status     = models.CharField(max_length=20, choices=ApprovalStatus.choices,
                                           default=ApprovalStatus.PENDING)
    reviewed_by_role    = models.CharField(max_length=20, choices=ReviewerRole.choices, blank=True)
    reviewed_by_user_id = models.IntegerField(null=True, blank=True)
    reviewed_at         = models.DateTimeField(null=True, blank=True)
    sent_at             = models.DateTimeField(null=True, blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"AISuggestion #{self.pk} for Request #{self.client_request_id} ({self.approval_status})"

    @property
    def is_sendable(self):
        """BR5 – only approved suggestions may be dispatched."""
        return self.approval_status == self.ApprovalStatus.APPROVED