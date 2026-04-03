"""
tradiePrototype/management/commands/seed_sample_technician.py

Management command: seed_sample_technician
------------------------------------------
Creates one realistic sample Technician record and its associated
Django User account, UserProfile (role=Technician), and authentication Token.

This mirrors the exact pipeline executed by TechnicianViewSet.create() (UC6)
without triggering HTTP validation, email side-effects, or requiring an
active administrator session. Intended for development and demonstration use only.

The temporary password is set to the technician's phone number, consistent
with the UC6 business rule implemented in the view layer.

Usage:
    python manage.py seed_sample_technician

Options:
    --force     Delete and re-create the record if the seed email already exists.

File location:
    tradiePrototype/management/commands/seed_sample_technician.py

Required directory structure (create __init__.py files if absent):
    tradiePrototype/
        management/
            __init__.py
            commands/
                __init__.py
                seed_sample_technician.py
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from tradiePrototype.models import Technician, UserProfile


# ---------------------------------------------------------------------------
# Sample record definition
# ---------------------------------------------------------------------------

SAMPLE_DATA = {
    # Technician profile fields
    "first_name":   "Daniel",
    "last_name":    "Mercer",
    "email":        "daniel.mercer@tradierm.internal",
    "phone":        "0421 987 654",
    "gender":       Technician.Gender.MALE,
    "home_address": "14 Banksia Drive, Mitcham VIC 3132",
    "skill":        "Plumbing",
    "hourly_rate":  "95.00",
    "is_active":    True,

    # Django User account credentials
    "username":     "daniel.mercer",
}

# Seed identifier -- used to detect an existing record before re-creating.
SEED_EMAIL    = SAMPLE_DATA["email"]
SEED_USERNAME = SAMPLE_DATA["username"]


class Command(BaseCommand):
    """
    Django management command that provisions one sample Technician record.

    Creates:
        - Technician profile record
        - Django User account (username = SAMPLE_DATA["username"])
        - UserProfile with role=TECHNICIAN
        - DRF authentication Token

    Idempotent by default: exits cleanly if the seed email or username
    already exists. Pass --force to remove and re-create all four records.
    """

    help = (
        "Provisions one sample Technician record (with User, UserProfile, "
        "and Token) for UC6 development and demonstration purposes."
    )

    # ------------------------------------------------------------------
    # Argument definition
    # ------------------------------------------------------------------

    def add_arguments(self, parser):
        """
        Register optional command-line arguments.

        --force: Removes the existing Technician, User, UserProfile, and Token
                 records before creating fresh ones. Use when the model schema
                 has changed or the seed data needs to be reset.
        """
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Delete the existing seed Technician and associated User records "
                "before re-creating them. Without this flag the command is a no-op "
                "when either the email or username already exists."
            ),
        )

    # ------------------------------------------------------------------
    # Command entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        """
        Main execution method called by Django's management framework.

        Behaviour:
            1. Check whether a Technician or User with the seed identifiers exists.
            2. If either exists and --force is not set, print a notice and exit.
            3. If --force is set, delete the existing Technician and User records.
               Cascading deletes remove the associated UserProfile and Token.
            4. Create the Technician profile record.
            5. Create the Django User account with a temporary password.
            6. Create the UserProfile linking the User to the Technician role.
            7. Create or retrieve the DRF authentication Token.
            8. Print a confirmation summary.
        """
        force = options["force"]

        # ------------------------------------------------------------------
        # Existence checks
        # ------------------------------------------------------------------

        existing_technician = Technician.objects.filter(email=SEED_EMAIL).first()
        existing_user       = User.objects.filter(username=SEED_USERNAME).first()

        if (existing_technician or existing_user) and not force:
            self.stdout.write(
                self.style.WARNING(
                    "Sample Technician record already exists. "
                    "Pass --force to delete and re-create all associated records."
                )
            )
            if existing_technician:
                self.stdout.write(
                    f"  Existing Technician pk : {existing_technician.pk} "
                    f"({existing_technician.first_name} {existing_technician.last_name})"
                )
            if existing_user:
                self.stdout.write(
                    f"  Existing User pk       : {existing_user.pk} "
                    f"(username={existing_user.username})"
                )
            return

        # ------------------------------------------------------------------
        # Cleanup (--force path)
        # ------------------------------------------------------------------

        if force:
            if existing_technician:
                self.stdout.write(
                    f"Deleting existing Technician (pk={existing_technician.pk}) "
                    f"due to --force flag."
                )
                existing_technician.delete()

            if existing_user:
                self.stdout.write(
                    f"Deleting existing User (pk={existing_user.pk}, "
                    f"username={existing_user.username}) due to --force flag."
                )
                # Deleting the User cascades to UserProfile and Token
                # because both use ForeignKey(User, on_delete=CASCADE).
                existing_user.delete()

        # ------------------------------------------------------------------
        # Step 1 -- Create the Technician profile record (UC6, step 7)
        # ------------------------------------------------------------------

        technician = Technician.objects.create(
            first_name=SAMPLE_DATA["first_name"],
            last_name=SAMPLE_DATA["last_name"],
            email=SAMPLE_DATA["email"],
            phone=SAMPLE_DATA["phone"],
            gender=SAMPLE_DATA["gender"],
            home_address=SAMPLE_DATA["home_address"],
            skill=SAMPLE_DATA["skill"],
            hourly_rate=SAMPLE_DATA["hourly_rate"],
            is_active=SAMPLE_DATA["is_active"],
        )

        # ------------------------------------------------------------------
        # Step 2 -- Create the Django User account (UC6, step 8)
        # Temporary password is set to the technician's phone number,
        # consistent with the rule in TechnicianViewSet.create().
        # ------------------------------------------------------------------

        temp_password = SAMPLE_DATA["phone"]

        user = User.objects.create_user(
            username=SAMPLE_DATA["username"],
            password=temp_password,
            email=technician.email,
            first_name=technician.first_name,
            last_name=technician.last_name,
        )

        # ------------------------------------------------------------------
        # Step 3 -- Create the UserProfile with role=TECHNICIAN
        # ------------------------------------------------------------------

        UserProfile.objects.create(
            user=user,
            role=UserProfile.Role.TECHNICIAN,
            phone=technician.phone,
        )

        # ------------------------------------------------------------------
        # Step 4 -- Create the DRF authentication Token
        # ------------------------------------------------------------------

        token, _ = Token.objects.get_or_create(user=user)

        # ------------------------------------------------------------------
        # Confirmation summary
        # ------------------------------------------------------------------

        self.stdout.write(
            self.style.SUCCESS(
                f"Sample Technician provisioned successfully.\n"
                f"\n"
                f"  Technician\n"
                f"    pk           : {technician.pk}\n"
                f"    name         : {technician.first_name} {technician.last_name}\n"
                f"    email        : {technician.email}\n"
                f"    phone        : {technician.phone}\n"
                f"    gender       : {technician.gender}\n"
                f"    skill        : {technician.skill}\n"
                f"    hourly_rate  : ${technician.hourly_rate}/hr\n"
                f"    home_address : {technician.home_address}\n"
                f"    is_active    : {technician.is_active}\n"
                f"\n"
                f"  Django User\n"
                f"    pk           : {user.pk}\n"
                f"    username     : {user.username}\n"
                f"    temp password: {temp_password}\n"
                f"\n"
                f"  Auth Token    : {token.key}\n"
                f"\n"
                f"Login endpoint : POST /api/auth/login/\n"
                f"  Body         : username={user.username} / password={temp_password}"
            )
        )
