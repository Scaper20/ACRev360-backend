"""
Data migration and seeding command for Kuje Area Council (KAC).
Workflow:
1. Backup existing 'department' and 'council_revenue_item' (along with rate schedules, bands, tiers) to JSON.
2. Clear current entries in these tables safely within a database transaction.
3. Seed the 8 departments extracted from KUJE DEPARTMENT.docx.
4. Seed/activate all revenue items, linking each to its parent department.
5. Apply gazette bye-law rate schedules and hierarchical rate bands/tiers (RANGE, TIERED, FLAT).
"""

import json
import os
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.revenue.models import (
    AgentPortfolio,
    ConsultantPortfolio,
    CouncilRevenueItem,
    RateBand,
    RateSchedule,
    RateTier,
    RevenueCategory,
    RevenueItemTemplate,
)
from apps.revenue.services import replace_rate_bands
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, Department
from apps.tenancy.services import activate_template_item

# 8 Departments extracted from KUJE DEPARTMENT.docx
DEPARTMENTS = [
    ("Department of Works, Lands, Housing and Engineering", "WORKS", "Engr. Head of Works", "08000000001"),
    ("Department of Environmental", "ENV", "Head of Environmental Sanitation", "08000000002"),
    ("Department of Finance and Supplies", "FIN", "Council Treasurer", "08000000003"),
    ("Department of Administration and General Services", "ADMIN", "Head of Administration", "08000000004"),
    ("Medical and Health Care Department", "HEALTH", "Head of Medical Services", "08000000005"),
    ("Department of Education, Social, Information, Sports and Culture", "EDU", "Head of Social Development", "08000000006"),
    ("Agricultural and Natural Resources Department", "AGRIC", "Head of Agricultural Services", "08000000007"),
    ("Audit Department", "AUDIT", "Head of Internal Audit", "08000000008"),
]

CATEGORIES = [
    "Rates",
    "Licences and Permits",
    "Fees and Charges",
    "Registration and Professional Fees",
    "Levies",
]

# Mapping of revenue items to departments, category, unit, default rate
# (harmonised_code, item_name, unit_of_charge, category_name, dept_code, rate, byelaw_ref)
REVENUE_ITEM_DEPT_MAPPING = [
    # Works, Lands, Housing and Engineering
    ("30010049", "Tenement Rate Collection", "Per Annum", "Rates", "WORKS", 20000, "Part XXI"),
    ("30010035", "Stacking of Building Material / Construction Permit", "Per Permit", "Licences and Permits", "WORKS", 25000, "Part VIII"),
    ("30010038", "Cutting Of Road Tar", "Per Permit", "Fees and Charges", "WORKS", 30000, "Part X"),
    ("30010040", "Numbering / Street Naming", "Per Annum", "Registration and Professional Fees", "WORKS", 5000, "Part XII"),
    ("30010052", "Wrong Parking, Corporate Parking Permit/License", "Per Annum", "Fees and Charges", "WORKS", 10000, "Part XXIV"),

    # Environmental
    ("30010033", "Environmental Sanitation and Premise Inspection", "Per Annum", "Fees and Charges", "ENV", 10000, "Part V"),
    ("30010046", "Public Toilet", "Per Annum", "Fees and Charges", "ENV", 10000, "Part XVIII"),
    ("30010047", "Pest Control", "Per Annum", "Fees and Charges", "ENV", 15000, "Part XIX"),
    ("30010050", "Private Sector Participation Refuse Operation (PSPRO)", "Per Annum", "Fees and Charges", "ENV", 15000, "Part XXII"),

    # Finance and Supplies
    ("30010043", "Trade License, Private Lockup Shops and Allied Matters", "Per Annum", "Licences and Permits", "FIN", 20000, "Part XV"),
    ("30010048", "Contractors", "Per Registration", "Registration and Professional Fees", "FIN", 50000, "Part XX"),
    ("30010061", "Tender Fees", "Per Tender", "Registration and Professional Fees", "FIN", 10000, "Part XX"),
    ("30010057", "Agreement Fees on Sale of Land and Other Disposition", "Per Transaction", "Registration and Professional Fees", "FIN", 20000, "Part XX"),
    ("30010058", "Fees for Certificate of Occupancy", "Per Certificate", "Registration and Professional Fees", "FIN", 25000, "Part XX"),
    ("30010059", "Fees for Change of Ownership", "Per Transaction", "Registration and Professional Fees", "FIN", 15000, "Part XX"),
    ("30010060", "Searches", "Per Search", "Registration and Professional Fees", "FIN", 5000, "Part XX"),
    ("30010062", "Community and Development Levy", "Per Annum", "Levies", "FIN", 5000, "Part XXV"),

    # Administration and General Services
    ("30010031", "Registration of Marriages, Births and Death", "Per Registration", "Registration and Professional Fees", "ADMIN", 5000, "Part III"),
    ("30010044", "Radio and Television License", "Per Annum", "Licences and Permits", "ADMIN", 5000, "Part XVI"),
    ("30010036", "Mobile Advert", "Per Annum", "Licences and Permits", "ADMIN", 15000, "Part VII"),
    ("30010051", "Liquor Licensing", "Per Annum", "Licences and Permits", "ADMIN", 50000, "Part XXIII"),

    # Medical and Health Care Department
    ("30010054", "Regulated Premises", "Per Annum", "Fees and Charges", "HEALTH", 30000, "Part XXVI"),
    ("30010053", "Foodstuff Regulation", "Per Month", "Fees and Charges", "HEALTH", 10000, "Part XXIV"),
    ("30010055", "Registration Fee", "Per Registration", "Registration and Professional Fees", "HEALTH", 5000, "Part XIX"),

    # Education, Social, Information, Sports and Culture
    ("30010034", "Control of Advertisement", "Per Annum", "Licences and Permits", "EDU", 20000, "Part VI"),
    ("30010045", "Tricycle (Keke) Commercial Motor Cycle Regulation", "Per Annum", "Licences and Permits", "EDU", 10000, "Part XV"),
    ("30010056", "Communication Mast License", "Per Annum", "Licences and Permits", "EDU", 1000000, "Part XVI"),

    # Agricultural and Natural Resources Department
    ("30010039", "Movement and Keeping of Dogs", "Per Annum", "Fees and Charges", "AGRIC", 5000, "Part II (Sec 2)"),
    ("30010032", "Motor Parks (Commercial Vehicles picking up passengers)", "Per Annum", "Fees and Charges", "AGRIC", 15000, "Part II (Sec 2)"),
    ("30010037", "Loading/Off Loading Control of Traffic", "Per Annum", "Fees and Charges", "AGRIC", 10000, "Part II (Sec 2)"),
]

# Gazette rate bands definitions
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

BUILDING_MATERIALS_BANDS = [
    ("Paint Depot", 50000, 100000),
    ("Cement", 10000, 20000),
    ("Cement (Warehouse)", 50000, 70000),
    ("Iron", 50000, 70000),
    ("Rod/Pipe", 70000, 100000),
    ("Paint Shop", 10000, 20000),
    ("POP Cement", 20000, 50000),
    ("Aluminum Profile", 100000, 250000),
    ("Roofing Sheet", 50000, 100000),
    ("Plumbing Material", 30000, 50000),
    ("Tiles", 50000, 150000),
    ("Iron Gate/Doors", 70000, 200000),
    ("Electrical Material (Cable, Transformer, Poles & Pipe)", 150000, 500000),
    ("Ceiling Material", 40000, 70000),
    ("Gravel Site", 150000, 250000),
    ("Sand Seller", 50000, 150000),
]

TRADE_LICENSE_BANDS = [
    ("Clinic/Private Hospitals", 10000, 45000),
    ("Patent Medicine Dealers", 5000, 15000),
    ("Pharmacy", 15000, 80000),
    ("Blacksmith", 2000, 10000),
    ("Goldsmith", 2000, 10000),
    ("POS", 10000, 25000),
    ("Printing Press", 10000, 100000),
    ("Dentist Shop", 10000, 50000),
    ("Herbal Shop", 5000, 20000),
    ("Optical Shop", 5000, 20000),
    ("Mechanic Workshop", 5000, 25000),
    ("Vulcanizing Shop", 1000, 2000),
    ("Watch Repairing Shop", 2000, 5000),
    ("Carwash", 5000, 20000),
    ("Grinding Machine", 5000, 15000),
    ("Welding Shop", 5000, 20000),
    ("Electrical Appliances Shop", 5000, 20000),
    ("Electronic Workshop", 5000, 20000),
    ("Pool/Bet Shop", 10000, 30000),
    ("Panel Beater Workshop", 5000, 20000),
    ("Spare Parts (Motor)", 15000, 70000),
    ("Spare Parts (Try-cycle)", 5000, 20000),
    ("Spare Parts (Motor Cycle)", 5000, 10000),
    ("Spare Parts (Bicycle)", 2000, 5000),
    ("Airline Ticketing Office", 50000, 150000),
    ("Media Houses (Print)", 200000, 300000),
    ("Departmental Store", 150000, 500000),
    ("Provision Store", 2000, 10000),
    ("Super Store", 50000, 100000),
    ("Super Market", 20000, 70000),
    ("Telephone Accessories Shop", 2000, 5000),
    ("Car Stand", 50000, 100000),
    ("Beauty Shop", 5000, 10000),
    ("Cosmetic Shop", 5000, 20000),
    ("Plastic Product Shop", 5000, 20000),
    ("Bookshop and Stationaries", 5000, 30000),
    ("Electrical Shop", 10000, 30000),
    ("Electronic Shop", 15000, 35000),
    ("Gas Refilling Shop", 5000, 10000),
    ("Coffee Shop", 5000, 10000),
    ("Bicycle Shop", 10000, 20000),
    ("Tricycle Shop", 20000, 50000),
    ("Motor Cycle Shop", 15000, 30000),
    ("Kiosk", 2000, 5000),
    ("Block Moulding Stand", 10000, 50000),
    ("Furniture/Carpentry Shop", 5000, 10000),
    ("Hair Dressing Salon", 3000, 10000),
    ("Barbing Salon Shop", 3000, 10000),
    ("Cinema Houses", 20000, 50000),
    ("Viewing Centres", 5000, 10000),
    ("Rentals", 5000, 10000),
    ("Photographic Studio", 2000, 5000),
    ("Business Centre/Cyber Cafe", 5000, 10000),
    ("Curtain Material Shop", 10000, 30000),
    ("Boutique Shop", 5000, 15000),
    ("Agro-Chemical Shop", 10000, 30000),
    ("Industrial Chemical Shop", 15000, 30000),
    ("Tools/Equipment Shop", 50000, 150000),
    ("Tailoring/Fashion Design Shop", 5000, 15000),
    ("Shoe Maker Shop", 5000, 20000),
    ("Spray/Painter Workshop", 5000, 20000),
    ("Mobile Phone Shop", 20000, 50000),
    ("Corporate Offices", 10000, 20000),
    ("Foam Shop", 10000, 20000),
    ("Foam Depot", 50000, 150000),
    ("Arts and Crafts Shop", 3000, 5000),
    ("Kerosene Shop", 3000, 5000),
    ("Soap and Detergent Depot", 20000, 50000),
    ("Tobacco Distribution Shop", 20000, 70000),
    ("Commercial Banks", 250000, 300000),
    ("Micro Finance Banks", 50000, 100000),
    ("Insurance Company", 50000, 100000),
    ("Bureau de Change", 100000, 200000),
    ("Mortgage Banks", 100000, 200000),
    ("Construction Company (Multi-National)", 500000, 1000000),
    ("Construction Company (Local)", 200000, 509000.13),
    ("Petrol Station", 100000, 150000),
    ("Quarries", 500000, 750000),
    ("Power Distribution Companies (DISCO's)", 1000000, 1500000),
    ("Telecommunication Company", 200000, 500000),
    ("Furniture Showroom", 200000, 500000),
    ("Warehouse (Exception of Premises and Building Materials Regulated)", 150000, 500000),
    ("Electric/Electronic Equipment Installation Company", 150000, 500000),
    ("Manufacturing Company", 150000, 450000),
]

CONTRACTORS_TIERED = [
    ("Construction", [
        ("₦1,000,000 – ₦5,000,000", 20000),
        ("₦5,100,000 – ₦50,000,000", 35000),
        ("₦50,100,000 – ₦99,000,000", 150000),
        ("₦100,000,000 and Above", 300000),
    ]),
    ("Supply", [
        ("₦100,000 – ₦1,000,000", 20000),
        ("₦1,100,000 – ₦10,000,000", 30000),
        ("₦11,000,000 – ₦50,000,000", 100000),
        ("₦51,000,000 and Above", 250000),
    ]),
]
CONTRACTORS_FLAT = [
    ("Services", 24000),
    ("Consultancy", 120000),
]

WRONG_PARKING_CORPORATE_BANDS = [
    ("Lorries/Tippers", 250000, 500000),
    ("Car/Buses/Vans/Pick-up", 500000, 1000000),
    ("Dyna Delivery Van/J5", 150000, 250000),
]

MOBILE_ADVERT_FLAT = [
    ("Industrial Motorcycle", 4500),
    ("Car/Buses/vans/pickups", 17000),
    ("Dyna Delivery Vans/J5", 25000),
    ("Tipp er\\Lonies", 30000),
    ("Trailers", 35000),
    ("Cranes", 45000),
    ("Earth moving equipment", 45000),
]

LOADING_OFFLOADING_FLAT = [
    ("Lorries/Tippers", 15000),
    ("Car/Buses /Vans/Pick-ups", 5000),
    ("Dyna Delivery Van/J5", 10000),
    ("Luxurious Buses", 20000),
    ("Trailers", 20000),
    ("Cranes", 25000),
    ("Earth-Moving Equipment", 25000),
]

REGULATED_PREMISES_RANGES = [
    ("HOTELS", 100000, 160000, 40000, 70000, 10000, 30000),
    ("GUEST INN", 20000, 30000, 15000, 20000, 5000, 10000),
    ("RESTAURANT", 30000, 70000, 10000, 30000, 5000, 10000),
    ("CANTEEN", 5000, 10000, 3000, 7000, 2000, 3000),
    ("JOINTS/BAR", 20000, 50000, 15000, 30000, 3000, 10000),
    ("BAKERY", 50000, 100000, 30000, 50000, 10000, 25000),
    ("OTHER CONF.", 15000, 20000, 5000, 10000, 1000, 5000),
    ("YOGHURT & OTHER DAIRIES FOOD", 100000, 150000, 50000, 70000, 10000, 50000),
    ("PORTABLE WATER FACTORY", 120000, 250000, 100000, 150000, 50000, 100000),
    ("FOOD PRESERVING ESTABLISHMENT", 70000, 100000, 15000, 60000, 5000, 10000),
    ("FOOD RELATED WAREHOUSE", 50000, 80000, 20000, 50000, 5000, 15000),
    ("FOOD HAWERS", 3000, 5000, 2000, 3000, 1000, 2000),
    ("FOOD RELATEDESTABLISHEMENT", 20000, 40000, 10000, 30000, 2000, 10000),
    ("OTHER", 5000, 10000, 3000, 5000, 500, 1500),
]


class Command(BaseCommand):
    help = "Backup, clear, and seed departments and revenue items with gazette rate bands for Kuje Area Council."

    def add_arguments(self, parser):
        parser.add_argument("--council-code", default="KAC", help="Council code (default: KAC)")

    @transaction.atomic
    def handle(self, *args, **options):
        council_code = options["council_code"]
        try:
            council = Council.objects.get(council_code=council_code)
        except Council.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Council with code {council_code} does not exist."))
            return

        set_council_context(council.id)
        self.stdout.write(self.style.NOTICE(f"=== Starting Migration & Seeding for {council.council_name} ({council_code}) ==="))

        # Step 1: Backup
        self.backup_data(council)

        # Step 2: Clear current entries
        self.clear_existing(council)

        # Step 3: Seed Departments
        dept_map = self.seed_departments(council)

        # Step 4: Seed & Map Revenue Items
        items_map = self.seed_revenue_items(council, dept_map)

        # Step 5: Seed Gazette Rate Bands
        self.seed_rate_bands(council, items_map)

        self.stdout.write(self.style.SUCCESS(f"=== Successfully completed migration and seeding for {council_code}! ==="))

    def backup_data(self, council):
        os.makedirs("docs/backups", exist_ok=True)
        backup_file = "docs/backups/departments_revenue_backup.json"

        dept_qs = Department.objects.filter(council=council)
        items_qs = CouncilRevenueItem.objects.filter(council=council)
        schedules_qs = RateSchedule.objects.filter(council_revenue_item__council=council)
        bands_qs = RateBand.objects.filter(council_revenue_item__council=council)
        tiers_qs = RateTier.objects.filter(band__council_revenue_item__council=council)

        data = {
            "departments": list(dept_qs.values("id", "department_name", "department_code", "head_name", "head_phone")),
            "revenue_items": list(items_qs.values("id", "harmonised_code", "item_name", "category_id", "unit_of_charge", "department_id")),
            "rate_schedules": list(schedules_qs.values("id", "council_revenue_item_id", "rate_amount", "effective_from", "effective_to")),
            "rate_bands": list(bands_qs.values("id", "council_revenue_item_id", "label", "rate_mode", "flat_amount", "min_amount", "max_amount")),
            "rate_tiers": list(tiers_qs.values("id", "band_id", "label", "amount")),
        }

        # Convert Decimal and Date to str
        def json_default(obj):
            if isinstance(obj, (Decimal, date)):
                return str(obj)
            raise TypeError(f"Type {type(obj)} not serializable")

        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=json_default)

        self.stdout.write(self.style.SUCCESS(f"[1/5] Backup created at: {backup_file} ({len(data['departments'])} depts, {len(data['revenue_items'])} items)"))

    def clear_existing(self, council):
        import traceback
        from apps.accounts.models import AppRole, AppUser, SubConsultant
        from apps.audit.models import AuditLog
        from apps.billing.models import Assessment, Bill, BillLine
        from apps.fieldops.models import MobileSyncRecord
        from apps.payments.models import APIClient, ChannelTransactionFeed, Payment, POSTerminal
        from apps.reconciliation.models import ReconciliationException, ReconciliationRun
        from apps.registry.models import EnumeratedAsset, Payer
        from apps.settlements.models import CommissionSettlement

        try:
            # 1. Nullify cyclic FK references to break delete cycles
            AppUser.objects.filter(council=council).update(consultant=None)
            SubConsultant.objects.filter(council=council).update(registration_payer=None)
            Payer.objects.filter(council=council).update(is_duplicate_of=None)

            # 2. Clear Portfolios and transactional records
            ConsultantPortfolio.objects.filter(council=council).delete()
            AgentPortfolio.objects.filter(council=council).delete()

            AuditLog.objects.filter(council=council).delete()
            ReconciliationException.objects.filter(council=council).delete()
            ReconciliationRun.objects.filter(council=council).delete()
            ChannelTransactionFeed.objects.filter(council=council).delete()
            MobileSyncRecord.objects.filter(council=council).delete()

            Payment.objects.filter(council=council).delete()
            BillLine.objects.filter(bill__council=council).delete()
            Bill.objects.filter(council=council).delete()
            Assessment.objects.filter(council=council).delete()

            CommissionSettlement.objects.filter(council=council).delete()
            POSTerminal.objects.filter(council=council).delete()
            APIClient.objects.filter(council=council).delete()
            
            SubConsultant.objects.filter(council=council).delete()
            EnumeratedAsset.objects.filter(payer__council=council).delete()
            Payer.objects.filter(council=council).delete()

            AppUser.objects.filter(
                council=council,
                role__access_level__in=[AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW],
            ).delete()

            # 2. Clear RateSchedule, RateTier, RateBand, CouncilRevenueItem, Department
            RateSchedule.objects.filter(council_revenue_item__council=council).delete()
            RateTier.objects.filter(band__council_revenue_item__council=council).delete()
            RateBand.objects.filter(council_revenue_item__council=council).delete()

            deleted_items, _ = CouncilRevenueItem.objects.filter(council=council).delete()
            deleted_depts, _ = Department.objects.filter(council=council).delete()

            self.stdout.write(self.style.SUCCESS(f"[2/5] Cleared existing entries: {deleted_items} revenue items and {deleted_depts} departments removed."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error in clear_existing: {e}"))
            traceback.print_exc()
            raise e

    def seed_departments(self, council):
        dept_map = {}
        for name, code, head, phone in DEPARTMENTS:
            dept, created = Department.objects.get_or_create(
                council=council,
                department_name=name,
                defaults={
                    "department_code": code,
                    "head_name": head,
                    "head_phone": phone,
                },
            )
            dept_map[code] = dept
            status = "Created" if created else "Retained"
            self.stdout.write(f"  - Department [{code}]: {name} ({status})")

        self.stdout.write(self.style.SUCCESS(f"[3/5] Seeded {len(dept_map)} departments."))
        return dept_map

    def seed_revenue_items(self, council, dept_map):
        for name in CATEGORIES:
            RevenueCategory.objects.get_or_create(name=name, defaults={"sort_order": CATEGORIES.index(name)})
        categories = {c.name: c for c in RevenueCategory.objects.all()}

        items_map = {}
        for code, name, unit, cat_name, dept_code, rate, byelaw in REVENUE_ITEM_DEPT_MAPPING:
            category = categories[cat_name]
            dept = dept_map.get(dept_code)

            # Update or create template
            template, _ = RevenueItemTemplate.objects.update_or_create(
                harmonised_code=code,
                defaults={"item_name": name, "unit_of_charge": unit, "category": category},
            )

            # Activate template item for council
            item = activate_template_item(
                council=council,
                template=template,
                rate_amount=rate,
                actor=None,
                category=category,
            )

            # Bind department FK
            item.department = dept
            item.save(update_fields=["department"])
            items_map[code] = item

            self.stdout.write(f"  - Item [{code}] {name} -> Dept: {dept.department_code} ({byelaw})")

        self.stdout.write(self.style.SUCCESS(f"[4/5] Seeded & mapped {len(items_map)} revenue items to departments."))
        return items_map

    def seed_rate_bands(self, council, items_map):
        def _range(label, min_amount, max_amount):
            return {"label": label, "rate_mode": "RANGE", "min_amount": min_amount, "max_amount": max_amount}

        def _tiered(label, tiers):
            return {"label": label, "rate_mode": "TIERED", "tiers": [{"label": t, "amount": a} for t, a in tiers]}

        def _flat(label, amount):
            return {"label": label, "rate_mode": "FLAT", "flat_amount": amount}

        # Apply bands
        if "30010034" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010034"], bands=[_range(lbl, mn, mx) for lbl, mn, mx in CONTROL_OF_ADVERTISEMENT_BANDS], actor=None)

        if "30010051" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010051"], bands=[
                _tiered(lbl, [("Large", l), ("Medium", m), ("Small", s)])
                for lbl, l, m, s in LIQUOR_LICENSING_BANDS
            ], actor=None)

        if "30010056" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010056"], bands=[_tiered("", COMMUNICATION_MAST_TIERS)], actor=None)

        if "30010035" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010035"], bands=[_range(lbl, mn, mx) for lbl, mn, mx in BUILDING_MATERIALS_BANDS], actor=None)

        if "30010043" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010043"], bands=[_range(lbl, mn, mx) for lbl, mn, mx in TRADE_LICENSE_BANDS], actor=None)

        if "30010048" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010048"], bands=[
                _tiered(lbl, tiers) for lbl, tiers in CONTRACTORS_TIERED
            ] + [_flat(lbl, amt) for lbl, amt in CONTRACTORS_FLAT], actor=None)

        if "30010052" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010052"], bands=[_range(lbl, mn, mx) for lbl, mn, mx in WRONG_PARKING_CORPORATE_BANDS], actor=None)

        if "30010036" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010036"], bands=[_flat(lbl, amt) for lbl, amt in MOBILE_ADVERT_FLAT], actor=None)

        if "30010037" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010037"], bands=[_flat(lbl, amt) for lbl, amt in LOADING_OFFLOADING_FLAT], actor=None)

        if "30010054" in items_map:
            replace_rate_bands(council_revenue_item=items_map["30010054"], bands=[
                band
                for est, l_mn, l_mx, m_mn, m_mx, s_mn, s_mx in REGULATED_PREMISES_RANGES
                for band in (
                    _range(f"{est} — Large", l_mn, l_mx),
                    _range(f"{est} — Medium", m_mn, m_mx),
                    _range(f"{est} — Small", s_mn, s_mx),
                )
            ], actor=None)

        self.stdout.write(self.style.SUCCESS("[5/5] Seeded gazette rate bands and classifications."))
