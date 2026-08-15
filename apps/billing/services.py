import datetime

from django.db import transaction
from django.utils import timezone

from apps.audit.services import audit
from apps.billing.models import Assessment, Bill, BillLine
from apps.common.refs import finalize_ref, placeholder_ref


class BillingError(Exception):
    """Raised for invalid billing operations — callers map this to a 400."""


def create_draft_assessment(*, payer, council_revenue_item, actor, quantity=1, asset=None):
    rate = council_revenue_item.current_rate
    if rate is None:
        raise BillingError(f"{council_revenue_item.harmonised_code} has no active rate")
    amount = rate.rate_amount * quantity
    return Assessment.objects.create(
        council_id=payer.council_id,
        payer=payer,
        council_revenue_item=council_revenue_item,
        rate_schedule=rate,
        asset=asset,
        quantity=quantity,
        amount=amount,
        status=Assessment.DRAFT,
        created_by=actor,
    )


def recompute_bill(bill: Bill) -> Bill:
    """Re-derives total_amount (billed lines + arrears) and status after any line
    edit or payment. See TDD.md §4.4."""
    line_total = sum((line.line_amount for line in bill.lines.all()), start=0)
    bill.total_amount = line_total + bill.arrears_amount

    if bill.status not in Bill.TERMINAL_STATUSES:
        if bill.amount_paid >= bill.total_amount and bill.total_amount > 0:
            bill.status = Bill.PAID
        elif bill.due_date < timezone.localdate():
            bill.status = Bill.OVERDUE
        elif bill.amount_paid > 0:
            bill.status = Bill.PART_PAID
        else:
            bill.status = Bill.ISSUED

    bill.save(update_fields=["total_amount", "status", "updated_at"])
    return bill


def _next_bill_ref(bill: Bill) -> str:
    config = bill.council.config
    year = timezone.localdate().year
    return f"{config.bill_ref_prefix}/{year}/{bill.id:06d}"


@transaction.atomic
def issue_bill(
    *,
    council_id,
    payer,
    due_date=None,
    lines=None,
    bill_all_drafts=False,
    roll_arrears=False,
    actor,
):
    """Three ways to build a bill, combinable — see API_REFERENCE.md 'Assessment &
    billing'. A bill built with only roll_arrears and no lines/drafts is a valid
    pure consolidation."""
    from apps.enforcement.services import close_debt_case_for_bill

    lines = lines or []

    assessments = []
    for entry in lines:
        assessment = create_draft_assessment(
            payer=payer,
            council_revenue_item=entry["council_revenue_item"],
            actor=actor,
            quantity=entry.get("quantity", 1),
        )
        assessments.append(assessment)

    if bill_all_drafts:
        drafts = Assessment.objects.filter(payer=payer, status=Assessment.DRAFT).exclude(
            id__in=[a.id for a in assessments]
        )
        assessments.extend(drafts)

    if not assessments and not roll_arrears:
        raise BillingError("A bill needs at least one line, bill_all_drafts, or roll_arrears")

    if due_date is None:
        due_date = timezone.localdate() + datetime.timedelta(days=payer.council.config.bill_due_days)

    bill = Bill.objects.create(
        council_id=council_id,
        bill_ref=placeholder_ref(),
        payer=payer,
        due_date=due_date,
        status=Bill.ISSUED,
        issued_by=actor,
    )

    for assessment in assessments:
        BillLine.objects.create(bill=bill, assessment=assessment, line_amount=assessment.amount)
        assessment.status = Assessment.BILLED
        assessment.save(update_fields=["status"])

    superseded_count = 0
    if roll_arrears:
        open_bills = (
            Bill.objects.select_for_update()
            .filter(payer=payer, status__in=[Bill.ISSUED, Bill.PART_PAID, Bill.OVERDUE])
            .exclude(id=bill.id)
        )
        arrears_total = 0
        for prior in open_bills:
            arrears_total += prior.balance
            prior.status = Bill.SUPERSEDED
            prior.superseded_by = bill
            prior.save(update_fields=["status", "superseded_by", "updated_at"])
            close_debt_case_for_bill(prior)
            superseded_count += 1
        bill.arrears_amount = arrears_total
        bill.save(update_fields=["arrears_amount"])

    finalize_ref(bill, "bill_ref", _next_bill_ref(bill))
    recompute_bill(bill)

    audit(
        council_id=council_id,
        actor=actor,
        action="BILL_ISSUED",
        entity_type="BILL",
        entity_id=bill.id,
        detail={"bill_ref": bill.bill_ref, "total_amount": str(bill.total_amount), "superseded_count": superseded_count},
    )
    bill.superseded_count = superseded_count
    return bill


@transaction.atomic
def add_bill_line(*, bill, council_revenue_item, quantity, actor):
    assessment = create_draft_assessment(payer=bill.payer, council_revenue_item=council_revenue_item, actor=actor, quantity=quantity)
    assessment.status = Assessment.BILLED
    assessment.save(update_fields=["status"])
    line = BillLine.objects.create(bill=bill, assessment=assessment, line_amount=assessment.amount)
    recompute_bill(bill)
    audit(council_id=bill.council_id, actor=actor, action="BILL_LINE_ADDED", entity_type="BILL", entity_id=bill.id, detail={"line_id": line.id, "amount": str(line.line_amount)})
    return line


@transaction.atomic
def update_bill_line(*, line: BillLine, line_amount, actor):
    old_amount = line.line_amount
    line.line_amount = line_amount
    line.save(update_fields=["line_amount"])
    line.assessment.amount = line_amount
    line.assessment.save(update_fields=["amount"])
    recompute_bill(line.bill)
    audit(
        council_id=line.bill.council_id, actor=actor, action="BILL_LINE_EDITED", entity_type="BILL", entity_id=line.bill_id,
        detail={"line_id": line.id, "old_amount": str(old_amount), "new_amount": str(line_amount)},
    )
    return line


@transaction.atomic
def delete_bill_line(*, line: BillLine, actor):
    bill = line.bill
    if bill.lines.count() <= 1:
        raise BillingError("Cannot delete a bill's last remaining line — cancel the bill instead")
    line_id = line.id
    line.assessment.status = Assessment.CANCELLED
    line.assessment.save(update_fields=["status"])
    line.delete()
    recompute_bill(bill)
    audit(council_id=bill.council_id, actor=actor, action="BILL_LINE_DELETED", entity_type="BILL", entity_id=bill.id, detail={"line_id": line_id})
