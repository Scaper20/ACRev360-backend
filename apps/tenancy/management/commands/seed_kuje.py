"""
Seeds Kuje Area Council (KAC) as council #1 through the real onboarding flow —
V2_ARCHITECTURE.md §11 phase 1. Revenue items/codes come from
docs/reference/Kuje Revenue Item and Code List.xlsx (KAC's actual 31-item list,
plus "Community and Development Levy" which appears on every real sample bill but
wasn't in that list — see SESSION_HANDOFF.md §5). Rates are illustrative pending
the Council's confirmed current rates schedule (PRD.md §6) — not the same claim as
"this is production revenue data."
"""
import secrets

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import AppRole, AppUser
from apps.payments.models import PaymentChannel
from apps.revenue.models import RevenueCategory, RevenueItemTemplate
from apps.tenancy.models import Council, WardZone
from apps.tenancy.services import activate_template_item, onboard_council

CATEGORIES = [
    "Rates",
    "Licences and Permits",
    "Fees and Charges",
    "Registration and Professional Fees",
    "Levies",
]

# (harmonised_code, item_name, unit_of_charge, category, illustrative_rate)
REVENUE_ITEMS = [
    ("30010031", "Registration of Marriages, Births and Death", "Per Registration", "Registration and Professional Fees", 5000),
    ("30010032", "Motor Parks (Commercial Vehicles picking up passengers)", "Per Annum", "Fees and Charges", 15000),
    ("30010033", "Environmental Sanitation and Premise Inspection", "Per Annum", "Fees and Charges", 10000),
    ("30010034", "Control of Advertisement", "Per Annum", "Licences and Permits", 20000),
    ("30010036", "Mobile Advert", "Per Annum", "Licences and Permits", 15000),
    ("30010035", "Stacking of Building Material / Construction Permit", "Per Permit", "Licences and Permits", 25000),
    ("30010037", "Loading/Off Loading Control of Traffic", "Per Annum", "Fees and Charges", 10000),
    ("30010038", "Cutting Of Road Tar", "Per Permit", "Fees and Charges", 30000),
    ("30010039", "Movement and Keeping of Dogs", "Per Annum", "Fees and Charges", 5000),
    ("30010040", "Numbering / Street Naming", "Per Annum", "Registration and Professional Fees", 5000),
    ("30010041", "Registration of Dry Cleaning and Laundry Houses", "Per Annum", "Registration and Professional Fees", 10000),
    ("30010042", "Market Regulation", "Per Annum", "Fees and Charges", 15000),
    ("30010043", "Trade License, Private Lockup Shops and Allied Matters", "Per Annum", "Licences and Permits", 20000),
    ("30010044", "Radio and Television License", "Per Annum", "Licences and Permits", 5000),
    ("30010045", "Tricycle (Keke) Commercial Motor Cycle Regulation", "Per Annum", "Licences and Permits", 10000),
    ("30010046", "Public Toilet", "Per Annum", "Fees and Charges", 10000),
    ("30010047", "Pest Control", "Per Annum", "Fees and Charges", 15000),
    ("30010048", "Contractors", "Per Registration", "Registration and Professional Fees", 50000),
    ("30010049", "Tenement Rate Collection", "Per Annum", "Rates", 20000),
    ("30010050", "Private Sector Participation Refuse Operation (PSPRO)", "Per Annum", "Fees and Charges", 15000),
    ("30010051", "Liquor Licensing", "Per Annum", "Licences and Permits", 50000),
    ("30010052", "Wrong Parking, Corporate Parking Permit/License", "Per Annum", "Fees and Charges", 10000),
    ("30010053", "Foodstuff Regulation", "Per Month", "Fees and Charges", 10000),
    ("30010054", "Regulated Premises", "Per Annum", "Fees and Charges", 30000),
    ("30010055", "Registration Fee", "Per Registration", "Registration and Professional Fees", 5000),
    ("30010056", "Communication Mast License", "Per Annum", "Licences and Permits", 1000000),
    ("30010057", "Agreement Fees on Sale of Land and Other Disposition", "Per Transaction", "Registration and Professional Fees", 20000),
    ("30010058", "Fees for Certificate of Occupancy", "Per Certificate", "Registration and Professional Fees", 25000),
    ("30010059", "Fees for Change of Ownership", "Per Transaction", "Registration and Professional Fees", 15000),
    ("30010060", "Searches", "Per Search", "Registration and Professional Fees", 5000),
    ("30010061", "Tender Fees", "Per Tender", "Registration and Professional Fees", 10000),
    ("30010062", "Community and Development Levy", "Per Annum", "Levies", 5000),
]

# Real FCT Kuje Area Council wards.
WARDS = ["Chibiri", "Gaube", "Gudun-Karya", "Ivo", "Kabi", "Kuje", "Kwaku", "Rubochi", "Yenche"]

ROLES = [
    ("COUNCIL_ADMIN", AppRole.COUNCIL_ADMIN),
    ("HEAD_REVENUE", AppRole.COUNCIL_ADMIN),
    ("CONSULTANT_MANAGER", AppRole.CONSULTANT),
    ("FIELD_AGENT", AppRole.AGENT),
    ("STAKEHOLDER", AppRole.GLOBAL_VIEW),
]


class Command(BaseCommand):
    help = "Seed Kuje Area Council (KAC) as council #1 from the harmonised chart of revenue."

    def add_arguments(self, parser):
        parser.add_argument("--admin-username", default="admin")
        parser.add_argument("--admin-password", default=None, help="Defaults to a freshly generated random password, printed once.")

    @transaction.atomic
    def handle(self, *args, **options):
        if Council.objects.filter(council_code="KAC").exists():
            self.stdout.write(self.style.WARNING("KAC already seeded — skipping. Delete the council to reseed."))
            return

        for name in CATEGORIES:
            RevenueCategory.objects.get_or_create(name=name, defaults={"sort_order": CATEGORIES.index(name)})
        categories = {c.name: c for c in RevenueCategory.objects.all()}

        for code, name, unit, _cat, _rate in REVENUE_ITEMS:
            # update_or_create, not get_or_create: RevenueItemTemplate is
            # global (shared across councils, not council-scoped), so it
            # survives a `reset_council_data --full` even though the council
            # itself doesn't. get_or_create would silently keep serving a
            # stale unit_of_charge/item_name from any earlier seed run
            # instead of picking up a rework to REVENUE_ITEMS above — bit
            # 30010053's "Per Annum" -> "Per Month" change on the very first
            # re-seed after this file was reworked.
            RevenueItemTemplate.objects.update_or_create(
                harmonised_code=code,
                defaults={"item_name": name, "unit_of_charge": unit, "category": categories[_cat]},
            )

        for code, access_level in ROLES:
            AppRole.objects.get_or_create(name=code, defaults={"access_level": access_level})
        admin_role = AppRole.objects.get(name="COUNCIL_ADMIN")

        for code, _label in PaymentChannel.CODE_CHOICES:
            PaymentChannel.objects.get_or_create(code=code)

        council = onboard_council(
            council_code="KAC",
            council_name="Kuje Area Council",
            config={
                "bill_ref_prefix": "KAC",
                "bill_due_days": 30,
                "revenue_bank_name": "Zenith Bank",
                "revenue_bank_account_number": "1010101010",
                "revenue_bank_account_name": "Kuje Area Council Revenue Account",
                "treasurer_name": "Treasurer, Kuje Area Council",
                "treasurer_phone": "08000000000",
                "print_signatory_name": "Head of Revenue",
                "print_signatory_title": "Head of Revenue, Kuje Area Council",
            },
            actor=None,
        )

        for ward_name in WARDS:
            WardZone.objects.get_or_create(
                council=council,
                ward_code=ward_name.upper().replace(" ", "_").replace("-", "_"),
                defaults={"ward_name": ward_name, "zone_type": "WARD"},
            )

        for code, name, unit, cat_name, rate in REVENUE_ITEMS:
            template = RevenueItemTemplate.objects.get(harmonised_code=code)
            item = activate_template_item(
                council=council, template=template, rate_amount=rate, actor=None, category=categories[cat_name],
            )
            _ = item  # created for its side effects (rate_schedule row)

        admin_username = options["admin_username"]
        admin_password = options["admin_password"] or secrets.token_urlsafe(12)
        if not AppUser.objects.filter(username=admin_username).exists():
            AppUser.objects.create_user(
                username=admin_username,
                password=admin_password,
                full_name="Council Revenue Administrator",
                council=council,
                role=admin_role,
            )
            self.stdout.write(self.style.SUCCESS(f"Created admin user '{admin_username}' — password: {admin_password}"))
            self.stdout.write(self.style.WARNING("Store this password now — it is not shown again and is not a shared demo password (see TDD.md 'Should fix before any real pilot')."))

        self.stdout.write(self.style.SUCCESS(f"Seeded {council.council_code}: {len(WARDS)} wards, {len(REVENUE_ITEMS)} revenue items."))
