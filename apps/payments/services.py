from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum

from apps.audit.services import audit
from apps.billing.models import Bill
from apps.billing.services import recompute_bill
from apps.common.refs import finalize_ref, placeholder_ref
from apps.payments.models import POSTerminal, Payment, PaymentAllocation, PaymentChannel, Receipt


class PaymentRejected(Exception):
    """Raised when a bill can't take a payment — terminal-state refusal, the one
    place this is enforced so every channel is covered. See V2_ARCHITECTURE.md §7.2."""


def _allocate_fifo(*, payment: Payment, bill: Bill) -> tuple[Decimal, Decimal]:
    """Applies payment.amount to bill.lines oldest-first, filling each line's
    outstanding balance before moving to the next. Returns (applied, leftover)
    — leftover is whatever's left once every line is fully paid (an
    overpayment), 0 otherwise. select_for_update on the lines guards against
    two concurrent payments double-spending the same outstanding balance.

    Paid-so-far per line is looked up via one grouped query up front rather
    than BillLine.paid_amount's per-line aggregate in the loop — Postgres
    rejects FOR UPDATE combined with GROUP BY/aggregates in the same query,
    so the lock query and the paid-amount query have to stay separate calls
    regardless; batching the latter into one query (instead of one per line)
    is what actually matters for a bill with many lines, on every payment
    posted through any channel."""
    lines = list(bill.lines.select_for_update().order_by("position", "id"))
    paid_by_line = dict(
        PaymentAllocation.objects.filter(bill_line__in=lines, payment__txn_status=Payment.CONFIRMED)
        .values("bill_line_id")
        .annotate(total=Sum("amount"))
        .values_list("bill_line_id", "total")
    )

    remaining = payment.amount
    applied = Decimal("0")
    for line in lines:
        if remaining <= 0:
            break
        outstanding = line.line_amount - paid_by_line.get(line.id, Decimal("0"))
        if outstanding <= 0:
            continue
        take = min(remaining, outstanding)
        PaymentAllocation.objects.create(payment=payment, bill_line=line, amount=take)
        remaining -= take
        applied += take
    return applied, remaining


@transaction.atomic
def post_payment(
    *,
    council_id,
    bill: Bill,
    channel: PaymentChannel,
    amount,
    bank_txn_ref="",
    posted_by=None,
    geo=None,
    terminal: POSTerminal | None = None,
) -> Payment:
    """The single money-in path every channel — POS, teller, transfer, USSD, agent
    banking, portal entry, offline sync replay — funnels through. See
    V2_ARCHITECTURE.md §7.1."""
    if bill.status == Bill.SUPERSEDED:
        target = bill.superseded_by.bill_ref if bill.superseded_by else "the consolidated bill"
        raise PaymentRejected(f"{bill.bill_ref} has been superseded — collect against {target} instead")
    if bill.status == Bill.CANCELLED:
        raise PaymentRejected(f"{bill.bill_ref} is cancelled and cannot take a payment")

    geo = geo or {}
    payment = Payment.objects.create(
        council_id=council_id,
        payment_ref=placeholder_ref(),
        bill=bill,
        channel=channel,
        terminal=terminal,
        amount=amount,
        bank_txn_ref=bank_txn_ref,
        txn_status=Payment.CONFIRMED,
        posted_by=posted_by,
        geo_lat=geo.get("lat"),
        geo_lng=geo.get("lng"),
    )
    finalize_ref(payment, "payment_ref", f"PAY-{payment.id:08d}")

    applied, leftover = _allocate_fifo(payment=payment, bill=bill)

    bill.amount_paid = bill.amount_paid + applied
    bill.save(update_fields=["amount_paid", "updated_at"])
    recompute_bill(bill)

    if leftover > 0:
        from apps.registry.models import Payer

        Payer.objects.filter(pk=bill.payer_id).update(credit_balance=F("credit_balance") + leftover)
        audit(
            council_id=council_id, actor=posted_by, action="PAYMENT_OVERPAYMENT_CREDITED",
            entity_type="PAYER", entity_id=bill.payer_id,
            detail={"payment_ref": payment.payment_ref, "bill_ref": bill.bill_ref, "credited_amount": str(leftover)},
        )

    receipt = Receipt.objects.create(council_id=council_id, receipt_ref=placeholder_ref(), payment=payment)
    finalize_ref(receipt, "receipt_ref", f"RCT-{receipt.id:08d}")

    if bill.status == Bill.PAID:
        from apps.enforcement.services import close_debt_case_for_bill

        close_debt_case_for_bill(bill)

    audit(
        council_id=council_id,
        actor=posted_by,
        action="PAYMENT_POSTED",
        entity_type="PAYMENT",
        entity_id=payment.id,
        detail={"payment_ref": payment.payment_ref, "bill_ref": bill.bill_ref, "amount": str(amount), "channel": channel.code},
    )
    return payment


@transaction.atomic
def reverse_payment(*, payment: Payment, actor, reason="") -> Payment:
    """The only way to correct a mis-recorded payment — there was previously no
    way to do this at all short of a raw negative-amount POST /payments, which
    is now rejected (PostPaymentSerializer.amount has min_value=0.01). Marks the
    payment REVERSED rather than deleting it, so the original record and its
    receipt stay in the audit trail; the bill's amount_paid/status are
    recomputed as if the payment had never landed.

    Since FIFO allocation (_allocate_fifo), payment.amount is no longer what
    landed on the bill — post_payment() only credits bill.amount_paid with
    the applied portion, routing any overpayment leftover to
    Payer.credit_balance instead. Undoing must mirror that split exactly:
    subtract only the applied portion from amount_paid (deriving it from this
    payment's own PaymentAllocation rows, which is exactly how much of it was
    applied — no separate bookkeeping needed), and claw back any leftover
    credit. Assumes the leftover hasn't since been spent — correct today,
    since nothing consumes credit_balance yet (see Payer.credit_balance)."""
    if payment.txn_status != Payment.CONFIRMED:
        raise PaymentRejected(f"{payment.payment_ref} is {payment.txn_status.lower()}, not confirmed — nothing to reverse")

    bill = payment.bill
    applied = payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    leftover = payment.amount - applied

    payment.txn_status = Payment.REVERSED
    payment.save(update_fields=["txn_status"])

    bill.amount_paid = max(bill.amount_paid - applied, 0)
    bill.save(update_fields=["amount_paid", "updated_at"])
    recompute_bill(bill)

    if leftover > 0:
        from apps.registry.models import Payer

        Payer.objects.filter(pk=bill.payer_id).update(credit_balance=F("credit_balance") - leftover)
        audit(
            council_id=payment.council_id, actor=actor, action="PAYMENT_OVERPAYMENT_CREDIT_CLAWED_BACK",
            entity_type="PAYER", entity_id=bill.payer_id,
            detail={"payment_ref": payment.payment_ref, "bill_ref": bill.bill_ref, "clawed_back_amount": str(leftover)},
        )

    audit(
        council_id=payment.council_id,
        actor=actor,
        action="PAYMENT_REVERSED",
        entity_type="PAYMENT",
        entity_id=payment.id,
        detail={
            "payment_ref": payment.payment_ref, "bill_ref": bill.bill_ref, "amount": str(payment.amount),
            "applied_amount": str(applied), "reason": reason,
        },
    )
    return payment
