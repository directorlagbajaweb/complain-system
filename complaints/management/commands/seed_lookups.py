"""
Create the units and categories the system needs in order to be usable.

Run with:  python manage.py seed_lookups

These rows are reference data, not test data — a complaint cannot be filed
without a category to file it under, so a fresh database needs them before
anything works. The command is safe to run repeatedly: it uses get_or_create,
so running it twice changes nothing and never duplicates a row.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from complaints.models import Category, Unit

# The office, and the kinds of complaint that office is responsible for.
# Editing this map and re-running the command is how you add a category.
UNITS_AND_CATEGORIES = {
    "Bursary": [
        "School fees payment",
        "Refunds and overpayment",
    ],
    "Student Affairs": [
        "Hostel accommodation",
        "Student welfare",
    ],
    "ICT": [
        "Student portal access",
        "Campus network and Wi-Fi",
        "Student email account",
    ],
    "Works & Maintenance": [
        "Electricity supply",
        "Water and plumbing",
    ],
    "Exams & Records": [
        "Missing or incorrect result",
        "Course registration error",
    ],
}


class Command(BaseCommand):
    help = "Create the standard units and complaint categories. Safe to re-run."

    @transaction.atomic
    def handle(self, *args, **options):
        units_created = 0
        categories_created = 0

        for unit_name, category_names in UNITS_AND_CATEGORIES.items():
            unit, created = Unit.objects.get_or_create(name=unit_name)
            units_created += created
            self.stdout.write(f"{'created' if created else '  exists'}  unit: {unit_name}")

            for category_name in category_names:
                _, created = Category.objects.get_or_create(
                    name=category_name, unit=unit
                )
                categories_created += created
                self.stdout.write(
                    f"{'created' if created else '  exists'}    category: {category_name}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {units_created} new unit(s), "
                f"{categories_created} new category(ies). "
                f"Totals now: {Unit.objects.count()} units, "
                f"{Category.objects.count()} categories."
            )
        )
