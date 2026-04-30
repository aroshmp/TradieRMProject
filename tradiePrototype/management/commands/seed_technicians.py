"""
tradiePrototype/management/commands/seed_technicians.py

Management command to seed 5 sample Technician records for development
and demonstration purposes.

Each record provisions:
    - A Technician profile record
    - A Django User account (username = email address)
    - A UserProfile with role=TECHNICIAN
    - A DRF authentication Token

This mirrors the exact pipeline executed by TechnicianViewSet.create() (UC13)
without triggering HTTP validation, email side-effects, or requiring an active
administrator session.

The temporary password for every account is set to the technician's
telephone_number, consistent with the UC13 business rule implemented in the
view layer.

Usage:
    python manage.py seed_technicians
    python manage.py seed_technicians --force

Options:
    --force     Delete and re-create any record whose email or username already
                exists. Deleting the Django User cascades to the linked
                UserProfile and Token via on_delete=CASCADE.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from tradiePrototype.models import Technician, UserProfile


# ---------------------------------------------------------------------------
# Sample record definitions
# ---------------------------------------------------------------------------
# Field names align with the Database Dictionary and Technician model.
# username mirrors the email address, consistent with UC13.
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    {
        "first_name":       "Daniel",
        "last_name":        "Mercer",
        "email_address":    "doxdoxdox9+daniel@gmail.com",
        "telephone_number": "0411 100 001",
        "gender":           Technician.Gender.MALE,
        "physical_address": "14 Banksia Drive, Mitcham VIC 3132",
        "skill":            "Plumbing",
        "hourly_rate":      "95.00",
        "username":         "doxdoxdox9+daniel@gmail.com",
    },
    {
        "first_name":       "Sarah",
        "last_name":        "Nguyen",
        "email_address":    "doxdoxdox9+sarah@gmail.com",
        "telephone_number": "0422 200 002",
        "gender":           Technician.Gender.FEMALE,
        "physical_address": "82 Wattle Street, Hawthorn VIC 3122",
        "skill":            "Electrical",
        "hourly_rate":      "110.00",
        "username":         "doxdoxdox9+sarah@gmail.com",
    },
    {
        "first_name":       "Marcus",
        "last_name":        "Obi",
        "email_address":    "doxdoxdox9+marcus@gmail.com",
        "telephone_number": "0433 300 003",
        "gender":           Technician.Gender.MALE,
        "physical_address": "5 Ironbark Court, Ringwood VIC 3134",
        "skill":            "HVAC",
        "hourly_rate":      "105.00",
        "username":         "doxdoxdox9+marcus@gmail.com",
    },
    {
        "first_name":       "Priya",
        "last_name":        "Sharma",
        "email_address":    "doxdoxdox9+priya@gmail.com",
        "telephone_number": "0444 400 004",
        "gender":           Technician.Gender.FEMALE,
        "physical_address": "31 Eucalyptus Road, Box Hill VIC 3128",
        "skill":            "Carpentry",
        "hourly_rate":      "90.00",
        "username":         "doxdoxdox9+priya@gmail.com",
    },
    {
        "first_name":       "Thomas",
        "last_name":        "Gallagher",
        "email_address":    "doxdoxdox9+thomas@gmail.com",
        "telephone_number": "0455 500 005",
        "gender":           Technician.Gender.MALE,
        "physical_address": "67 Melaleuca Grove, Nunawading VIC 3131",
        "skill":            "Painting",
        "hourly_rate":      "85.00",
        "username":         "doxdoxdox9+thomas@gmail.com",
    },
]


class Command(BaseCommand):
    """
    Django management command that provisions 5 sample Technician records.

    For each record, creates:
        - Technician profile
        - Django User account (username = email address)
        - UserProfile with role=TECHNICIAN
        - DRF authentication Token

    Idempotent by default: skips any record whose email or username already
    exists. Pass --force to remove and re-create all associated records.
    """

    help = (
        "Provisions 5 sample Technician records (with User, UserProfile, and Token) "
        "for UC13 development and demonstration purposes."
    )

    def add_arguments(self, parser):
        """
        Register optional command-line arguments.

        --force: Removes existing Technician and User records for each seed
                 entry before re-creating them. Deleting the User cascades
                 to UserProfile and Token via on_delete=CASCADE.
        """
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Delete and re-create any seed Technician and associated User "
                "records that already exist. Without this flag, existing records "
                "are skipped and a warning is printed."
            ),
        )

    def handle(self, *args, **options):
        """
        Main execution method called by Django's management framework.

        For each entry in SAMPLE_RECORDS:
            1. Check for an existing Technician (by email) and User (by username).
            2. If either exists and --force is not set, skip and warn.
            3. If --force is set, delete the existing Technician and User.
               Cascading deletes remove the associated UserProfile and Token.
            4. Create the Technician profile record.
            5. Create the Django User account with a temporary password.
            6. Create the UserProfile with role=TECHNICIAN.
            7. Create or retrieve the DRF authentication Token.
        Prints a per-record summary and a final count on completion.
        """
        force   = options["force"]
        created = 0
        skipped = 0

        for record in SAMPLE_RECORDS:
            email    = record["email_address"]
            username = record["username"]

            existing_technician = Technician.objects.filter(
                email_address=email
            ).first()
            existing_user = User.objects.filter(username=username).first()

            # ------------------------------------------------------------------
            # Skip guard -- no --force
            # ------------------------------------------------------------------

            if (existing_technician or existing_user) and not force:
                self.stdout.write(
                    self.style.WARNING(
                        f"  SKIP  {email} -- record already exists. "
                        f"Use --force to overwrite."
                    )
                )
                skipped += 1
                continue

            # ------------------------------------------------------------------
            # Cleanup -- --force path
            # Deleting User cascades to UserProfile and Token.
            # ------------------------------------------------------------------

            if force:
                if existing_technician:
                    self.stdout.write(
                        f"  DELETE  Technician (pk={existing_technician.pk}, "
                        f"email={email}) due to --force flag."
                    )
                    existing_technician.delete()

                if existing_user:
                    self.stdout.write(
                        f"  DELETE  User (pk={existing_user.pk}, "
                        f"username={username}) due to --force flag."
                    )
                    existing_user.delete()

            # ------------------------------------------------------------------
            # Step 1 -- Create the Technician profile record (UC13)
            # ------------------------------------------------------------------

            technician = Technician.objects.create(
                first_name       = record["first_name"],
                last_name        = record["last_name"],
                email_address    = record["email_address"],
                telephone_number = record["telephone_number"],
                gender           = record["gender"],
                physical_address = record["physical_address"],
                skill            = record["skill"],
                hourly_rate      = record["hourly_rate"],
                role             = Technician.Role.TECHNICIAN,
                status           = Technician.Status.ACTIVE,
            )

            # ------------------------------------------------------------------
            # Step 2 -- Create the Django User account (UC13)
            # Temporary password is set to the technician's telephone_number,
            # consistent with the rule in TechnicianViewSet.create().
            # ------------------------------------------------------------------

            temp_password = record["telephone_number"]

            user = User.objects.create_user(
                username   = record["username"],
                password   = temp_password,
                email      = technician.email_address,
                first_name = technician.first_name,
                last_name  = technician.last_name,
            )

            # ------------------------------------------------------------------
            # Step 3 -- Create the UserProfile with role=TECHNICIAN
            # ------------------------------------------------------------------

            UserProfile.objects.create(
                user = user,
                role = UserProfile.Role.TECHNICIAN,
            )

            # ------------------------------------------------------------------
            # Step 4 -- Create the DRF authentication Token
            # ------------------------------------------------------------------

            token, _ = Token.objects.get_or_create(user=user)

            self.stdout.write(
                self.style.SUCCESS(
                    f"  CREATE  pk={technician.pk:<4} "
                    f"{technician.first_name} {technician.last_name} "
                    f"<{technician.email_address}>  skill={technician.skill}"
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
                f"\nLogin endpoint : POST /api/auth/login/\n"
                f"Credentials    : username=<email_address>  "
                f"password=<telephone_number>"
            )
        )