"""
Populates a freshly-onboarded council with realistic demo data so the app can
be clicked through as if it were mid-operation: payers, field agents,
sub-consultants (with portfolios), POS terminals, bills (against both flat
and gazette-banded revenue items), payments across every channel, debt
cases, commission settlements, and a couple of reconciliation runs.

Run after seed_kuje and seed_rate_bands — this command assumes the council,
wards, revenue items, and rate bands already exist.
"""

import random
import string
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.billing.services import issue_bill
from apps.enforcement.services import refresh_debt
from apps.payments.models import ChannelTransactionFeed, PaymentChannel, POSTerminal
from apps.payments.services import post_payment
from apps.registry.models import Payer
from apps.registry.services import DuplicatePayer, create_payer
from apps.revenue.models import ConsultantPortfolio, CouncilRevenueItem, RateBand
from apps.settlements.services import compute_settlements
from apps.reconciliation.services import run_reconciliation
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, WardZone

FIRST_NAMES = [
    "Ade", "Chidi", "Emeka", "Bola", "Ngozi", "Yusuf", "Amina", "Tunde",
    "Chioma", "Ibrahim", "Funke", "Kelechi", "Aisha", "Segun", "Uche",
    "Fatima", "Bayo", "Nkechi", "Musa", "Folake", "Obinna", "Zainab",
    "Kunle", "Adaeze", "Suleiman", "Temitope", "Chinwe", "Nasir", "Bisi",
    "Ikechukwu", "Halima", "Wale", "Ijeoma", "Aliyu", "Ronke", "Chukwuemeka",
    "Hauwa", "Damilola", "Okonkwo", "Rukayat",
]
LAST_NAMES = [
    "Okafor", "Bello", "Adeyemi", "Eze", "Yakubu", "Okonkwo", "Abubakar",
    "Adewale", "Nwosu", "Suleiman", "Okoro", "Danladi", "Ogunleye", "Chukwu",
    "Mohammed", "Balogun", "Nnamdi", "Garba", "Ibekwe", "Usman", "Afolabi",
    "Umar", "Onyeka", "Sani", "Adebayo", "Idris", "Obi", "Lawal", "Ojo",
    "Aliyu", "Ekwueme", "Haruna", "Fashola", "Momoh", "Anyanwu", "Shehu",
]
BUSINESS_SUFFIXES = [
    "Ventures", "Enterprises", "Trading Co", "& Sons", "Global Concepts",
    "Multi-Ventures", "Stores", "Nigeria Ltd", "Investments Ltd",
    "Integrated Services", "& Brothers", "Resources Ltd",
]
PHONE_PREFIXES = ["0803", "0805", "0806", "0807", "0810", "0813", "0814", "0816", "0703", "0706", "0813", "0902", "0906"]


def _phone(used):
    while True:
        p = random.choice(PHONE_PREFIXES) + "".join(random.choices(string.digits, k=7))
        if p not in used:
            used.add(p)
            return p


class Command(BaseCommand):
    help = "Populate a council with realistic demo data for exploring the app in a near-operational state."

    def add_arguments(self, parser):
        parser.add_argument("--council-code", default="KAC")
        parser.add_argument("--payers", type=int, default=100)

    @transaction.atomic
    def handle(self, *args, **options):
        council = Council.objects.get(council_code=options["council_code"])
        set_council_context(council.id)

        if Payer.objects.filter(council=council).exists():
            self.stdout.write(self.style.WARNING(f"{council.council_code} already has payers — skipping (clear the DB first to reseed)"))
            return

        admin = AppUser.objects.filter(council=council, role__access_level=AppRole.COUNCIL_ADMIN).first()
        if admin is None:
            self.stdout.write(self.style.ERROR("No council admin user found — run seed_kuje first"))
            return

        wards = list(WardZone.objects.filter(council=council))
        items = list(CouncilRevenueItem.objects.filter(council=council, is_active=True).prefetch_related("rate_bands__tiers"))
        channels = {c.code: c for c in PaymentChannel.objects.filter(code__in=[c for c, _ in PaymentChannel.CODE_CHOICES])}
        for code, _label in PaymentChannel.CODE_CHOICES:
            channels.setdefault(code, PaymentChannel.objects.get_or_create(code=code)[0])

        consultants = self._seed_consultants(council, items)
        agents = self._seed_agents(council, consultants, wards)
        self._seed_terminals(council, agents, wards)
        used_phones = set()
        payers = self._seed_payers(council, admin, agents, wards, options["payers"], used_phones)
        recon_candidates = self._seed_bills_and_payments(council, admin, payers, items, channels)

        result = refresh_debt(council_id=council.id, actor=admin)
        self.stdout.write(self.style.SUCCESS(f"Debt ageing: {result['opened']} case(s) opened"))

        settlements = compute_settlements(
            council_id=council.id, period_start=timezone.localdate() - timezone.timedelta(days=30),
            period_end=timezone.localdate(), actor=admin,
        )
        self.stdout.write(self.style.SUCCESS(f"Computed {len(settlements)} commission settlement(s)"))

        self._seed_reconciliation(council, admin, channels, recon_candidates)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data seeded for {council.council_code}: {len(payers)} payers, {len(agents)} agents, "
            f"{len(consultants)} consultants"
        ))

    # ---------------------------------------------------------------- consultants

    def _seed_consultants(self, council, items):
        specs = [
            ("Zenith Revenue Partners", "KAC/RC/2026/001", Decimal("30.00")),
            ("Bridgeway Consultants", "KAC/RC/2026/002", Decimal("25.00")),
            ("Highgate Fiscal Services", "KAC/RC/2026/003", Decimal("35.00")),
            ("Northstar Municipal Advisors", "KAC/RC/2026/004", Decimal("28.00")),
        ]
        consultants = []
        for name, ref, rate in specs:
            consultant = SubConsultant.objects.create(
                council=council, consultant_name=name, contract_ref=ref,
                commission_rate=rate, status=SubConsultant.ACTIVE,
            )
            for item in random.sample(items, k=min(3, len(items))):
                ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item, ward=None)
            consultants.append(consultant)
        return consultants

    # --------------------------------------------------------------------- agents

    def _seed_agents(self, council, consultants, wards):
        agent_role, _ = AppRole.objects.get_or_create(name="FIELD_AGENT", defaults={"access_level": AppRole.AGENT})
        agents = []
        for i in range(1, 9):
            consultant = None if i <= 3 else consultants[(i - 4) % len(consultants)]
            first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
            app_user = AppUser.objects.create_user(
                username=f"agent{i:02d}", password="acrev360-2026", full_name=f"{first} {last}",
                phone=_phone(set()), council_id=council.id, role=agent_role,
                consultant_id=consultant.id if consultant else None,
            )
            agent = FieldAgent.objects.create(
                council=council, user=app_user, agent_code=f"AGT-{i:05d}",
                assigned_ward=random.choice(wards), status=FieldAgent.ACTIVE,
            )
            agents.append(agent)
        return agents

    def _seed_terminals(self, council, agents, wards):
        for i, agent in enumerate(random.sample(agents, k=min(5, len(agents))), start=1):
            POSTerminal.objects.create(
                council=council, terminal_id=f"TERM-{i:04d}", bank_terminal_id=f"BTID{1000 + i}",
                agent=agent, ward=agent.assigned_ward or random.choice(wards),
                status=POSTerminal.FAULTY if i == 5 else POSTerminal.ACTIVE,
            )

    # --------------------------------------------------------------------- payers

    def _seed_payers(self, council, admin, agents, wards, count, used_phones):
        payers = []
        for _ in range(count):
            is_business = random.random() < 0.6
            ward = random.choice(wards)
            phone = _phone(used_phones)
            enumerator = admin if random.random() < 0.55 else random.choice(agents).user

            if is_business:
                name = f"{random.choice(LAST_NAMES)} {random.choice(BUSINESS_SUFFIXES)}"
                payer_type = Payer.BUSINESS
                business_size = random.choices(
                    [Payer.MICRO, Payer.SMALL, Payer.MEDIUM, Payer.LARGE], weights=[40, 30, 20, 10]
                )[0]
            else:
                name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
                payer_type = Payer.INDIVIDUAL
                business_size = None

            kyc_status = random.choices(
                [Payer.VERIFIED, Payer.PENDING, Payer.FLAGGED], weights=[70, 20, 10]
            )[0]

            try:
                payer, _drafts = create_payer(
                    council_id=council.id, actor=enumerator, payer_type=payer_type, full_name=name,
                    phone=phone, address=f"{random.randint(1, 200)} {ward.ward_name} Road", ward=ward,
                    business_size=business_size, kyc_status=kyc_status,
                )
            except DuplicatePayer:
                continue
            payers.append(payer)
        return payers

    # ---------------------------------------------------------- bills & payments

    def _pick_line(self, item):
        """Returns kwargs for issue_bill's lines=[...] entry for one revenue item,
        resolving a band/tier if the item is banded."""
        bands = list(item.active_bands)
        if not bands:
            return {"council_revenue_item": item, "quantity": 1}
        band = random.choice(bands)
        if band.rate_mode == RateBand.FLAT:
            return {"council_revenue_item": item, "quantity": 1, "rate_band": band}
        if band.rate_mode == RateBand.RANGE:
            amount = Decimal(random.randint(int(band.min_amount), int(band.max_amount)))
            return {"council_revenue_item": item, "quantity": 1, "rate_band": band, "amount_override": amount}
        tier = random.choice(list(band.tiers.all()))
        return {"council_revenue_item": item, "quantity": 1, "rate_band": band, "rate_tier": tier}

    def _seed_bills_and_payments(self, council, admin, payers, items, channels):
        recon_candidates = []  # (channel_code, bank_txn_ref, amount)
        today = timezone.localdate()

        for payer in payers:
            if random.random() > 0.85:
                continue  # ~15% of payers have no bills yet — realistic backlog
            for _ in range(random.choice([1, 1, 1, 2])):
                item = random.choice(items)
                line = self._pick_line(item)
                overdue = random.random() < 0.2
                due_date = today - timezone.timedelta(days=random.randint(7, 75)) if overdue else None
                try:
                    bill = issue_bill(
                        council_id=council.id, payer=payer, due_date=due_date, lines=[line], actor=admin,
                    )
                except Exception:
                    continue

                outcome = random.random()
                if outcome < 0.55:
                    amount = bill.balance
                elif outcome < 0.70:
                    amount = (bill.balance * Decimal(random.choice([30, 40, 50, 60, 70])) / 100).quantize(Decimal("1"))
                else:
                    continue  # left unpaid

                if amount <= 0:
                    continue
                channel = random.choice(list(channels.values()))
                posted_by = payer.enumerated_by if payer.enumerated_by_id else admin
                bank_txn_ref = ""
                if channel.code in (PaymentChannel.POS, PaymentChannel.OTC):
                    bank_txn_ref = "BNK" + "".join(random.choices(string.digits, k=8))
                try:
                    payment = post_payment(
                        council_id=council.id, bill=bill, channel=channel, amount=amount,
                        bank_txn_ref=bank_txn_ref, posted_by=posted_by,
                    )
                except Exception:
                    continue
                if bank_txn_ref:
                    recon_candidates.append((channel.code, bank_txn_ref, payment.amount))
        return recon_candidates

    # --------------------------------------------------------------- reconciliation

    def _seed_reconciliation(self, council, admin, channels, recon_candidates):
        today = timezone.localdate()
        by_channel = {}
        for code, ref, amount in recon_candidates:
            by_channel.setdefault(code, []).append((ref, amount))

        for code, rows in by_channel.items():
            channel = channels[code]
            # ~90% of collected payments show up in the bank feed today; a few
            # phantom bank credits with no matching payment create exceptions.
            for ref, amount in rows:
                if random.random() < 0.9:
                    ChannelTransactionFeed.objects.create(council_id=council.id, channel=channel, bank_txn_ref=ref, amount=amount)
            for _ in range(random.randint(1, 3)):
                ChannelTransactionFeed.objects.create(
                    council_id=council.id, channel=channel,
                    bank_txn_ref="BNK" + "".join(random.choices(string.digits, k=8)),
                    amount=Decimal(random.randint(2000, 50000)),
                )
            run_reconciliation(council_id=council.id, channel=channel, run_date=today, actor=admin)
