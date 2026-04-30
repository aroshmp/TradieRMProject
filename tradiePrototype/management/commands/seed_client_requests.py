"""
tradiePrototype/management/commands/seed_client_requests.py

Management command to seed 10 sample ClientRequest records for development
and demonstration purposes.

Each record represents a realistic inbound service request from a prospective
customer, covering a variety of trade service categories (plumbing, electrical,
HVAC, carpentry, painting). All records are created with UNPROCESSED status,
mirroring the state produced by the live webhook endpoint.

No HTTP requests or email side-effects are triggered -- records are inserted
directly via the ORM.

Usage:
    python manage.py seed_client_requests
    python manage.py seed_client_requests --force

Options:
    --force     Delete all existing seed records (matched by email address)
                and re-create them. Without this flag the command skips any
                record whose email already exists in the database.
"""

from django.core.management.base import BaseCommand

from tradiePrototype.models import ClientRequest


# ---------------------------------------------------------------------------
# Sample record definitions
# ---------------------------------------------------------------------------
# Each entry mirrors the fields accepted by the live webhook endpoint and
# maps directly to ClientRequest model fields.
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    {
        "first_name": "Alice",
        "last_name":  "Morrison",
        "email":      "doxdoxdox9+alice@gmail.com",
        "phone":      "0412 345 678",
        "subject":    "Leaking tap in kitchen",
        "message":    (
            "The kitchen tap has been dripping for over a week and is getting worse "
            "when hot water is used. I would like someone to inspect and repair it "
            "as soon as possible."
        ),
    },
    {
        "first_name": "Benjamin",
        "last_name":  "Tran",
        "email":      "doxdoxdox9+benjamin@gmail.com",
        "phone":      "0423 456 789",
        "subject":    "No hot water -- hot water system fault",
        "message":    (
            "Our hot water system stopped working yesterday morning. The pilot light "
            "appears to be out and we cannot get it to re-ignite. We have three young "
            "children at home and urgently need this resolved."
        ),
    },
    {
        "first_name": "Catherine",
        "last_name":  "Walsh",
        "email":      "doxdoxdox9+catherine@gmail.com",
        "phone":      "0434 567 890",
        "subject":    "Blocked drain in bathroom",
        "message":    (
            "The shower drain in our main bathroom is completely blocked. Water is "
            "pooling during showers and has started backing up into the bath. I have "
            "tried a standard plunger with no success."
        ),
    },
    {
        "first_name": "David",
        "last_name":  "Okafor",
        "email":      "doxdoxdox9+david@gmail.com",
        "phone":      "0445 678 901",
        "subject":    "Electrical fault -- power points not working",
        "message":    (
            "Three power points in the living room stopped working after a storm two "
            "days ago. The circuit breaker has been checked and appears fine. We need "
            "an electrician to diagnose the fault and restore power to that circuit."
        ),
    },
    {
        "first_name": "Emma",
        "last_name":  "Fitzgerald",
        "email":      "doxdoxdox9+emma@gmail.com",
        "phone":      "0456 789 012",
        "subject":    "Ceiling fan installation -- two rooms",
        "message":    (
            "We would like ceiling fans installed in the master bedroom and the study. "
            "Wiring points are already in place from a previous installation. Please "
            "advise on availability and whether we need to supply the fans ourselves."
        ),
    },
    {
        "first_name": "Frank",
        "last_name":  "Nguyen",
        "email":      "doxdoxdox9+frank@gmail.com",
        "phone":      "0467 890 123",
        "subject":    "Air conditioning unit not cooling",
        "message":    (
            "Our ducted air conditioning system is running but not cooling the house "
            "below 28 degrees even on the highest setting. The unit is approximately "
            "six years old and has not been serviced in the last two years."
        ),
    },
    {
        "first_name": "Grace",
        "last_name":  "Patel",
        "email":      "doxdoxdox9+grace@gmail.com",
        "phone":      "0478 901 234",
        "subject":    "Fence repair after storm damage",
        "message":    (
            "Two panels of our timber fence collapsed during last week's storm. The "
            "posts are intact but the paling boards and rails need replacement. The "
            "damaged section runs approximately six metres along the rear boundary."
        ),
    },
    {
        "first_name": "Henry",
        "last_name":  "Liu",
        "email":      "doxdoxdox9+henry@gmail.com",
        "phone":      "0489 012 345",
        "subject":    "Interior painting -- lounge and hallway",
        "message":    (
            "We are looking to repaint the lounge room and main hallway. Both rooms "
            "were last painted roughly ten years ago and the paint is peeling near "
            "the skirting boards. We can discuss colour selection during the inspection."
        ),
    },
    {
        "first_name": "Isabella",
        "last_name":  "Santos",
        "email":      "doxdoxdox9+isabella@gmail.com",
        "phone":      "0490 123 456",
        "subject":    "Toilet cistern constantly running",
        "message":    (
            "The toilet cistern in our ensuite has been running continuously for "
            "several days. It does not stop filling even after the bowl is full. "
            "Our water bill has increased noticeably and we would like it fixed promptly."
        ),
    },
    {
        "first_name": "James",
        "last_name":  "Kowalski",
        "email":      "doxdoxdox9+james@gmail.com",
        "phone":      "0401 234 567",
        "subject":    "Smoke alarm installation -- rental compliance",
        "message":    (
            "I am a landlord and need interconnected smoke alarms installed in my "
            "rental property to meet the updated Queensland rental compliance "
            "requirements. The property has three bedrooms and two living areas."
        ),
    },
]


class Command(BaseCommand):
    """
    Django management command that seeds 10 sample ClientRequest records.

    All records are inserted with UNPROCESSED status. Each record is matched
    by its email address; existing records are skipped unless --force is used.
    """

    help = (
        "Seeds 10 sample ClientRequest records (status=UNPROCESSED) for "
        "development and demonstration purposes."
    )

    def add_arguments(self, parser):
        """
        Register optional command-line arguments.

        --force: Delete any existing seed record whose email matches before
                 re-creating it. Records whose email does not match any seed
                 entry are not affected.
        """
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Delete and re-create any seed record whose email address already "
                "exists in the database. Without this flag, existing records are "
                "skipped and a warning is printed."
            ),
        )

    def handle(self, *args, **options):
        """
        Main execution method called by Django's management framework.

        Iterates over SAMPLE_RECORDS. For each entry:
            1. Check whether a ClientRequest with the same email exists.
            2. If it exists and --force is not set, skip and warn.
            3. If it exists and --force is set, delete the existing record.
            4. Create the new ClientRequest with UNPROCESSED status.
        Prints a summary of created and skipped records on completion.
        """
        force   = options["force"]
        created = 0
        skipped = 0

        for record in SAMPLE_RECORDS:
            existing = ClientRequest.objects.filter(
                email_address=record["email"]
            ).first()

            if existing and not force:
                self.stdout.write(
                    self.style.WARNING(
                        f"  SKIP  {record['email']} "
                        f"-- record already exists (pk={existing.pk}). "
                        f"Use --force to overwrite."
                    )
                )
                skipped += 1
                continue

            if existing and force:
                self.stdout.write(
                    f"  DELETE  existing ClientRequest (pk={existing.pk}, "
                    f"email={record['email']}) due to --force flag."
                )
                existing.delete()

            # Build the raw_payload mirror, replicating what the webhook view
            # stores for audit purposes.
            raw_payload = {
                "first_name": record["first_name"],
                "last_name":  record["last_name"],
                "email":      record["email"],
                "phone":      record["phone"],
                "subject":    record["subject"],
                "message":    record["message"],
            }

            # Create the ClientRequest record directly via the ORM, bypassing
            # HTTP and email side-effects. Intentional for seed data.
            client_request = ClientRequest.objects.create(
                first_name       = record["first_name"],
                last_name        = record["last_name"],
                email_address    = record["email"],
                telephone_number = record["phone"],
                subject          = record["subject"],
                client_message   = record["message"],
                source_ip        = "127.0.0.1",
                raw_payload      = raw_payload,
                status           = ClientRequest.Status.UNPROCESSED,
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"  CREATE  pk={client_request.pk:<4} "
                    f"{client_request.first_name} {client_request.last_name} "
                    f"<{client_request.email_address}>"
                )
            )
            created += 1

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeed complete. "
                f"Created: {created}  |  Skipped: {skipped}  |  "
                f"Total configured: {len(SAMPLE_RECORDS)}\n"
                f"All new records have status=UNPROCESSED and are visible "
                f"in the UC2 Job Requests pool."
            )
        )