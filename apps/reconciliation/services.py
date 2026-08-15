from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.audit.services import audit
from apps.payments.models import ChannelTransactionFeed, Payment
from apps.reconciliation.models import ReconciliationException, ReconciliationRun


@transaction.atomic
def run_reconciliation(*, council_id, channel, run_date, actor) -> ReconciliationRun:
    """Matches platform payment records against the bank's transaction feed for one
    channel on one day. Real bank integration is simulated (see PRD.md §5); this is
    the matching pass that runs once real feeds land. Safe to re-run for the same
    day — exceptions are recomputed from scratch each time."""
    payments = Payment.objects.filter(
        council_id=council_id, channel=channel, txn_status=Payment.CONFIRMED, created_at__date=run_date
    )
    total_platform = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    feed_rows = ChannelTransactionFeed.objects.filter(council_id=council_id, channel=channel, received_at__date=run_date)
    total_bank = feed_rows.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    run, _created = ReconciliationRun.objects.update_or_create(
        council_id=council_id,
        channel=channel,
        run_date=run_date,
        defaults={"total_platform": total_platform, "total_bank": total_bank, "run_by": actor, "status": ReconciliationRun.OPEN},
    )
    run.exceptions.all().delete()

    exceptions_created = 0
    for row in feed_rows:
        match = payments.filter(bank_txn_ref=row.bank_txn_ref, amount=row.amount).first()
        if match:
            row.match_status = ChannelTransactionFeed.MATCHED
            row.matched_payment = match
            row.save(update_fields=["match_status", "matched_payment"])
        else:
            row.match_status = ChannelTransactionFeed.EXCEPTION
            row.save(update_fields=["match_status"])
            ReconciliationException.objects.create(
                council_id=council_id, run=run, feed_row=row, note="No matching platform payment found for this bank reference/amount"
            )
            exceptions_created += 1

    run.status = ReconciliationRun.EXCEPTIONS if exceptions_created else ReconciliationRun.BALANCED
    run.save(update_fields=["status"])

    audit(
        council_id=council_id, actor=actor, action="RECONCILIATION_RUN", entity_type="RECONCILIATION_RUN", entity_id=run.id,
        detail={"channel": channel.code, "run_date": str(run_date), "status": run.status, "exceptions": exceptions_created},
    )
    return run
