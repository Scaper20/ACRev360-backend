"""
Seeds Kuje Area Council's departmental structure and its full revenue-item
catalogue, with each item carrying the bye-law it is charged under and the
gazetted bands/classifications underneath it.

Sources
-------
`docs/KUJE DEPARTMENT.docx` is authoritative for *which* departments exist and
*which* items each one collects, and supplies every bye-law citation ("Part
XXI", "Part VIII (Part B)", ...) plus the council's own description of what the
provision authorises. Both are read straight off the document's departmental
tables; nothing here is inferred.

`docs/reference/Kuje Revenue Item and Code List.xlsx` supplies the harmonised
codes for the 25 items that appear in the FCT-wide catalogue.

Most bands and classifications are imported from
`apps.revenue.management.commands.seed_rate_bands` rather than re-transcribed —
that module is the single reviewed transcription of the XLSX schedules, and
copying its figures into a second file would only create somewhere for the two
to drift apart. See its docstring for per-schedule provenance.

`docs/KAC NEW GAzETTE (4).pdf` — the actual legal instrument — is a 144-page
scan with no text layer, so `pypdf` extracts zero characters and it cannot be
parsed as text. It is not unreadable, though: every page is a single embedded
200 DPI JPEG, extracted losslessly and read page by page. Schedules recovered
that way live in `apps.revenue.gazette_kac`, each carrying its gazette page.

Reading the gazette directly corrected the secondary sources in four ways:
  - Foodstuff Regulation's Part citation (the .docx says Part XXIV, but Part
    XXIV is Wrong Parking; the foodstuff monthly rates are Part XXVI's Second
    Schedule, gazette B317-B318).
  - Six items the .docx leaves unattributed do each have a Part of their own.
  - Tender Fees' four figures, which `seed_rate_bands` refused to guess at
    because the XLSX copy was corrupt, are clean in the gazette (B276).
  - Schedule "C" has 26 establishment rows; the XLSX transcription has 22.

Every citation here was read off the relevant Part's BODY header, never off the
gazette's own table of contents — the TOC at B168 mislabels Part XXV as
"Foodstuff Regulations" when the Part itself (B301) is headed "Community
Development Levy and Allied Matters". The .docx was right about that one and an
earlier TOC-based reading of it was wrong.

Reconciling the two documents
-----------------------------
The .docx lists 42 departmental rows, but `uniq_item_code_per_council` plus a
single `department` FK means one row per item with one owning department. Four
collapses, each resolved on the document's own wording rather than by picking a
side:

- Veterinary Services Fees is listed under both Medical (§2.5) and Agriculture
  (§2.7); §2.5 itself says "though under Agric Dept, health oversight is from
  Medical" -> Agriculture owns it, Medical's oversight noted in the description.
- Street Naming/Numbering is listed under both Works (§2.1) and Education
  (§2.6); §2.6 says "also linked to Works, but Social Development may process
  applications" -> Works owns it.
- "Mobile Advertisement Permit" and "Regulation of Mobile Advertisement" are two
  rows in the same department under the same Part VII with the same description
  ("Fees for branded vehicles and operational vehicles") -> one item.
- Medical's "Pest Control Certificates" and Environmental's "Fumigation
  Certificates" are both Part XIX and both described as fumigation certificates
  required before food-premises registration -> one item, owned by
  Environmental, whose Part XIX registers the firms that issue them.

Where the harmonised catalogue's own name already merges two .docx rows — 30010035
"Stacking of Building Material/Construction Permit" over §2.1's separate stacking
and construction-site rows, 30010052 "Wrong Parking, Corporate Parking Permit/
License" over §2.1's separate permit and impoundment rows, 30010033
"Environmental Sanitation and Premise inspection" over §2.2's sanitation and
inspection-fee rows — the catalogue code is used once and both provisions are
cited on it, rather than minting a second code for a charge the catalogue
already covers.

Ten items the .docx names have no harmonised code (Forestry, Fishery,
Cooperatives, Trade Site Allocation, Certificate of Fitness for Habitation and
so on). They are seeded as council-local items — `template` null, `KJ`-prefixed
code — which is exactly the case `CouncilRevenueItem.template`'s null branch
exists for.

Six codes run the other way: they are in the harmonised catalogue but the .docx
never assigns them to a department (30010032 Motor Parks, 30010037 Loading/Off
Loading, 30010039 Dogs, 30010041 Dry Cleaning, 30010042 Market Regulation,
30010045 Tricycle/Keke). They are seeded with `department` NULL — the FK is
nullable for this — so their codes and, for Loading/Off Loading, its seven
gazetted bands survive, without inventing a departmental home the source
document does not support. They need a department assigned before they can be
billed against.

Audit Department is seeded with no revenue items: §2.8 lists compliance audit,
risk assessment and regulatory compliance as its functions and states it "does
not directly collect revenue".

Known-incomplete rows
---------------------
Certificate of Fitness for Habitation now carries its full Schedule "C" (78
bands). Its ₦100,000,000 multinational figure had been withheld by an earlier
pass as unconfirmed; gazette B219 states it verbatim, so it is sourced rather
than assumed — though a figure of that size still deserves the council's eye.

Fumigation Certificates keeps a placeholder rate. Part XIX (B273-B274) gazettes
only the ₦100,000 pest-control *firm* registration and prices no separate
certificate, so there is no gazette figure to seed and none was invented.

Tenement Rate Collection has no bands, and that is correct rather than missing:
Part XXI (B277-B284) contains no fee schedule at all — the rate is a percentage
of assessed annual value, which the flat/range/tiered band model cannot express.
This finally explains the long-standing "no bye-law unambiguously named this"
note in `seed_rate_bands`.

Safety
------
`department` and `council_revenue_item` are both under FORCE ROW LEVEL SECURITY,
so every statement runs inside `council_context`. Without it Postgres filters
each DELETE to zero rows and the clear would silently do nothing while
reporting success.

The clear is scoped to exactly the two named tables plus the rows that are
FK-dependent on a revenue item and cannot outlive it (`rate_schedule`,
`rate_band`, `rate_tier`). Payers, bills, assessments, payments, users and
consultant/agent portfolios are left alone; this command refuses to run if any
assessment or portfolio row still points at an item it would delete, rather than
widening the blast radius to get past the PROTECT.
"""

from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.revenue.management.commands.seed_rate_bands import (
    BUILDING_MATERIALS_BANDS,
    COMMUNICATION_MAST_TIERS,
    COMMUNITY_LEVY_FLAT,
    COMMUNITY_LEVY_TIERED,
    CONTRACTORS_FLAT,
    CONTRACTORS_TIERED,
    CONTROL_OF_ADVERTISEMENT_BANDS,
    FOODSTUFF_REGULATION_FLAT,
    LIQUOR_LICENSING_BANDS,
    LOADING_OFFLOADING_FLAT,
    MOBILE_ADVERT_FLAT,
    REGULATED_PREMISES_RANGES,
    TRADE_LICENSE_BANDS,
    WRONG_PARKING_CORPORATE_BANDS,
)
from apps.revenue.gazette_kac import TENDER_FEES_FLAT, certificate_of_fitness_bands
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
from apps.tenancy.context import council_context
from apps.tenancy.models import Council, Department

# (code, name, legal_basis, head_name, head_phone) — KUJE DEPARTMENT.docx §2.1-2.8
DEPARTMENTS = [
    ("WORKS", "Department of Works, Lands, Housing and Engineering", "Part II, Section 6", "Engr. Head of Works", "08000000001"),
    ("ENV", "Department of Environmental", "Part II, Section 5(iii)", "Head of Environmental Sanitation", "08000000002"),
    ("FIN", "Department of Finance and Supplies", "Part II, Section 4", "Council Treasurer", "08000000003"),
    ("ADMIN", "Department of Administration and General Services", "Part II, Section 3", "Head of Administration", "08000000004"),
    ("HEALTH", "Medical and Health Care Department", "Part II, Section 5", "Head of Medical Services", "08000000005"),
    ("EDU", "Department of Education, Social, Information, Sports and Culture", "Part II, Section 7", "Head of Social Development", "08000000006"),
    ("AGRIC", "Agricultural and Natural Resources Department", "Part II, Section 2", "Head of Agricultural Services", "08000000007"),
    ("AUDIT", "Audit Department", "Part II, Section 4 (Audit Department)", "Head of Internal Audit", "08000000008"),
]

CATEGORIES = [
    "Rates",
    "Licences and Permits",
    "Fees and Charges",
    "Registration and Professional Fees",
    "Levies",
]

_UNASSIGNED_NOTE = (
    "In the Kuje harmonised revenue code list but not assigned to any department in the "
    "council's departmental schedule. The bye-law citation is the gazette's own — every one "
    "of these items does have a Part of its own, read off that Part's body header — so only "
    "the owning DEPARTMENT is still outstanding and needs council confirmation."
)

# (code, name, unit, category, dept_code|None, base_rate, bye_law_ref, bye_law_description)
REVENUE_ITEMS = [
    # --- 2.1 Works, Lands, Housing and Engineering ---------------------------
    ("30010049", "Tenement Rate Collection", "Per Annum", "Rates", "WORKS", 20000, "Part XXI",
     "Assessed on all ratable properties at 4% of annual value. Collected by the Valuation Office under this department."),
    ("30010035", "Stacking of Building Material / Construction Permit", "Per Permit", "Licences and Permits", "WORKS", 25000, "Part VIII",
     "Fees for stacking building materials on streets; includes daily charges for unlawful stacking. Part VIII (Part B) "
     "covers construction site permits, including special permits for pile driving."),
    ("30010038", "Cutting Of Road Tar", "Per Permit", "Fees and Charges", "WORKS", 15000, "Part X",
     "Fee of ₦15,000 minimum for cutting road tar for speed breakers, water pipes, or cables."),
    ("30010040", "Numbering / Street Naming", "Per Annum", "Registration and Professional Fees", "WORKS", 5000, "Part XII",
     "Fees paid by individuals who want streets/roads named after them. Applications may be processed by the Department "
     "of Education, Social, Information, Sports and Culture."),
    ("30010052", "Wrong Parking, Corporate Parking Permit/License", "Per Annum", "Fees and Charges", "WORKS", 10000, "Part XXIV",
     "Annual parking permits for corporate bodies and institutions, together with penalties and recovery fees for "
     "illegally parked or abandoned vehicles."),

    # --- 2.2 Environmental ---------------------------------------------------
    ("30010033", "Environmental Sanitation and Premise Inspection", "Per Annum", "Fees and Charges", "ENV", 10000, "Part V",
     "Monthly waste management charges for residential, commercial, industrial, and institutional premises. Part V "
     "(Section 6.6) covers fees collected during inspection of premises for sanitary compliance. Administered with the "
     "Environmental Sanitation and Waste Management Authority."),
    ("30010046", "Public Toilet", "Per Annum", "Fees and Charges", "ENV", 10000, "Part XVIII",
     "Fees for establishing/operating public toilets; annual renewal fees."),
    ("KJ30010063", "Private Dislodging Tank/Vehicle Registration", "Per Annum", "Registration and Professional Fees", "ENV", 100000, "Part XVIII",
     "₦100,000 annual registration for private dislodging vehicles."),
    ("30010047", "Pest Control", "Per Annum", "Registration and Professional Fees", "ENV", 100000, "Part XIX",
     "₦100,000 annual registration for private pest control firms operating in Kuje."),
    ("30010050", "Private Sector Participation Refuse Operation (PSPRO)", "Per Annum", "Fees and Charges", "ENV", 15000, "Part XXII",
     "Registration and licensing of PSP refuse operators."),
    ("KJ30010064", "Fumigation Certificates", "Per Certificate", "Fees and Charges", "ENV", 10000, "Part XIX",
     "Fees for fumigation of food premises before registration. Issued also as the Pest Control Certificates the Medical "
     "and Health Care Department requires before registering a food premises. NOTE: Part XIX (gazette B273-B274) "
     "gazettes only the N100,000 pest-control FIRM registration, no separate certificate fee — this item's rate is a "
     "placeholder awaiting the council's own figure."),
    ("KJ30010065", "Certificate of Fitness for Habitation", "Per Certificate", "Fees and Charges", "ENV", 50000, "Part V (Section 8.0)",
     "Fees for issuance of certificate of fitness for habitation/continued habitation. Priced by gazette "
     "Schedule C (B219): 26 establishment types x three charges — Fitness for Habitation (old building), "
     "Fitness for Continued Habitation (new building), and Fitness for Use (renewal)."),

    # --- 2.3 Finance and Supplies -------------------------------------------
    ("30010043", "Trade License, Private Lockup Shops and Allied Matters", "Per Annum", "Licences and Permits", "FIN", 20000, "Part XV",
     "Annual license fees for all businesses, shops, kiosks, workshops, and corporate offices."),
    ("30010048", "Contractors", "Per Registration", "Registration and Professional Fees", "FIN", 50000, "Part XX",
     "Registration fees for contractors (construction, supply, services, consultancy)."),
    ("30010061", "Tender Fees", "Per Tender", "Registration and Professional Fees", "FIN", 10000, "Part XX",
     "Fees paid by intending contractors for tender documents."),
    ("30010057", "Agreement Fees on Sale of Land and Other Disposition", "Per Transaction", "Registration and Professional Fees", "FIN", 20000, "Part XX",
     "Fees for land agreements and other dispositions."),
    ("30010058", "Fees for Certificate of Occupancy", "Per Certificate", "Registration and Professional Fees", "FIN", 25000, "Part XX",
     "Processing fees for C of O."),
    ("30010059", "Fees for Change of Ownership", "Per Transaction", "Registration and Professional Fees", "FIN", 15000, "Part XX",
     "Fees for transfer of property ownership."),
    ("30010060", "Searches", "Per Search", "Registration and Professional Fees", "FIN", 5000, "Part XX",
     "Fees for land searches."),
    ("30010062", "Community and Development Levy", "Per Annum", "Levies", "FIN", 5000, "Part XXV",
     "Annual levy on residents (individuals and corporate bodies) except exempted categories. Collected and enforced by "
     "the Area Council Revenue Committee."),

    # --- 2.4 Administration and General Services -----------------------------
    ("30010031", "Registration of Marriages, Births and Death", "Per Registration", "Registration and Professional Fees", "ADMIN", 5000, "Part III",
     "Fees for registering marriages, births, and deaths."),
    ("30010044", "Radio and Television License", "Per Annum", "Licences and Permits", "ADMIN", 5000, "Part XVI",
     "Annual license fees on radio/TV sets and communication masts."),
    ("30010036", "Mobile Advert", "Per Annum", "Licences and Permits", "ADMIN", 15000, "Part VII",
     "Fees for branded/operational vehicles used for advertisement. Collected by the Internal Revenue General Committee."),
    ("30010051", "Liquor Licensing", "Per Annum", "Licences and Permits", "ADMIN", 50000, "Part XXIII",
     "Annual fees for tavern, wine/beer, hotel, club, wholesale, and retail liquor licenses."),
    ("30010056", "Communication Mast License", "Per Annum", "Licences and Permits", "ADMIN", 1000000, "Part XVI",
     "Annual license fees on communication masts, licensed under the same bye-law as radio and television sets."),

    # --- 2.5 Medical and Health Care ----------------------------------------
    ("30010054", "Regulated Premises", "Per Annum", "Fees and Charges", "HEALTH", 30000, "Part XXVI",
     "Annual registration fees for hotels, guest inns, restaurants, bakeries, dairies, aerated water manufacturers, and "
     "food-related establishments."),
    ("30010053", "Foodstuff Regulation", "Per Month", "Fees and Charges", "HEALTH", 10000, "Part XXVI",
     "Permits for foodstuff premises and food handlers. Priced by the Regulated Premises bye-law's "
     "SECOND Schedule (s.3(c), gazette B317-B318) — monthly rates by premises sub-type, Categories A-H — "
     "which is a separate recurring charge from that bye-law's FIRST Schedule licence fee. The council's "
     "departmental schedule cites Part XXIV for this item, but Part XXIV is Wrong Parking/Corporate "
     "Parking (gazette B297); the foodstuff monthly rates sit in Part XXVI."),

    # --- 2.6 Education, Social, Information, Sports and Culture --------------
    ("30010034", "Control of Advertisement", "Per Annum", "Licences and Permits", "EDU", 20000, "Part VI",
     "Permit fees for fixed advertisements such as billboards, signboards, neon signs, banners, etc."),
    ("KJ30010066", "Trade Site Allocation", "Per Allocation", "Fees and Charges", "EDU", 20000, "Part II (Section 7)",
     "Fees for allocation of trade sites within the Council."),
    ("30010055", "Registration of Social Organizations", "Per Registration", "Registration and Professional Fees", "EDU", 5000, "Part II (Section 7)",
     "Fees for registration of voluntary, self-help, and social organizations."),
    ("KJ30010067", "Cinema and Viewing Centre Licenses", "Per Annum", "Licences and Permits", "EDU", 20000, "Part XV",
     "License fees for cinema houses and viewing centres."),

    # --- 2.7 Agricultural and Natural Resources ------------------------------
    ("KJ30010068", "Veterinary Services Fees", "Per Service", "Fees and Charges", "AGRIC", 5000, "Part II (Section 2)",
     "Fees for vaccination, animal treatment, and slaughterhouse supervision. Health oversight is provided by the "
     "Medical and Health Care Department."),
    ("KJ30010069", "Forestry Fees and Royalties", "Per Assessment", "Fees and Charges", "AGRIC", 10000, "Part II (Section 2)",
     "Fees for forest produce measurement, assessment, and royalties."),
    ("KJ30010070", "Agricultural Extension Services", "Per Service", "Fees and Charges", "AGRIC", 10000, "Part II (Section 2)",
     "Fees for tractor hiring services and agricultural training programmes."),
    ("KJ30010071", "Fishery Services", "Per Service", "Fees and Charges", "AGRIC", 5000, "Part II (Section 2)",
     "Fees for fishery extension services and demonstrations."),
    ("KJ30010072", "Cooperative Registration", "Per Registration", "Registration and Professional Fees", "AGRIC", 5000, "Part II (Section 2)",
     "Registration fees for cooperative societies."),

    # --- Harmonised codes with no department in the source document ----------
    ("30010032", "Motor Parks (Commercial Vehicles picking up passengers)", "Per Annum", "Fees and Charges", None, 15000, "Part IV", _UNASSIGNED_NOTE),
    ("30010037", "Loading/Off Loading Control of Traffic", "Per Annum", "Fees and Charges", None, 10000, "Part IX", _UNASSIGNED_NOTE),
    ("30010039", "Movement and Keeping of Dogs", "Per Annum", "Licences and Permits", None, 5000, "Part XI", _UNASSIGNED_NOTE),
    ("30010041", "Registration of Dry Cleaning and Laundry Houses", "Per Annum", "Registration and Professional Fees", None, 10000, "Part XIII", _UNASSIGNED_NOTE),
    ("30010042", "Market Regulation", "Per Annum", "Fees and Charges", None, 10000, "Part XIV", _UNASSIGNED_NOTE),
    ("30010045", "Tricycle (Keke) Commercial Motor Cycle Regulation", "Per Annum", "Licences and Permits", None, 10000, "Part XVII", _UNASSIGNED_NOTE),
]


def _range(label, min_amount, max_amount):
    return {"label": label, "rate_mode": RateBand.RANGE, "min_amount": Decimal(min_amount), "max_amount": Decimal(max_amount)}


def _flat(label, amount):
    return {"label": label, "rate_mode": RateBand.FLAT, "flat_amount": Decimal(amount)}


def _tiered(label, tiers):
    return {"label": label, "rate_mode": RateBand.TIERED,
            "tiers": [{"label": t, "amount": Decimal(a)} for t, a in tiers]}


def build_band_specs():
    """Gazette schedules keyed by harmonised code. Every figure comes from
    `seed_rate_bands`; nothing is computed or filled in here."""
    return {
        "30010034": [_range(*b) for b in CONTROL_OF_ADVERTISEMENT_BANDS],
        "30010035": [_range(*b) for b in BUILDING_MATERIALS_BANDS],
        "30010036": [_flat(*b) for b in MOBILE_ADVERT_FLAT],
        "30010037": [_flat(*b) for b in LOADING_OFFLOADING_FLAT],
        "30010043": [_range(*b) for b in TRADE_LICENSE_BANDS],
        "30010048": [_tiered(lbl, tiers) for lbl, tiers in CONTRACTORS_TIERED]
                    + [_flat(*b) for b in CONTRACTORS_FLAT],
        "30010051": [_tiered(lbl, [("Large", lg), ("Medium", md), ("Small", sm)])
                     for lbl, lg, md, sm in LIQUOR_LICENSING_BANDS],
        "30010052": [_range(*b) for b in WRONG_PARKING_CORPORATE_BANDS],
        "30010053": [_flat(*b) for b in FOODSTUFF_REGULATION_FLAT],
        # One unlabeled band: the gazette gives a single Large/Medium/Small
        # triple for the whole item, with no sub-classification above it.
        "30010056": [_tiered("", COMMUNICATION_MAST_TIERS)],
        "30010054": [
            spec
            for est, l_mn, l_mx, m_mn, m_mx, s_mn, s_mx in REGULATED_PREMISES_RANGES
            for spec in (
                _range(f"{est} — Large", l_mn, l_mx),
                _range(f"{est} — Medium", m_mn, m_mx),
                _range(f"{est} — Small", s_mn, s_mx),
            )
        ],
        "30010062": [_tiered(lbl, tiers) for lbl, tiers in COMMUNITY_LEVY_TIERED]
                    + [_flat(*b) for b in COMMUNITY_LEVY_FLAT],
        # Read straight from the gazette — see apps.revenue.gazette_kac.
        "30010061": [_flat(*b) for b in TENDER_FEES_FLAT],
        "KJ30010065": [_flat(*b) for b in certificate_of_fitness_bands()],
    }


class Command(BaseCommand):
    help = (
        "Back up, clear and re-seed a council's departments and revenue items from "
        "KUJE DEPARTMENT.docx, with gazette bye-law references, bands and classifications."
    )

    def add_arguments(self, parser):
        parser.add_argument("--council", default="KAC", help="Council code to seed (default: KAC).")
        parser.add_argument("--skip-backup", action="store_true", help="Do not write a JSON backup first.")
        parser.add_argument("--dry-run", action="store_true", help="Roll the whole transaction back at the end.")

    def handle(self, *args, **options):
        council_code = options["council"]
        try:
            council = Council.objects.get(council_code=council_code)
        except Council.DoesNotExist:
            raise CommandError(f"No council with code {council_code!r}.")

        if not options["skip_backup"]:
            self.stdout.write(self.style.MIGRATE_HEADING("[1/5] Backing up current state"))
            call_command("backup_departments_and_revenue", council=council_code)
        else:
            self.stdout.write(self.style.WARNING("[1/5] Backup skipped (--skip-backup)"))

        dry_run = options["dry_run"]
        try:
            with council_context(council.id):
                self._guard_dependents(council)
                self._clear(council)
                departments = self._seed_departments(council)
                items = self._seed_items(council, departments)
                self._seed_bands(items)
                self._report(council, departments, items)
                if dry_run:
                    self.stdout.write(self.style.WARNING("\n--dry-run: rolling back."))
                    transaction.set_rollback(True)
        except Exception as exc:
            raise CommandError(f"Seed aborted, nothing committed: {exc}") from exc

    def _guard_dependents(self, council):
        """Refuse rather than widen the blast radius. Assessments and portfolios
        are PROTECTed against a revenue item; if any exist, clearing the items
        would mean deleting real operational data this command has no business
        touching."""
        blockers = []
        assessments = self._assessment_count(council)
        if assessments:
            blockers.append(f"{assessments} assessment(s)")
        consultant_rows = ConsultantPortfolio.objects.filter(council=council).count()
        if consultant_rows:
            blockers.append(f"{consultant_rows} consultant portfolio row(s)")
        agent_rows = AgentPortfolio.objects.filter(council=council).count()
        if agent_rows:
            blockers.append(f"{agent_rows} agent portfolio row(s)")
        if blockers:
            raise CommandError(
                "Existing revenue items are still referenced by "
                + ", ".join(blockers)
                + ". Reassign or remove those first — this command will not delete them."
            )

    @staticmethod
    def _assessment_count(council):
        from apps.billing.models import Assessment

        return Assessment.objects.filter(council=council).count()

    def _clear(self, council):
        self.stdout.write(self.style.MIGRATE_HEADING("[2/5] Clearing departments and revenue items"))
        item_ids = list(CouncilRevenueItem.objects.filter(council=council).values_list("id", flat=True))

        tiers, _ = RateTier.objects.filter(band__council_revenue_item_id__in=item_ids).delete()
        bands, _ = RateBand.objects.filter(council_revenue_item_id__in=item_ids).delete()
        schedules, _ = RateSchedule.objects.filter(council_revenue_item_id__in=item_ids).delete()
        items, _ = CouncilRevenueItem.objects.filter(council=council).delete()
        departments, _ = Department.objects.filter(council=council).delete()

        self.stdout.write(
            f"  cleared {departments} departments, {items} revenue items, "
            f"{schedules} rate schedules, {bands} bands, {tiers} tiers"
        )

    def _seed_departments(self, council):
        self.stdout.write(self.style.MIGRATE_HEADING("[3/5] Seeding departments"))
        departments = {}
        for code, name, legal_basis, head, phone in DEPARTMENTS:
            departments[code] = Department.objects.create(
                council=council,
                department_name=name,
                department_code=code,
                head_name=head,
                head_phone=phone,
                legal_basis=legal_basis,
            )
            self.stdout.write(f"  [{code:<6}] {name}  ({legal_basis})")
        self.stdout.write(self.style.SUCCESS(f"  {len(departments)} departments seeded"))
        return departments

    def _seed_items(self, council, departments):
        self.stdout.write(self.style.MIGRATE_HEADING("[4/5] Seeding revenue items"))
        for index, name in enumerate(CATEGORIES):
            RevenueCategory.objects.get_or_create(name=name, defaults={"sort_order": index})
        categories = {c.name: c for c in RevenueCategory.objects.all()}

        today = date.today()
        items = {}
        for code, name, unit, category_name, dept_code, rate, bye_law, description in REVENUE_ITEMS:
            category = categories[category_name]

            # Council-local items (KJ-prefixed) have no place in the harmonised
            # template catalogue — they exist for Kuje only, so `template` stays
            # null rather than polluting the shared catalogue with local codes.
            template = None
            if not code.startswith("KJ"):
                template, _ = RevenueItemTemplate.objects.update_or_create(
                    harmonised_code=code,
                    defaults={"item_name": name, "unit_of_charge": unit, "category": category},
                )

            item = CouncilRevenueItem.objects.create(
                council=council,
                template=template,
                harmonised_code=code,
                item_name=name,
                category=category,
                unit_of_charge=unit,
                department=departments.get(dept_code) if dept_code else None,
                bye_law_reference=bye_law,
                bye_law_description=description,
            )
            RateSchedule.objects.create(
                council_revenue_item=item, rate_amount=Decimal(rate), effective_from=today
            )
            items[code] = item

        assigned = sum(1 for i in items.values() if i.department_id)
        self.stdout.write(
            self.style.SUCCESS(
                f"  {len(items)} revenue items seeded ({assigned} mapped to a department, "
                f"{len(items) - assigned} awaiting assignment)"
            )
        )
        return items

    def _seed_bands(self, items):
        self.stdout.write(self.style.MIGRATE_HEADING("[5/5] Seeding gazette bands and classifications"))
        total_bands = total_tiers = 0
        for code, specs in build_band_specs().items():
            item = items.get(code)
            if item is None:
                continue
            created = replace_rate_bands(council_revenue_item=item, bands=specs, actor=None)
            tier_count = sum(len(s.get("tiers") or []) for s in specs)
            total_bands += len(created)
            total_tiers += tier_count
            self.stdout.write(
                f"  [{code}] {item.item_name[:52]:<52} {len(created):>3} bands"
                + (f", {tier_count} tiers" if tier_count else "")
            )
        self.stdout.write(self.style.SUCCESS(f"  {total_bands} bands and {total_tiers} tiers seeded"))

    def _report(self, council, departments, items):
        self.stdout.write(self.style.MIGRATE_HEADING("\nDepartment-wise revenue portfolio"))
        for code, _, _, _, _ in DEPARTMENTS:
            dept = departments[code]
            owned = [i for i in items.values() if i.department_id == dept.id]
            self.stdout.write(f"\n  {dept.department_name}  [{code}] — {dept.legal_basis}")
            if not owned:
                self.stdout.write("      (no revenue items — oversight only)")
            for item in sorted(owned, key=lambda i: i.harmonised_code):
                band_count = item.rate_bands.filter(effective_to__isnull=True).count()
                suffix = f"  · {band_count} bands" if band_count else ""
                self.stdout.write(
                    f"      {item.harmonised_code:<11} {item.item_name[:48]:<48} "
                    f"{item.bye_law_reference or '—':<22}{suffix}"
                )

        unassigned = [i for i in items.values() if not i.department_id]
        if unassigned:
            self.stdout.write(self.style.WARNING("\n  Awaiting departmental assignment"))
            for item in sorted(unassigned, key=lambda i: i.harmonised_code):
                self.stdout.write(f"      {item.harmonised_code:<11} {item.item_name}")
