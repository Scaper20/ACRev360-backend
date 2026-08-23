"""
Clears every piece of transactional/demo data for a council while leaving its
onboarding baseline untouched by default — the council record, wards,
revenue-item catalog + flat rates (seed_kuje.py), gazette rate bands
(seed_rate_bands.py), the channel/role catalogue, and the admin login.
Everything seed_demo_data.py would add, and everything a real user adds
through the app (payers, bills, payments, receipts, consultants, agents,
settlements, audit history), gets deleted.

--full goes further: also deletes the baseline itself (wards, rate bands,
rate schedules, the revenue-item catalog, the admin login, and finally the
Council row), so the council needs re-onboarding from scratch afterward —
e.g. `seed_kuje` then `seed_rate_bands` again. Use this after reworking
seed_kuje.py/seed_rate_bands.py's own source data (rates, bands), since the
default (non-full) reset deliberately never touches those tables — there'd
be nothing to make the rework take effect otherwise.

Deletion order matters — several FKs are PROTECT, not CASCADE, so children
must be cleared before the parents they'd otherwise block:
  Payment (before Bill, POSTerminal) -> cascades Receipt
  Bill (before Payer) -> cascades BillLine, DebtCase
  CommissionSettlement, POSTerminal (before SubConsultant/FieldAgent)
  Payer (after Bill) -> cascades Assessment, EnumeratedAsset
  non-admin AppUser (after everything above) -> cascades FieldAgent
  SubConsultant (last — blocked until the AppUsers pointing at it are gone)
--full adds, after the above:
  RateSchedule (before CouncilRevenueItem — PROTECT, doesn't cascade)
  CouncilRevenueItem (cascades RateBand -> RateTier)
  WardZone (safe once Payer/POSTerminal/portfolios, all PROTECT against it,
    are already gone above)
  Every remaining AppUser, i.e. the admin login (before Council — PROTECT)
  Council itself (cascades CouncilConfig)

Same RLS pattern as seed_kuje/seed_demo_data: SET LOCAL app.council_id is
transaction-scoped, so set_council_context() must run inside the same atomic
block as the deletes, or they silently affect zero rows instead of erroring.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import AppRole, AppUser, SubConsultant
from apps.audit.models import AuditLog
from apps.billing.models import Bill
from apps.fieldops.models import MobileSyncRecord
from apps.payments.models import APIClient, ChannelTransactionFeed, Payment, POSTerminal
from apps.reconciliation.models import ReconciliationException, ReconciliationRun
from apps.registry.models import Payer
from apps.revenue.models import CouncilRevenueItem, RateSchedule
from apps.settlements.models import CommissionSettlement
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, WardZone


class Command(BaseCommand):
    help = (
        "Clear all transactional/demo data for a council (payers, bills, payments, "
        "receipts, consultants, agents, settlements, audit log, etc.) while keeping "
        "the onboarding baseline intact: the council record, wards, revenue-item "
        "catalog, rates and gazette bands, and the admin login."
    )

    def add_arguments(self, parser):
        parser.add_argument("--council-code", default="KAC")
        parser.add_argument(
            "--full", action="store_true",
            help="Also delete the baseline itself (wards, revenue-item catalog, rate bands, "
                 "admin login, and the Council row) — re-onboarding (seed_kuje + "
                 "seed_rate_bands) is needed afterward.",
        )
        parser.add_argument(
            "--yes", action="store_true",
            help="Skip the confirmation prompt (needed for non-interactive use).",
        )

    def handle(self, *args, **options):
        try:
            council = Council.objects.get(council_code=options["council_code"])
        except Council.DoesNotExist:
            raise CommandError(f"No council with code {options['council_code']!r}")

        full = options["full"]
        if not options["yes"]:
            scope = (
                f"permanently delete {council.council_code} itself, along with its wards, "
                f"revenue-item catalog, rate bands, and the admin login"
                if full else
                f"permanently delete ALL payers, bills, payments, receipts, consultants, "
                f"agents, settlements and audit history for {council.council_code}. "
                f"The council, wards, revenue-item catalog/rates, and the admin login are kept"
            )
            confirm = input(f"This will {scope}.\nType the council code ({council.council_code}) to confirm: ")
            if confirm != council.council_code:
                self.stdout.write(self.style.WARNING("Aborted — confirmation did not match."))
                return

        council_code = council.council_code
        with transaction.atomic():
            set_council_context(council.id)
            self._clear(council)
            if full:
                self._clear_full_baseline(council)

        if full:
            self.stdout.write(self.style.SUCCESS(f"{council_code} fully deleted — re-run seed_kuje to start over."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{council_code} reset — onboarding baseline kept, all transactional/demo data cleared."
            ))

    def _wipe(self, qs, label):
        count = qs.count()
        qs.delete()
        self.stdout.write(f"  {label}: {count} deleted")

    def _clear(self, council):
        self._wipe(AuditLog.objects.filter(council=council), "Audit log entries")
        self._wipe(ReconciliationException.objects.filter(council=council), "Reconciliation exceptions")
        self._wipe(ReconciliationRun.objects.filter(council=council), "Reconciliation runs")
        self._wipe(ChannelTransactionFeed.objects.filter(council=council), "Bank feed rows")
        self._wipe(MobileSyncRecord.objects.filter(council=council), "Mobile sync records")

        self._wipe(Payment.objects.filter(council=council), "Payments (+ receipts)")
        self._wipe(Bill.objects.filter(council=council), "Bills (+ lines, debt cases)")
        self._wipe(CommissionSettlement.objects.filter(council=council), "Commission settlements")
        self._wipe(POSTerminal.objects.filter(council=council), "POS terminals")
        self._wipe(APIClient.objects.filter(council=council), "API clients")
        self._wipe(Payer.objects.filter(council=council), "Payers (+ assessments, assets)")

        self._wipe(
            AppUser.objects.filter(
                council=council,
                role__access_level__in=[AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW],
            ),
            "Non-admin logins (consultants, agents, stakeholders — cascades field agents)",
        )
        self._wipe(SubConsultant.objects.filter(council=council), "Sub-consultants")

    def _clear_full_baseline(self, council):
        self._wipe(RateSchedule.objects.filter(council_revenue_item__council=council), "Rate schedules")
        self._wipe(CouncilRevenueItem.objects.filter(council=council), "Revenue items (+ rate bands, tiers)")
        self._wipe(WardZone.objects.filter(council=council), "Wards")
        self._wipe(AppUser.objects.filter(council=council), "Remaining logins (admin)")
        self._wipe(Council.objects.filter(id=council.id), "Council (+ config)")
