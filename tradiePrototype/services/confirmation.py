"""
core/services/confirmation.py
BR3 – Auto-send a confirmation email when a client request is received.
"""

import logging
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from tradiePrototype.models import ClientRequest

logger = logging.getLogger(__name__)

CONFIRMATION_SUBJECT = "We've received your request"

CONFIRMATION_TEMPLATE = """Hi {name},

Thank you for getting in touch. This is an automated confirmation to let you
know we have received your request and our team will review it shortly.

  Subject : {subject}
  Received: {received_at}

We aim to respond within one business day. If your matter is urgent, please
call us directly.

Kind regards,
The Service Team
"""


def send_confirmation(client_request: ClientRequest) -> bool:
    """
    US3.1 – Send an automatic acknowledgement to the client.
    Returns True if the email was dispatched successfully.
    """
    if not client_request.contact_email:
        logger.warning("ClientRequest #%s has no email — skipping confirmation.", client_request.pk)
        return False

    body = CONFIRMATION_TEMPLATE.format(
        name=client_request.contact_name or "there",
        subject=client_request.subject or "your enquiry",
        received_at=client_request.created_at.strftime("%Y-%m-%d %H:%M UTC"),
    )

    try:
        send_mail(
            subject=CONFIRMATION_SUBJECT,
            message=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
            recipient_list=[client_request.contact_email],
            fail_silently=False,
        )
        client_request.status         = ClientRequest.Status.ACKNOWLEDGED
        client_request.acknowledged_at = timezone.now()
        client_request.save(update_fields=['status', 'acknowledged_at'])

        logger.info("Confirmation sent for ClientRequest #%s to %s",
                    client_request.pk, client_request.contact_email)
        return True

    except Exception as exc:
        logger.error("Failed to send confirmation for ClientRequest #%s: %s", client_request.pk, exc)
        return False