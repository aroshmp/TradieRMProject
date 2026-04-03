"""
tradiePrototype/management/commands/seed_sample_client_request.py

Management command: seed_sample_client_request
-----------------------------------------------
Creates one realistic sample ClientRequest record directly in the database,
simulating a job request that would normally arrive via the UC8 webhook endpoint
(POST /api/webhook/job-request/).

The record is created with status UNPROCESSED so it appears immediately in
the UC1 Job Requests pool and can be processed by the administrator.

Usage:
    python manage.py seed_sample_client_request

Options:
    --force     Re-create the record even if a sample record already exists
                for the seed email address.

File location:
    tradiePrototype/management/commands/seed_sample_client_request.py

Required directory structure (create __init__.py files if absent):
    tradiePrototype/
        management/
            __init__.py
            commands/
                __init__.py
                seed_sample_client_request.py
"""

from django.core.management.base import BaseCommand, CommandError

from tradiePrototype.models import ClientRequest


# ---------------------------------------------------------------------------
# Sample record definition
# ---------------------------------------------------------------------------

SAMPLE_PAYLOAD = {
    "name":    "James Thornton",
    "email":   "james.thornton@example.com",
    "phone":   "0412 345 678",
    "subject": "Hot water system not working",
    "message": (
        "Hi, our hot water system stopped working yesterday evening. "
        "We have had no hot water since then. The unit is a Rheem gas "
        "storage system, approximately 8 years old. There is no pilot "
        "light visible and the system makes no sound when the tap is "
        "turned on. We live in a single-storey home. Could someone "
        "please come out to assess and repair it as soon as possible? "
        "Best time to call is before 9 am or after 5 pm."
    ),
}

# Seed identifier -- used to detect an existing seed record before re-creating.
SEED_EMAIL = SAMPLE_PAYLOAD["email"]


class Command(BaseCommand):
    """
    Django management command that inserts one sample ClientRequest record.

    Idempotent by default: the command does nothing if a record with the
    seed email address already exists. Pass --force to delete and re-create.
    """

    help = (
        "Inserts one sample ClientRequest record (status=Unprocessed) for "
        "UC1/UC8 development and demonstration purposes."
    )

    # ------------------------------------------------------------------
    # Argument definition
    # ------------------------------------------------------------------

    def add_arguments(self, parser):
        """
        Register optional command-line arguments.

        --force: Removes any existing seed record before creating a fresh one.
                 Useful when the model schema has changed during development.
        """
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Delete the existing seed record (if any) and re-create it. "
                "Without this flag the command is a no-op when the record exists."
            ),
        )

    # ------------------------------------------------------------------
    # Command entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        """
        Main execution method called by Django's management framework.

        Behaviour:
            1. Check whether a ClientRequest with the seed email already exists.
            2. If it exists and --force is not set, print a notice and exit.
            3. If --force is set, delete the existing record first.
            4. Create the new ClientRequest record with status UNPROCESSED.
            5. Print a confirmation with the assigned primary key.

        Raises:
            CommandError: Propagated on unexpected database errors.
        """
        force = options["force"]

        # Detect an existing seed record.
        existing = ClientRequest.objects.filter(
            contact_email=SEED_EMAIL
        ).first()

        if existing and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Sample ClientRequest already exists (pk={existing.pk}, "
                    f"status={existing.status}). "
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
        # stores for audit purposes (UC8, raw_payload field).
        raw_payload = {
            "name":    SAMPLE_PAYLOAD["name"],
            "email":   SAMPLE_PAYLOAD["email"],
            "phone":   SAMPLE_PAYLOAD["phone"],
            "subject": SAMPLE_PAYLOAD["subject"],
            "message": SAMPLE_PAYLOAD["message"],
        }

        # Create the ClientRequest record directly, bypassing HTTP and email
        # side-effects (no confirmation or admin notification email is sent).
        # This is intentional for seed/development data.
        client_request = ClientRequest.objects.create(
            contact_name=SAMPLE_PAYLOAD["name"],
            contact_email=SAMPLE_PAYLOAD["email"],
            contact_phone=SAMPLE_PAYLOAD["phone"],
            subject=SAMPLE_PAYLOAD["subject"],
            message=SAMPLE_PAYLOAD["message"],
            source_ip="127.0.0.1",         # Simulated local source for seed data.
            raw_payload=raw_payload,
            status=ClientRequest.Status.UNPROCESSED,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sample ClientRequest created successfully.\n"
                f"  pk            : {client_request.pk}\n"
                f"  contact_name  : {client_request.contact_name}\n"
                f"  contact_email : {client_request.contact_email}\n"
                f"  contact_phone : {client_request.contact_phone}\n"
                f"  subject       : {client_request.subject}\n"
                f"  status        : {client_request.status}\n"
                f"  created_at    : {client_request.created_at}\n"
                f"\n"
                f"The record is now visible in the UC1 Job Requests pool.\n"
                f"Process it via: POST /api/client-requests/{client_request.pk}/process/"
            )
        )