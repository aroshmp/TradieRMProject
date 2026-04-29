"""
tradiePrototype/management/commands/seed_inventory.py

Management command to seed 20 sample Inventory (job parts) records for
development and demonstration purposes.

Parts are drawn from the five trade categories serviced by the sample
technicians: Plumbing, Electrical, HVAC, Carpentry, and Painting.
Quantities and costs reflect realistic trade pricing for a small business
context.

Status is automatically derived by the Inventory.save() method based on
quantity -- records with quantity > 0 receive IN_STOCK; zero-quantity records
receive OUT_OF_STOCK. Two records are intentionally seeded at quantity=0 to
demonstrate the OUT_OF_STOCK state in the UI.

No HTTP requests or side-effects are triggered -- records are inserted
directly via the ORM.

Usage:
    python manage.py seed_inventory
    python manage.py seed_inventory --force

Options:
    --force     Delete any existing seed record whose name matches (case-
                insensitive) and re-create it. Records not in SAMPLE_RECORDS
                are not affected.
"""

from django.core.management.base import BaseCommand

from tradiePrototype.models import Inventory


# ---------------------------------------------------------------------------
# Sample record definitions
# ---------------------------------------------------------------------------
# Fields:
#   name        -- VARCHAR(255), unique identifier (read-only after creation)
#   description -- VARCHAR(255), human-readable detail
#   quantity    -- INTEGER; drives automatic status derivation on save()
#   cost        -- NUMERIC(10,2), unit cost in AUD
#
# Category grouping is for readability only; no category field exists on the
# Inventory model.
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    {
        "name":        "Brass Ball Valve 15mm",
        "description": "15 mm brass ball valve for copper and PEX water supply lines.",
        "quantity":    40,
        "cost":        "12.50",
    },
    {
        "name":        "Flexible Tap Connector 300mm",
        "description": "Braided stainless steel flexible hose, 300 mm, 3/8 inch fittings.",
        "quantity":    60,
        "cost":        "8.75",
    },
    {
        "name":        "Cistern Inlet Valve",
        "description": "Universal bottom-entry fill valve compatible with most close-coupled cisterns.",
        "quantity":    25,
        "cost":        "18.00",
    },
    {
        "name":        "PVC Pipe 50mm x 3m",
        "description": "50 mm diameter Class 12 PVC pressure pipe, 3 metre length.",
        "quantity":    30,
        "cost":        "22.40",
    },
    {
        "name":        "Pipe Thread Sealing Tape",
        "description": "PTFE thread seal tape, 12 mm x 10 m roll, suitable for gas and water fittings.",
        "quantity":    0,
        "cost":        "1.95",
    },

    # ------------------------------------------------------------------
    # Electrical
    # ------------------------------------------------------------------
    {
        "name":        "Double GPO Power Point",
        "description": "10 A double general-purpose outlet, white, flush-mount, 3-pin.",
        "quantity":    50,
        "cost":        "14.20",
    },
    {
        "name":        "10A Circuit Breaker",
        "description": "Single-pole 10 A Type B miniature circuit breaker for DIN rail mounting.",
        "quantity":    35,
        "cost":        "9.80",
    },
    {
        "name":        "Smoke Alarm 240V Interconnectable",
        "description": (
            "240 V photoelectric smoke alarm with 9 V battery backup, "
            "interconnectable via 3-wire link."
        ),
        "quantity":    20,
        "cost":        "54.00",
    },
    {
        "name":        "2.5mm Twin & Earth Cable 10m",
        "description": "2.5 mm flat TPS twin and earth wiring cable, 10 metre coil.",
        "quantity":    18,
        "cost":        "38.50",
    },
    {
        "name":        "Junction Box 100mm IP54",
        "description": "100 mm x 100 mm ABS junction box, IP54-rated, includes 4 x M20 cable glands.",
        "quantity":    0,
        "cost":        "7.60",
    },

    # ------------------------------------------------------------------
    # HVAC
    # ------------------------------------------------------------------
    {
        "name":        "AC Filter 20x20 Inch",
        "description": "Pleated panel filter, MERV-8 rating, 20 x 20 x 1 inch, ducted system.",
        "quantity":    45,
        "cost":        "11.00",
    },
    {
        "name":        "Refrigerant R32 1kg",
        "description": "R32 single-component refrigerant, 1 kg cylinder, for split-system recharge.",
        "quantity":    12,
        "cost":        "42.00",
    },
    {
        "name":        "Condensate Drain Pan 600mm",
        "description": "Galvanised steel condensate collection tray, 600 mm x 300 mm, 40 mm deep.",
        "quantity":    10,
        "cost":        "28.90",
    },
    {
        "name":        "Fan Capacitor 5uF",
        "description": "5 microfarad run capacitor for evaporative and HVAC fan motors, 450 V AC.",
        "quantity":    22,
        "cost":        "6.50",
    },

    # ------------------------------------------------------------------
    # Carpentry
    # ------------------------------------------------------------------
    {
        "name":        "Butt Hinge 75mm Stainless",
        "description": "75 mm x 75 mm x 2.5 mm 304 stainless steel butt hinge, pair, includes screws.",
        "quantity":    80,
        "cost":        "5.40",
    },
    {
        "name":        "Treated Pine Paling 1.8m",
        "description": "Treated pine fence paling, 100 mm x 12 mm, 1.8 metre length, H3 treated.",
        "quantity":    150,
        "cost":        "3.80",
    },
    {
        "name":        "Decking Screw 65mm Box",
        "description": "65 mm self-embedding head Type 17 decking screws, 316 stainless, 500-piece box.",
        "quantity":    15,
        "cost":        "34.00",
    },
    {
        "name":        "Timber Connector Bracket 90x90",
        "description": "90 x 90 x 90 mm galvanised post cap connector bracket, load-rated.",
        "quantity":    40,
        "cost":        "9.20",
    },

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    {
        "name":        "Interior Wall Paint 10L White",
        "description": "Low-VOC water-based interior wall paint, flat sheen, white base, 10 litre.",
        "quantity":    14,
        "cost":        "78.00",
    },
    {
        "name":        "9 Inch Paint Roller Kit",
        "description": (
            "9 inch roller frame with 12 mm nap sleeve, extension pole, and tray. "
            "Suitable for emulsion and acrylic paints."
        ),
        "quantity":    25,
        "cost":        "19.95",
    },
]


class Command(BaseCommand):
    """
    Django management command that seeds 20 sample Inventory records.

    Each record is matched by its name field (case-insensitive). Existing
    records are skipped unless --force is used.
    """

    help = (
        "Seeds 20 sample Inventory (job parts) records across five trade "
        "categories for development and demonstration purposes."
    )

    def add_arguments(self, parser):
        """
        Register optional command-line arguments.

        --force: Delete any existing record whose name matches a seed entry
                 and re-create it. Records not present in SAMPLE_RECORDS are
                 not affected.
        """
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Delete and re-create any seed Inventory record whose name "
                "already exists. Without this flag, existing records are "
                "skipped and a warning is printed."
            ),
        )

    def handle(self, *args, **options):
        """
        Main execution method called by Django's management framework.

        For each entry in SAMPLE_RECORDS:
            1. Check for an existing Inventory record with a matching name
               (case-insensitive).
            2. If it exists and --force is not set, skip and warn.
            3. If --force is set, delete the existing record.
            4. Create the new Inventory record.
               Status is derived automatically by Inventory.save().
        Prints a per-record summary and a final count on completion.
        """
        force   = options["force"]
        created = 0
        skipped = 0

        for record in SAMPLE_RECORDS:
            existing = Inventory.objects.filter(
                name__iexact=record["name"]
            ).first()

            if existing and not force:
                self.stdout.write(
                    self.style.WARNING(
                        f"  SKIP  \"{record['name']}\" "
                        f"-- record already exists (pk={existing.pk}). "
                        f"Use --force to overwrite."
                    )
                )
                skipped += 1
                continue

            if existing and force:
                self.stdout.write(
                    f"  DELETE  existing Inventory (pk={existing.pk}, "
                    f"name=\"{existing.name}\") due to --force flag."
                )
                existing.delete()

            # Create the Inventory record.
            # Status (IN_STOCK / OUT_OF_STOCK) is set automatically by the
            # model's save() method based on the supplied quantity value.
            inventory = Inventory.objects.create(
                name        = record["name"],
                description = record["description"],
                quantity    = record["quantity"],
                cost        = record["cost"],
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"  CREATE  pk={inventory.pk:<4} "
                    f"qty={str(inventory.quantity):<5} "
                    f"status={inventory.status:<12} "
                    f"\"{inventory.name}\""
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
                f"Two records are seeded at quantity=0 (OUT_OF_STOCK) to "
                f"demonstrate that status in the UC20/UC21 inventory views."
            )
        )