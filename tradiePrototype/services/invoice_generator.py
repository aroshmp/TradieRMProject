"""
tradiePrototype/services/invoice_generator.py

Service layer for UC24 -- Complete Job (Technician-Triggered).

Responsibilities:
    1. Derive hours_taken from Job.start_time and Job.end_time.
    2. Pull distance from the job's confirmed Booking record.
    3. Sum parts cost from all JobInventory lines linked to the job.
    4. Create a draft Invoice record with all cost fields pre-populated.
    5. Create an in-system Notification record for every administrator user
       so the dashboard can surface the pending invoice (UC24, step 10).
    6. Send an email notification to ADMIN_NOTIFICATION_EMAIL (UC24, step 10).

This module contains no view logic. It is imported by JobViewSet.update_status
in viewsets.py and called after the job status is saved as Completed.

Fallback behaviour (UC24 alternate courses):
    - If start_time or end_time is missing, hours_taken is set to 0.00 and a
      warning is logged (UC24, step 9a).
    - If no confirmed Booking with a distance value exists, distance and
      distance_cost are set to 0.00 and a warning is logged (UC24, step 9b).
    - If no JobInventory lines exist, parts_cost is set to 0.00 (UC24, step 9c).
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils import timezone

from tradiePrototype.models import (
    Booking,
    Invoice,
    Job,
    Notification,
    UserProfile,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------

# Percentage used to calculate the service charge on invoices (UC25, step 7).
# Example: a value of 10 means 10% service charge.
# Add INVOICE_SERVICE_CHARGE_PERCENTAGE to settings.py to override.
_DEFAULT_SERVICE_CHARGE_PERCENTAGE = Decimal('10.00')

# Rate in dollars per kilometre used to calculate distance cost (UC24, step 9).
# Add INVOICE_DISTANCE_RATE to settings.py to override.
_DEFAULT_DISTANCE_RATE = Decimal('1.50')


def _get_service_charge_percentage() -> Decimal:
    """
    Return the system-configured service charge percentage as a Decimal.
    Falls back to _DEFAULT_SERVICE_CHARGE_PERCENTAGE if the setting is absent.
    """
    raw = getattr(settings, 'INVOICE_SERVICE_CHARGE_PERCENTAGE', _DEFAULT_SERVICE_CHARGE_PERCENTAGE)
    return Decimal(str(raw))


def _get_distance_rate() -> Decimal:
    """
    Return the system-configured distance rate ($/km) as a Decimal.
    Falls back to _DEFAULT_DISTANCE_RATE if the setting is absent.
    """
    raw = getattr(settings, 'INVOICE_DISTANCE_RATE', _DEFAULT_DISTANCE_RATE)
    return Decimal(str(raw))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_invoice(job: Job) -> Invoice:
    """
    UC24, step 9 -- Create a draft Invoice record for a completed job.

    This function is idempotent with respect to duplicate calls: if an Invoice
    already exists for the job (e.g., from a retry), it is returned unchanged
    and no new notifications are created.

    Parameters:
        job -- A Job instance whose status has already been set to Completed
               and saved. The instance must be fully populated (customer and
               technician relations accessible).

    Returns:
        The created (or existing) Invoice instance.

    Raises:
        ValueError -- if job.status is not Completed. The caller must ensure
                      the status transition is saved before calling this function.
    """
    if not job.is_completed:
        raise ValueError(
            f"generate_invoice called for Job #{job.pk} with status "
            f"'{job.status}'. Job must be Completed before an invoice can be generated."
        )

    # Guard against duplicate invoice creation (idempotency).
    existing = Invoice.objects.filter(job=job).first()
    if existing:
        logger.warning(
            "Invoice already exists for Job #%s (Invoice #%s). "
            "Returning existing record without modification.",
            job.pk, existing.pk,
        )
        return existing

    # Derive each cost component independently so a failure in one does not
    # prevent the invoice from being created with partial data.
    hours_taken  = _derive_hours_taken(job)
    hourly_rate  = _derive_hourly_rate(job)
    distance_km  = _derive_distance(job)
    distance_rate = _get_distance_rate()
    parts_cost   = _derive_parts_cost(job)

    # Calculate derived fields using the UC25 formula so the draft already
    # has reasonable values before the administrator reviews it.
    labour_cost    = (hours_taken * hourly_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    distance_cost  = (distance_km * distance_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    subtotal       = labour_cost + distance_cost + parts_cost

    scp            = _get_service_charge_percentage()
    service_charge = (subtotal * scp / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total_cost     = subtotal + service_charge

    invoice = Invoice.objects.create(
        job                       = job,
        technician                = job.technician,
        status                    = Invoice.Status.DRAFT,
        hours_taken               = hours_taken,
        hourly_rate               = hourly_rate,
        labour_cost               = labour_cost,
        distance                  = distance_km,
        distance_rate             = distance_rate,
        distance_cost             = distance_cost,
        parts_cost                = parts_cost,
        subtotal                  = subtotal,
        service_charge_percentage = scp,
        service_charge            = service_charge,
        total_cost                = total_cost,
    )

    logger.info(
        "Draft Invoice #%s created for Job #%s -- "
        "hours_taken=%.2f, labour=%.2f, distance_cost=%.2f, "
        "parts=%.2f, total=%.2f.",
        invoice.pk, job.pk,
        hours_taken, labour_cost, distance_cost, parts_cost, total_cost,
    )

    # Notify all administrators via both database record and email (UC24, step 10).
    _notify_administrators(job, invoice)

    return invoice


# ---------------------------------------------------------------------------
# Cost derivation helpers
# ---------------------------------------------------------------------------

def _derive_hours_taken(job: Job) -> Decimal:
    """
    UC24, step 9 -- Derive hours_taken from Job.start_time and Job.end_time.

    Returns the duration in hours as a Decimal rounded to two decimal places.
    Returns Decimal('0.00') and logs a warning if either timestamp is missing
    (UC24, step 9a alternate course).
    """
    if not job.start_time or not job.end_time:
        logger.warning(
            "UC24 step 9a -- Job #%s is missing start_time or end_time. "
            "hours_taken set to 0.00 on draft invoice.",
            job.pk,
        )
        return Decimal('0.00')

    delta_seconds = (job.end_time - job.start_time).total_seconds()

    if delta_seconds <= 0:
        logger.warning(
            "UC24 step 9a -- Job #%s has end_time <= start_time (delta: %ss). "
            "hours_taken set to 0.00 on draft invoice.",
            job.pk, delta_seconds,
        )
        return Decimal('0.00')

    hours = Decimal(str(delta_seconds)) / Decimal('3600')
    return hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _derive_hourly_rate(job: Job) -> Decimal:
    """
    UC24, step 9 -- Retrieve the technician's hourly rate for the job.

    The rate is copied from Technician.hourly_rate at invoice creation time
    to preserve a historical snapshot. Returns Decimal('0.00') and logs a
    warning if no technician is assigned.
    """
    if not job.technician:
        logger.warning(
            "UC24 step 9a -- Job #%s has no assigned technician. "
            "hourly_rate set to 0.00 on draft invoice.",
            job.pk,
        )
        return Decimal('0.00')

    return Decimal(str(job.technician.hourly_rate))


def _derive_distance(job: Job) -> Decimal:
    """
    UC24, step 9 -- Retrieve the road distance from the job's confirmed Booking.

    Queries for the most recent Confirmed booking with a non-null distance value.
    Returns Decimal('0.00') and logs a warning if no qualifying booking exists
    (UC24, step 9b alternate course).
    """
    booking = (
        Booking.objects
        .filter(
            job=job,
            status=Booking.Status.CONFIRMED,
            distance__isnull=False,
        )
        .order_by('-created_at')
        .first()
    )

    if not booking or booking.distance is None:
        logger.warning(
            "UC24 step 9b -- No confirmed Booking with a distance value found "
            "for Job #%s. distance and distance_cost set to 0.00 on draft invoice.",
            job.pk,
        )
        return Decimal('0.00')

    return Decimal(str(booking.distance))


def _derive_parts_cost(job: Job) -> Decimal:
    """
    UC24, step 9 -- Sum all JobInventory line totals for the job.

    Returns Decimal('0.00') if no parts have been assigned (UC24, step 9c).
    Each line_total is computed as quantity_used * inventory.cost on the model.
    """
    lines = job.job_inventory.select_related('inventory').all()

    if not lines.exists():
        # UC24, step 9c -- no parts assigned; parts_cost set to 0.00 silently.
        return Decimal('0.00')

    total = sum(Decimal(str(line.line_total)) for line in lines)
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Administrator notification helpers
# ---------------------------------------------------------------------------

def _notify_administrators(job: Job, invoice: Invoice) -> None:
    """
    UC24, step 10 -- Notify all administrator users that a completed job
    requires invoice finalisation.

    Creates one Notification database record per administrator account so
    the dashboard can surface pending invoices. Also sends a single email
    to settings.ADMIN_NOTIFICATION_EMAIL.

    Both channels are attempted independently. A failure in one does not
    suppress the other.
    """
    _create_notification_records(job, invoice)
    _send_admin_completion_email(job, invoice)


def _create_notification_records(job: Job, invoice: Invoice) -> None:
    """
    Create one Notification record for each active administrator user.

    Administrators are identified by their UserProfile.role. If no
    administrator accounts are found, a warning is logged and the function
    returns without error so invoice creation is not blocked.
    """
    admin_users = User.objects.filter(
        profile__role=UserProfile.Role.ADMINISTRATOR,
        is_active=True,
    )

    if not admin_users.exists():
        logger.warning(
            "UC24 step 10 -- No active administrator users found. "
            "No Notification records created for Job #%s.",
            job.pk,
        )
        return

    customer = job.customer
    message = (
        f"Job #{job.pk} ({job.subject}) for customer "
        f"{customer.first_name} {customer.last_name} has been completed. "
        f"Draft Invoice #{invoice.pk} is ready for review and approval."
    )

    notifications = [
        Notification(
            recipient         = admin_user,
            notification_type = Notification.NotificationType.JOB_COMPLETED,
            job               = job,
            invoice           = invoice,
            message           = message,
        )
        for admin_user in admin_users
    ]

    Notification.objects.bulk_create(notifications)

    logger.info(
        "UC24 step 10 -- %d Notification record(s) created for Job #%s / Invoice #%s.",
        len(notifications), job.pk, invoice.pk,
    )


def _send_admin_completion_email(job: Job, invoice: Invoice) -> None:
    """
    UC24, step 10 -- Send a completion email to settings.ADMIN_NOTIFICATION_EMAIL.

    Logs a warning and returns without error if the setting is not configured,
    consistent with the existing pattern used by _send_admin_new_request_notification
    in viewsets.py.
    """
    admin_email = getattr(settings, 'ADMIN_NOTIFICATION_EMAIL', None)
    if not admin_email:
        logger.warning(
            "ADMIN_NOTIFICATION_EMAIL is not configured in settings. "
            "Completion email for Job #%s skipped.",
            job.pk,
        )
        return

    customer   = job.customer
    technician = job.technician

    subject = f"Job #{job.pk} completed -- Invoice #{invoice.pk} requires review"
    message = (
        f"A job has been completed and a draft invoice is awaiting your review.\n\n"
        f"  Job ID       : #{job.pk}\n"
        f"  Subject      : {job.subject}\n"
        f"  Customer     : {customer.first_name} {customer.last_name}\n"
        f"  Technician   : "
        f"{technician.first_name} {technician.last_name if technician else 'Unassigned'}\n"
        f"  Completed at : {job.end_time.strftime('%d %B %Y %H:%M UTC') if job.end_time else 'N/A'}\n\n"
        f"  Invoice ID   : #{invoice.pk}\n"
        f"  Labour Cost  : ${invoice.labour_cost:.2f}\n"
        f"  Distance Cost: ${invoice.distance_cost:.2f}\n"
        f"  Parts Cost   : ${invoice.parts_cost:.2f}\n"
        f"  Total Cost   : ${invoice.total_cost:.2f}\n\n"
        f"Log in to TradieRM and navigate to Invoices to review and approve."
    )

    try:
        send_mail(
            subject      = subject,
            message      = message,
            from_email   = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list = [admin_email],
            fail_silently  = False,
        )
        logger.info(
            "UC24 step 10 -- Completion email sent to '%s' for Job #%s.",
            admin_email, job.pk,
        )
    except Exception as exc:
        logger.error(
            "UC24 step 10 -- Failed to send completion email for Job #%s: %s",
            job.pk, exc,
        )