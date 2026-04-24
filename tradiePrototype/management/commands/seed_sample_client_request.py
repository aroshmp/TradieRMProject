"""
tradiePrototype/management/commands/seed_sample_client_request.py

Management command to create a sample ClientRequest record for development
and testing purposes.

Usage:
    python manage.py seed_sample_client_request
    python manage.py seed_sample_client_request --force

The --force flag deletes any existing sample record before creating a new one.
No HTTP requests or emails are sent -- this bypasses all side effects.
"""

from django.core.management.base import BaseCommand

from tradiePrototype.models import ClientRequest


SAMPLE_PAYLOAD = {
    "first_name": "Alice",
    "last_name":  "Sample",
    "email":      "doxdoxdox9@gmail.com",
    "phone":      "0412345678",
    "subject":    "Leaking tap in kitchen",
    "message":    "The kitchen tap has been leaking for a week. It gets worse when hot water is used.",
}


class Command(BaseCommand):
    """Create a sample ClientRequest record for development and testing."""

    help = "Seed a sample Unprocessed ClientRequest for UC2 development and testing."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help="Delete and re-create the sample record if it already exists.",
        )

    def handle(self, *args, **options):
        force    = options['force']
        existing = ClientRequest.objects.filter(
            email_address=SAMPLE_PAYLOAD['email']
        ).first()

        if existing and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"A sample ClientRequest already exists (pk={existing.pk}). "
                    f"Use --force to delete and re-create it."
                )
            )
            return

        if existing and force:
            self.stdout.write(
                f"Deleting existing sample ClientRequest (pk={existing.pk}) "
                f"due to --force flag."
            )
            existing.delete()

        # Build the raw_payload mirror -- replicates what the webhook view
        # stores for audit purposes.
        raw_payload = {
            "first_name": SAMPLE_PAYLOAD["first_name"],
            "last_name":  SAMPLE_PAYLOAD["last_name"],
            "email":      SAMPLE_PAYLOAD["email"],
            "phone":      SAMPLE_PAYLOAD["phone"],
            "subject":    SAMPLE_PAYLOAD["subject"],
            "message":    SAMPLE_PAYLOAD["message"],
        }

        # Create the ClientRequest record directly, bypassing HTTP and email
        # side-effects. This is intentional for seed/development data.
        client_request = ClientRequest.objects.create(
            first_name       = SAMPLE_PAYLOAD["first_name"],
            last_name        = SAMPLE_PAYLOAD["last_name"],
            email_address    = SAMPLE_PAYLOAD["email"],
            telephone_number = SAMPLE_PAYLOAD["phone"],
            subject          = SAMPLE_PAYLOAD["subject"],
            client_message   = SAMPLE_PAYLOAD["message"],
            source_ip        = "127.0.0.1",
            raw_payload      = raw_payload,
            status           = ClientRequest.Status.UNPROCESSED,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sample ClientRequest created successfully.\n"
                f"  pk               : {client_request.pk}\n"
                f"  first_name       : {client_request.first_name}\n"
                f"  last_name        : {client_request.last_name}\n"
                f"  email_address    : {client_request.email_address}\n"
                f"  telephone_number : {client_request.telephone_number}\n"
                f"  subject          : {client_request.subject}\n"
                f"  status           : {client_request.status}\n"
                f"  date_received    : {client_request.date_received}\n"
                f"\n"
                f"The record is now visible in the UC2 Job Requests pool.\n"
                f"Process it via: POST /api/client-requests/{client_request.pk}/process/"
            )
        )