from django.db import transaction

from apps.audit.services import audit
from apps.billing.models import Bill
from apps.billing.services import recompute_bill
from apps.common.refs import finalize_ref, placeholder_ref
from apps.payments.models import POSTerminal, Payment, PaymentChannel, Receipt


class PaymentRejected(Exception):
    """Raised when a bill can't take a payment — terminal-state refusal, the one
    place this is enforced so every channel is covered. See V2_ARCHITECTURE.md §7.2."""


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

    bill.amount_paid = bill.amount_paid + amount
    bill.save(update_fields=["amount_paid", "updated_at"])
    recompute_bill(bill)

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
