"""
Clears every piece of transactional/demo data for a council while leaving its
onboarding baseline untouched — the council record, wards, revenue-item
catalog + flat rates (seed_kuje.py), gazette rate bands (seed_rate_bands.py),
the channel/role catalogue, and the admin login. Everything seed_demo_data.py
would add, and everything a real user adds through the app (payers, bills,
payments, receipts, consultants, agents, settlements, audit history), gets
deleted.

Deletion order matters — several FKs are PROTECT, not CASCADE, so children
must be cleared before the parents they'd otherwise block:
  Payment (before Bill, POSTerminal) -> cascades Receipt
  Bill (before Payer) -> cascades BillLine, DebtCase
  CommissionSettlement, POSTerminal (before SubConsultant/FieldAgent)
  Payer (after Bill) -> cascades Assessment, EnumeratedAsset
  non-admin AppUser (after everything above) -> cascades FieldAgent
  SubConsultant (last — blocked until the AppUsers pointing at it are gone)

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
from apps.settlements.models import CommissionSettlement
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council


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
            "--yes", action="store_true",
            help="Skip the confirmation prompt (needed for non-interactive use).",
        )

    def handle(self, *args, **options):
        try:
            council = Council.objects.get(council_code=options["council_code"])
        except Council.DoesNotExist:
            raise CommandError(f"No council with code {options['council_code']!r}")

        if not options["yes"]:
            confirm = input(
                f"This will permanently delete ALL payers, bills, payments, receipts, "
                f"consultants, agents, settlements and audit history for {council.council_code}. "
                f"The council, wards, revenue-item catalog/rates, and the admin login are kept.\n"
                f"Type the council code ({council.council_code}) to confirm: "
            )
            if confirm != council.council_code:
                self.stdout.write(self.style.WARNING("Aborted — confirmation did not match."))
                return

        with transaction.atomic():
            set_council_context(council.id)
            self._clear(council)

        self.stdout.write(self.style.SUCCESS(
            f"{council.council_code} reset — onboarding baseline kept, all transactional/demo data cleared."
        ))

    def _clear(self, council):
        def wipe(qs, label):
            count = qs.count()
            qs.delete()
            self.stdout.write(f"  {label}: {count} deleted")

        wipe(AuditLog.objects.filter(council=council), "Audit log entries")
        wipe(ReconciliationException.objects.filter(council=council), "Reconciliation exceptions")
        wipe(ReconciliationRun.objects.filter(council=council), "Reconciliation runs")
        wipe(ChannelTransactionFeed.objects.filter(council=council), "Bank feed rows")
        wipe(MobileSyncRecord.objects.filter(council=council), "Mobile sync records")

        wipe(Payment.objects.filter(council=council), "Payments (+ receipts)")
        wipe(Bill.objects.filter(council=council), "Bills (+ lines, debt cases)")
        wipe(CommissionSettlement.objects.filter(council=council), "Commission settlements")
        wipe(POSTerminal.objects.filter(council=council), "POS terminals")
        wipe(APIClient.objects.filter(council=council), "API clients")
        wipe(Payer.objects.filter(council=council), "Payers (+ assessments, assets)")

        wipe(
            AppUser.objects.filter(
                council=council,
                role__access_level__in=[AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW],
            ),
            "Non-admin logins (consultants, agents, stakeholders — cascades field agents)",
        )
        wipe(SubConsultant.objects.filter(council=council), "Sub-consultants")
