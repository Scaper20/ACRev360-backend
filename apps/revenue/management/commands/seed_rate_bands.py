"""
Seeds a small set of real gazette-derived rate bands, transcribed from
docs/reference/KAC Gazette.xlsx. Only the tables whose structure is
unambiguous are transcribed here — kept deliberately narrow rather than
guessing at the gazette's messier sections:

- Control of Advertisement (30010034): 24 RANGE bands, one per sign/advert
  type, min/max per FIRST SCHEDULE (B 226).
- Liquor Licensing (30010051): 9 TIERED bands (Large/Medium/Small), one per
  licensed establishment type, per the Liquor Licencing Bye-Law's FIRST
  SCHEDULE (B 292).
- Communication Mast License (30010056): one unlabeled TIERED band
  (Large/Medium/Small) — the gazette gives one flat triple for the whole
  item, no sub-classification.

Deliberately NOT transcribed: Tenement Rate Collection (30010049)'s
multi-category schedule (National Companies / Category B / Category C /
Residential / Individuals), which tiers by location (Rural/Semi Urban/Urban)
across ~90 rows with inconsistent formatting, some blank cells, and at least
one obvious typo ("200.000.00"). That data is real but too ambiguous to
responsibly hardcode — enter it via the admin rate-band editor once it's
been confirmed against the council's actual current bye-law text.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.revenue.models import CouncilRevenueItem
from apps.revenue.services import replace_rate_bands
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council

# (label, min_amount, max_amount)
CONTROL_OF_ADVERTISEMENT_BANDS = [
    ("School Sign Board", 20800, 40800),
    ("Neon Sign", 20200, 40200),
    ("Metal Fixed", 10400, 20400),
    ("Wooden Fixed", 10800, 20800),
    ("Metal Standing (Two Faces)", 10800, 20800),
    ("Metal Standing (Dual Faces)", 10400, 20400),
    ("Wooden Standing", 10000, 20000),
    ("Wooden Standing (Two Faces)", 20000, 40000),
    ("Electrical Fixed", 15000, 25000),
    ("Plastic Fixed", 15000, 25000),
    ("Electrical Standing", 20000, 40000),
    ("Electrical Standing (Two Faces)", 38000, 68000),
    ("Plastic Standing", 20000, 40000),
    ("Plastic Standing (Two Faces)", 38000, 38000),
    ("Special Sign Board", 150000, 300000),
    ("Carving", 8000, 16000),
    ("Banners", 10000, 20000),
    ("Posters", 5000, 10000),
    ("Tin Plates", 20000, 40000),
    ("Advert on Cloths (Prior to Colour)", 30000, 60000),
    ("Major Highway / Town Bill Board", 350000, 500000),
    ("Lamp Plate Advert", 60000, 100000),
    ("Multinational Companies", 700000, 1000000),
    ("Financial Institution", 200000, 500000),
]

# (label, large, medium, small)
LIQUOR_LICENSING_BANDS = [
    ("Wholesale Liquor", 200000, 100000, 50000),
    ("Depot (Beer)", 500000, 250000, 150000),
    ("Departmental / Super Store Liquor", 200000, 100000, 50000),
    ("Supermarket / Shop", 50000, 20000, 15000),
    ("Restaurant Liquor", 20000, 10000, 5000),
    ("Hotels", 500000, 200000, 70000),
    ("Beer Parlor", 20000, 10000, 5000),
    ("Native Liquor", 1500, 500, 100),
    ("Club Liquor", 150000, 100000, 50000),
]

COMMUNICATION_MAST_TIERS = [("Large", 2000000), ("Medium", 1500000), ("Small", 1000000)]


class Command(BaseCommand):
    help = "Seed real gazette-derived rate bands for a few unambiguous KAC Gazette schedules."

    def add_arguments(self, parser):
        parser.add_argument("--council-code", default="KAC")

    @transaction.atomic
    def handle(self, *args, **options):
        # SET LOCAL (what set_council_context issues) is transaction-scoped —
        # must run inside one atomic block for the whole command, same as
        # seed_kuje.py's own @transaction.atomic handle().
        council = Council.objects.get(council_code=options["council_code"])
        set_council_context(council.id)

        self._seed_range_item(
            council, "30010034", "Control of Advertisement", CONTROL_OF_ADVERTISEMENT_BANDS
        )
        self._seed_tiered_item(
            council, "30010051", "Liquor Licensing",
            [(label, [("Large", large), ("Medium", medium), ("Small", small)]) for label, large, medium, small in LIQUOR_LICENSING_BANDS],
        )
        self._seed_tiered_item(
            council, "30010056", "Communication Mast License",
            [("", COMMUNICATION_MAST_TIERS)],
        )

    def _get_item(self, council, code, name):
        try:
            return CouncilRevenueItem.objects.get(council=council, harmonised_code=code)
        except CouncilRevenueItem.DoesNotExist:
            self.stdout.write(self.style.WARNING(f"{code} {name} not activated for {council.council_code} — skipping"))
            return None

    def _seed_range_item(self, council, code, name, bands):
        item = self._get_item(council, code, name)
        if item is None:
            return
        if item.active_bands.exists():
            self.stdout.write(self.style.WARNING(f"{code} {name} already has rate bands — skipping (already seeded)"))
            return
        replace_rate_bands(
            council_revenue_item=item,
            actor=None,
            bands=[
                {"label": label, "rate_mode": "RANGE", "min_amount": min_amount, "max_amount": max_amount}
                for label, min_amount, max_amount in bands
            ],
        )
        self.stdout.write(self.style.SUCCESS(f"{code} {name}: seeded {len(bands)} range bands"))

    def _seed_tiered_item(self, council, code, name, bands):
        item = self._get_item(council, code, name)
        if item is None:
            return
        if item.active_bands.exists():
            self.stdout.write(self.style.WARNING(f"{code} {name} already has rate bands — skipping (already seeded)"))
            return
        replace_rate_bands(
            council_revenue_item=item,
            actor=None,
            bands=[
                {
                    "label": label,
                    "rate_mode": "TIERED",
                    "tiers": [{"label": tier_label, "amount": amount} for tier_label, amount in tiers],
                }
                for label, tiers in bands
            ],
        )
        self.stdout.write(self.style.SUCCESS(f"{code} {name}: seeded {len(bands)} tiered band(s)"))
