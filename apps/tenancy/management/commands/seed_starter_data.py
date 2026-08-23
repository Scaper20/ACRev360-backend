"""
Populates a freshly-onboarded council with exactly one consultant, one field
agent (assigned under that consultant, never council-direct — see the Tier 2
"remove council-direct agents" change), and ten payers, each deliberately
different from the rest (payer type, ward, business size, KYC status, who
enumerated them, whether they have an email on file, whether anything's been
enumerated against them yet) rather than randomly generated like
seed_demo_data.py's 100. No bills/payments/settlements — just clean starter
accounts and payer variety to click through.

Run after seed_kuje and seed_rate_bands.
"""
import hashlib

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.registry.models import Payer
from apps.registry.services import create_payer
from apps.revenue.models import CouncilRevenueItem
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, WardZone

DEMO_PASSWORD = "acrev360-2026"


class Command(BaseCommand):
    help = "Seed one consultant, one agent (under that consultant), and ten varied payers."

    def add_arguments(self, parser):
        parser.add_argument("--council-code", default="KAC")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            council = Council.objects.get(council_code=options["council_code"])
        except Council.DoesNotExist:
            raise CommandError(f"No council with code {options['council_code']!r} — run seed_kuje first.")
        set_council_context(council.id)

        admin = AppUser.objects.filter(council=council, role__access_level=AppRole.COUNCIL_ADMIN).first()
        if admin is None:
            raise CommandError("No council admin user found — run seed_kuje first.")

        if Payer.objects.filter(council=council).exists() or SubConsultant.objects.filter(council=council).exists():
            raise CommandError(f"{council.council_code} already has payers/consultants — reset first (reset_council_data).")

        wards = {w.ward_code: w for w in WardZone.objects.filter(council=council)}

        consultant = SubConsultant.objects.create(
            council=council, consultant_name="Heritage Fiscal Partners", contract_ref=f"{council.council_code}/RC/2026/001",
            commission_rate="30.00", status=SubConsultant.ACTIVE,
        )
        consultant_role, _ = AppRole.objects.get_or_create(name="CONSULTANT_MANAGER", defaults={"access_level": AppRole.CONSULTANT})
        consultant_user = AppUser.objects.create_user(
            username="consultant1", password=DEMO_PASSWORD, full_name=f"Manager, {consultant.consultant_name}",
            council=council, role=consultant_role, consultant=consultant,
        )
        self.stdout.write(self.style.SUCCESS(f"Consultant: {consultant.consultant_name} ({consultant.contract_ref}) — login consultant1"))

        agent_role, _ = AppRole.objects.get_or_create(name="FIELD_AGENT", defaults={"access_level": AppRole.AGENT})
        agent_user = AppUser.objects.create_user(
            username="agent01", password=DEMO_PASSWORD, full_name="Amina Bello", phone="08031234567",
            council=council, role=agent_role, consultant=consultant,
        )
        agent = FieldAgent.objects.create(
            council=council, user=agent_user, agent_code="AGT-00001",
            assigned_ward=wards.get("KUJE"), status=FieldAgent.ACTIVE,
        )
        self.stdout.write(self.style.SUCCESS(f"Field agent: {agent_user.full_name} ({agent.agent_code}, under {consultant.consultant_name}) — login agent01"))

        items = {i.harmonised_code: i for i in CouncilRevenueItem.objects.filter(council=council, is_active=True)}

        def item(code):
            found = items.get(code)
            return [found] if found else []

        def nin(seed):
            return hashlib.sha256(seed.encode()).hexdigest()

        # (payer_type, full_name, phone, email, ward_code, business_size, tin,
        #  nin_bvn_hash, kyc_status, enumerator, revenue_item_ids)
        specs = [
            (Payer.INDIVIDUAL, "Ngozi Adeyemi", "08021110001", "ngozi.adeyemi@example.com", "KUJE", None, "", nin("ngozi"), Payer.VERIFIED, admin, []),
            (Payer.INDIVIDUAL, "Suleiman Garba", "08021110002", "", "CHIBIRI", None, "", nin("suleiman"), Payer.PENDING, admin, item("30010031")),
            (Payer.INDIVIDUAL, "Folake Bello", "08021110003", "folake.bello@example.com", "GAUBE", None, "", nin("folake"), Payer.VERIFIED, consultant_user, item("30010040") + item("30010060")),
            (Payer.BUSINESS, "Adaeze Micro Ventures", "08021110004", "info@adaezeventures.example.com", "KUJE", Payer.MICRO, "TIN-000104", "", Payer.PENDING, admin, []),
            (Payer.BUSINESS, "Bello & Sons Trading Co", "08021110005", "", "GUDUN_KARYA", Payer.SMALL, "TIN-000105", "", Payer.VERIFIED, consultant_user, item("30010039")),
            (Payer.BUSINESS, "Ivo Medium Enterprises", "08021110006", "contact@ivomedium.example.com", "IVO", Payer.MEDIUM, "", "", Payer.FLAGGED, admin, []),
            (Payer.BUSINESS, "Kabi Global Concepts Ltd", "08021110007", "admin@kabiglobal.example.com", "KABI", Payer.LARGE, "TIN-000107", "", Payer.VERIFIED, admin, item("30010042") + item("30010046")),
            (Payer.INDIVIDUAL, "Chinwe Okonkwo", "08021110008", "", "KWAKU", None, "", nin("chinwe"), Payer.PENDING, consultant_user, []),
            (Payer.BUSINESS, "Rubochi Stores", "08021110009", "rubochistores@example.com", "RUBOCHI", Payer.SMALL, "TIN-000109", "", Payer.VERIFIED, admin, item("30010041")),
            (Payer.INDIVIDUAL, "Temitope Alabi", "08021110010", "temitope.alabi@example.com", "YENCHE", None, "", nin("temitope"), Payer.PENDING, admin, item("30010047")),
        ]

        created = []
        for payer_type, name, phone, email, ward_code, biz_size, tin, nin_hash, kyc, enumerator, rev_items in specs:
            ward = wards.get(ward_code)
            if ward is None:
                self.stdout.write(self.style.WARNING(f"Ward {ward_code} not found — skipping {name}"))
                continue
            payer, draft_count = create_payer(
                council_id=council.id, actor=enumerator, enumerated_by=enumerator,
                payer_type=payer_type, full_name=name, phone=phone, email=email, address=f"{ward.ward_name} Road",
                ward=ward, business_size=biz_size, tin=tin, nin_bvn_hash=nin_hash, kyc_status=kyc,
                revenue_item_ids=rev_items,
            )
            created.append(payer)
            self.stdout.write(f"  {payer.payer_ref} — {payer.full_name} ({payer.payer_type}, {ward.ward_name}, KYC {kyc}, {draft_count} item(s))")

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(created)} payer(s) for {council.council_code}."))
