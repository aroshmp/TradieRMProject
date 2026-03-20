"""
core/services/invoice_generator.py
BR10 – Generate an invoice when a job is marked as completed.
"""

from datetime import date, timedelta
from django.utils import timezone
from tradiePrototype.models import Invoice, Job

DEFAULT_LABOUR_RATE = 85.00
DEFAULT_PAYMENT_INSTRUCTIONS = (
    "Payment is due within 14 days of the invoice date. "
    "Please reference your invoice number when making payment. "
    "Accepted methods: bank transfer, credit card, or cheque."
)


def generate_invoice(job: Job, labour_hours: float = 0.0, labour_rate: float = None) -> Invoice:
    """
    US10.1 – Create (or update) an Invoice for a completed job.
    Calculates parts total, labour total, tax, and grand total automatically.
    Returns the Invoice instance in DRAFT status (admin reviews before sending).
    """
    if not job.is_completed:
        raise ValueError(
            f"Cannot generate invoice for job #{job.pk}: status is '{job.status}', not 'completed'."
        )

    rate = labour_rate if labour_rate is not None else DEFAULT_LABOUR_RATE

    invoice, _ = Invoice.objects.get_or_create(
        job=job,
        defaults={
            'labour_hours': labour_hours,
            'labour_rate': rate,
            'payment_instructions': DEFAULT_PAYMENT_INSTRUCTIONS,
            'issued_at': timezone.now(),
            'due_date': date.today() + timedelta(days=14),
        }
    )

    # Recalculate totals whether new or existing
    invoice.labour_hours = labour_hours
    invoice.labour_rate  = rate
    invoice.calculate_totals()
    invoice.save()

    return invoice