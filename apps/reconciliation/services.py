from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import audit
from apps.payments.models import ChannelTransactionFeed, Payment, PaymentChannel
from apps.reconciliation.models import ReconciliationException, ReconciliationRun


class ReconciliationError(Exception):
    pass


def _match_feed_rows_for_channel(*, council_id, channel, run_date):
    """The one matching rule: a bank feed row is matched if there's a platform
    Payment with the exact same bank_txn_ref and amount. The core building
    block both `_match_feed_rows` (adds the platform-side total, for
    run_reconciliation) and `live_reconciliation_summary` (which computes its
    own all-channel platform total once, not per channel — see there) share,
    so "matched" has one definition, not two that can quietly drift apart.

    Returns (total_bank, matches, unmatched_rows) where `matches` is
    {feed_row_id: Payment | None} and `unmatched_rows` is the
    ChannelTransactionFeed rows with no match.
    """
    payments = Payment.objects.filter(
        council_id=council_id, channel=channel, txn_status=Payment.CONFIRMED, created_at__date=run_date
    )
    feed_rows = list(
        ChannelTransactionFeed.objects.filter(council_id=council_id, channel=channel, received_at__date=run_date)
    )
    total_bank = sum((row.amount for row in feed_rows), start=Decimal("0"))

    matches: dict[int, Payment | None] = {}
    unmatched_rows = []
    for row in feed_rows:
        match = payments.filter(bank_txn_ref=row.bank_txn_ref, amount=row.amount).first()
        matches[row.id] = match
        if match is None:
            unmatched_rows.append(row)

    return total_bank, matches, unmatched_rows


def _match_feed_rows(*, council_id, channel, run_date):
    """Adds the platform-side total to `_match_feed_rows_for_channel`, for
    run_reconciliation's own totals. Returns (total_platform, total_bank,
    matches, unmatched_rows)."""
    total_platform = (
        Payment.objects.filter(
            council_id=council_id, channel=channel, txn_status=Payment.CONFIRMED, created_at__date=run_date
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    total_bank, matches, unmatched_rows = _match_feed_rows_for_channel(
        council_id=council_id, channel=channel, run_date=run_date
    )
    return total_platform, total_bank, matches, unmatched_rows


@transaction.atomic
def run_reconciliation(*, council_id, channel, run_date, actor) -> ReconciliationRun:
    """Matches platform payment records against the bank's transaction feed for one
    channel on one day. Real bank integration is simulated (see PRD.md §5); this is
    the matching pass that runs once real feeds land. Safe to re-run for the same
    day — exceptions are recomputed from scratch each time."""
    if channel.code in PaymentChannel.NO_FEED_EXPECTED:
        raise ReconciliationError(
            f"{channel.code} has no bank-side feed to reconcile against and cannot be run."
        )

    total_platform, total_bank, matches, _unmatched = _match_feed_rows(
        council_id=council_id, channel=channel, run_date=run_date
    )

    run, _created = ReconciliationRun.objects.update_or_create(
        council_id=council_id,
        channel=channel,
        run_date=run_date,
        defaults={"total_platform": total_platform, "total_bank": total_bank, "run_by": actor, "status": ReconciliationRun.OPEN},
    )
    run.exceptions.all().delete()

    feed_rows = ChannelTransactionFeed.objects.filter(council_id=council_id, channel=channel, received_at__date=run_date)
    exceptions_created = 0
    for row in feed_rows:
        match = matches.get(row.id)
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


def live_reconciliation_summary(*, council_id, run_date=None) -> dict:
    """Always-current dashboard view, computed fresh on every call — no
    ReconciliationRun/Exception rows are read or written here, so this never
    interferes with (or requires) a manual run. total_platform is ALL
    confirmed payments for the period regardless of channel (what we should
    have overall); total_bank and unmatched_credits are per-channel, over
    every channel with a bank feed to speak of (NO_FEED_EXPECTED channels
    like CASH never have feed rows, so including them would be a no-op, but
    skipping them is cheaper and documents the exclusion explicitly)."""
    run_date = run_date or timezone.localdate()

    total_platform = (
        Payment.objects.filter(council_id=council_id, txn_status=Payment.CONFIRMED, created_at__date=run_date).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0")
    )

    total_bank = Decimal("0")
    unmatched_credits = []
    channels = PaymentChannel.objects.exclude(code__in=PaymentChannel.NO_FEED_EXPECTED)
    for channel in channels:
        channel_bank_total, _matches, unmatched_rows = _match_feed_rows_for_channel(
            council_id=council_id, channel=channel, run_date=run_date
        )
        total_bank += channel_bank_total
        unmatched_credits.extend(unmatched_rows)

    return {
        "run_date": run_date,
        "total_platform": total_platform,
        "total_bank": total_bank,
        "unmatched_credits": unmatched_credits,
    }
