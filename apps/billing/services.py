import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.services import audit
from apps.billing.models import Assessment, Bill, BillLine
from apps.common.refs import finalize_ref, placeholder_ref
from apps.revenue.models import RateBand


class BillingError(Exception):
    """Raised for invalid billing operations — callers map this to a 400."""


class DuplicateBill(Exception):
    """Raised when a payer already has a non-terminal bill for the calendar
    year — callers map this to a 409 carrying `existing`'s id/reference,
    same warn-then-force contract as apps.registry.services.DuplicatePayer."""

    def __init__(self, existing: Bill):
        self.existing = existing
        super().__init__(f"{existing.payer} already has an active bill this year: {existing.bill_ref}")


def create_draft_assessment(
    *, payer, council_revenue_item, actor, quantity=1, asset=None, rate_band=None, rate_tier=None, amount_override=None
):
    """Prices one assessment against whichever pricing source the item currently
    uses. An item with open `RateBand`s *requires* `rate_band` — see
    `CouncilRevenueItem.active_bands` — and validates it server-side rather than
    trusting the client's arithmetic, same discipline as every other money path
    in this codebase:

    - band.rate_mode == FLAT: charges band.flat_amount, no further input needed.
    - band.rate_mode == RANGE: charges `amount_override`, which must fall within
      [band.min_amount, band.max_amount] — rejected otherwise.
    - band.rate_mode == TIERED: charges `rate_tier.amount`; `rate_tier` must
      belong to the chosen `rate_band`.

    An item with no open bands prices from its plain `RateSchedule`, unchanged
    from before banding existed.
    """
    active_bands = list(council_revenue_item.active_bands)

    if active_bands:
        if rate_band is None or rate_band.council_revenue_item_id != council_revenue_item.id or rate_band.effective_to is not None:
            raise BillingError(f"{council_revenue_item.harmonised_code} requires selecting a rate band")

        if rate_band.rate_mode == RateBand.FLAT:
            unit_amount = rate_band.flat_amount
        elif rate_band.rate_mode == RateBand.RANGE:
            if amount_override is None:
                raise BillingError(f"Enter a chargeable amount for '{rate_band.label}'")
            if not (rate_band.min_amount <= amount_override <= rate_band.max_amount):
                raise BillingError(
                    f"'{rate_band.label}' must be charged between {rate_band.min_amount} and {rate_band.max_amount}"
                )
            unit_amount = amount_override
            rate_tier = None
        elif rate_band.rate_mode == RateBand.TIERED:
            if rate_tier is None or rate_tier.band_id != rate_band.id:
                raise BillingError(f"Select a valid tier for '{rate_band.label}'")
            unit_amount = rate_tier.amount
        else:  # pragma: no cover — exhaustive over RateBand.RATE_MODE_CHOICES
            raise BillingError(f"Unknown rate mode '{rate_band.rate_mode}'")

        amount = unit_amount * quantity
        return Assessment.objects.create(
            council_id=payer.council_id,
            payer=payer,
            council_revenue_item=council_revenue_item,
            rate_band=rate_band,
            rate_tier=rate_tier,
            asset=asset,
            quantity=quantity,
            amount=amount,
            status=Assessment.DRAFT,
            created_by=actor,
        )

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
    """Re-derives total_amount and status after any line edit or payment. See
    TDD.md §4.4.

    arrears now live on the lines themselves (BillLine.arrears_amount) rather
    than as one bill-level lump sum, so bill.arrears_amount is a derived
    display rollup here — sum(line.line_amount) alone is the true total, not
    that plus arrears_amount again (that field's value is already folded into
    each line's line_amount, see issue_bill(roll_arrears=True))."""
    lines = list(bill.lines.all())
    bill.arrears_amount = sum((line.arrears_amount for line in lines), start=Decimal("0"))
    bill.total_amount = sum((line.line_amount for line in lines), start=Decimal("0"))

    if bill.status not in Bill.TERMINAL_STATUSES:
        if bill.amount_paid >= bill.total_amount and bill.total_amount > 0:
            bill.status = Bill.PAID
        elif bill.due_date < timezone.localdate():
            bill.status = Bill.OVERDUE
        elif bill.amount_paid > 0:
            bill.status = Bill.PART_PAID
        else:
            bill.status = Bill.ISSUED

    bill.save(update_fields=["total_amount", "arrears_amount", "status", "updated_at"])
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
    force=False,
):
    """Three ways to build a bill, combinable — see API_REFERENCE.md 'Assessment &
    billing'. A bill built with only roll_arrears and no lines/drafts is a valid
    pure consolidation.

    Each revenue item is billed yearly, so a payer should only ever have one
    non-terminal bill per calendar year — new charges normally get added to
    that bill via add_bill_line(), not issued as a second one. roll_arrears
    calls are exempt from this check: consolidation is the sanctioned way to
    issue a bill while a prior one is still open, and it immediately
    supersedes that prior bill itself. Otherwise, an existing non-terminal
    bill for the same payer+year raises DuplicateBill unless force=True, in
    which case the bill is issued anyway and the override is audited.
    """
    from apps.enforcement.services import close_debt_case_for_bill

    bypassed_duplicate = None
    if not roll_arrears:
        existing = (
            Bill.objects.filter(payer=payer, created_at__year=timezone.localdate().year)
            .exclude(status__in=Bill.TERMINAL_STATUSES)
            .first()
        )
        if existing is not None:
            if not force:
                raise DuplicateBill(existing)
            bypassed_duplicate = existing

    lines = lines or []

    assessments = []
    for entry in lines:
        assessment = create_draft_assessment(
            payer=payer,
            council_revenue_item=entry["council_revenue_item"],
            actor=actor,
            quantity=entry.get("quantity", 1),
            rate_band=entry.get("rate_band"),
            rate_tier=entry.get("rate_tier"),
            amount_override=entry.get("amount_override"),
        )
        assessments.append(assessment)

    if bill_all_drafts:
        drafts = Assessment.objects.filter(payer=payer, status=Assessment.DRAFT).exclude(
            id__in=[a.id for a in assessments]
        )
        assessments.extend(drafts)

    # Two assessments landing on this same new bill for the exact same (item,
    # band, tier) — whether both came from explicit lines, or one from a
    # line and one swept in via bill_all_drafts — become one billed line
    # with combined quantity/amount, not two visibly duplicate rows. See
    # add_bill_line's identical merge for an already-issued bill.
    merged: dict[tuple, Assessment] = {}
    superseded = []
    for assessment in assessments:
        key = (assessment.council_revenue_item_id, assessment.rate_band_id, assessment.rate_tier_id)
        kept = merged.get(key)
        if kept is None:
            merged[key] = assessment
        else:
            kept.quantity += assessment.quantity
            kept.amount += assessment.amount
            superseded.append(assessment)
    assessments = list(merged.values())
    for assessment in assessments:
        assessment.save(update_fields=["quantity", "amount"])
    for assessment in superseded:
        assessment.status = Assessment.CANCELLED
        assessment.save(update_fields=["status"])

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

    next_position = 0
    fresh_lines_by_item: dict[int, BillLine] = {}
    for assessment in assessments:
        line = BillLine.objects.create(
            bill=bill, assessment=assessment, line_amount=assessment.amount,
            current_amount=assessment.amount, arrears_amount=0, position=next_position,
        )
        next_position += 1
        # First fresh line wins if two lines somehow land on the same item under
        # different bands/tiers (see the merge dict above — that only collapses
        # identical (item, band, tier) triples, so this can still happen).
        fresh_lines_by_item.setdefault(assessment.council_revenue_item_id, line)
        assessment.status = Assessment.BILLED
        assessment.save(update_fields=["status"])

    superseded_count = 0
    if roll_arrears:
        open_bills = (
            Bill.objects.select_for_update()
            .filter(payer=payer, status__in=[Bill.ISSUED, Bill.PART_PAID, Bill.OVERDUE])
            .exclude(id=bill.id)
        )
        # Itemized by revenue item, not one bill-level lump sum: an item with
        # both a current-cycle charge and carried arrears prints as ONE line
        # with both columns, not two rows (see BillLine docstring). Summing
        # each prior line's own outstanding balance (line_amount - paid_amount,
        # paid_amount from FIFO PaymentAllocation) rather than the old bill's
        # total balance is what makes this itemization possible at all.
        arrears_by_item: dict[int, Decimal] = {}
        representative_assessment: dict[int, Assessment] = {}
        for prior in open_bills:
            prior.status = Bill.SUPERSEDED
            prior.superseded_by = bill
            prior.save(update_fields=["status", "superseded_by", "updated_at"])
            close_debt_case_for_bill(prior)
            superseded_count += 1
            for prior_line in prior.lines.all():
                outstanding = prior_line.line_amount - prior_line.paid_amount
                if outstanding <= 0:
                    continue
                item_id = prior_line.assessment.council_revenue_item_id
                arrears_by_item[item_id] = arrears_by_item.get(item_id, 0) + outstanding
                representative_assessment[item_id] = prior_line.assessment

        for item_id, arrears_amt in arrears_by_item.items():
            existing_line = fresh_lines_by_item.get(item_id)
            if existing_line is not None:
                existing_line.arrears_amount += arrears_amt
                existing_line.line_amount = existing_line.current_amount + existing_line.arrears_amount
                existing_line.save(update_fields=["arrears_amount", "line_amount"])
            else:
                BillLine.objects.create(
                    bill=bill, assessment=representative_assessment[item_id],
                    current_amount=0, arrears_amount=arrears_amt, line_amount=arrears_amt,
                    position=next_position,
                )
                next_position += 1

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
    if bypassed_duplicate is not None:
        audit(
            council_id=council_id, actor=actor, action="BILL_ISSUED_FORCED_DUPLICATE",
            entity_type="BILL", entity_id=bill.id,
            detail={"bypassed_bill_id": bypassed_duplicate.id, "bypassed_bill_ref": bypassed_duplicate.bill_ref},
        )
    bill.superseded_count = superseded_count
    return bill


@transaction.atomic
def add_bill_line(*, bill, council_revenue_item, quantity, actor, rate_band=None, rate_tier=None, amount_override=None):
    assessment = create_draft_assessment(
        payer=bill.payer,
        council_revenue_item=council_revenue_item,
        actor=actor,
        quantity=quantity,
        rate_band=rate_band,
        rate_tier=rate_tier,
        amount_override=amount_override,
    )

    # Same item already on this bill under the exact same band/tier (a
    # different band/tier for the same item is a genuinely distinct charge —
    # e.g. two different Liquor Licensing establishment types — and is never
    # merged) folds into that existing line instead of creating a visibly
    # duplicate row. The assessment just created above is superseded, not
    # deleted — same "cancel, don't erase" discipline as delete_bill_line.
    existing_line = bill.lines.filter(
        assessment__council_revenue_item=council_revenue_item, assessment__rate_band=rate_band, assessment__rate_tier=rate_tier,
    ).first()
    if existing_line is not None:
        existing_assessment = existing_line.assessment
        existing_assessment.quantity += assessment.quantity
        existing_assessment.amount += assessment.amount
        existing_assessment.save(update_fields=["quantity", "amount"])
        existing_line.current_amount = existing_assessment.amount
        existing_line.line_amount = existing_line.current_amount + existing_line.arrears_amount
        existing_line.save(update_fields=["current_amount", "line_amount"])

        assessment.status = Assessment.CANCELLED
        assessment.save(update_fields=["status"])

        recompute_bill(bill)
        audit(
            council_id=bill.council_id, actor=actor, action="BILL_LINE_MERGED", entity_type="BILL", entity_id=bill.id,
            detail={"line_id": existing_line.id, "added_quantity": str(assessment.quantity), "added_amount": str(assessment.amount), "new_line_amount": str(existing_line.line_amount)},
        )
        return existing_line

    assessment.status = Assessment.BILLED
    assessment.save(update_fields=["status"])
    next_position = (bill.lines.aggregate(m=Max("position"))["m"] or -1) + 1
    line = BillLine.objects.create(
        bill=bill, assessment=assessment, line_amount=assessment.amount,
        current_amount=assessment.amount, arrears_amount=0, position=next_position,
    )
    recompute_bill(bill)
    audit(council_id=bill.council_id, actor=actor, action="BILL_LINE_ADDED", entity_type="BILL", entity_id=bill.id, detail={"line_id": line.id, "amount": str(line.line_amount)})
    return line


@transaction.atomic
def update_bill_line(*, line: BillLine, line_amount, actor):
    """line_amount replaces the line's total; the arrears portion carried on
    it (if any, from roll_arrears) is preserved and current_amount absorbs
    the rest, matching how the split is created in the first place."""
    old_amount = line.line_amount
    line.current_amount = line_amount - line.arrears_amount
    line.line_amount = line_amount
    line.save(update_fields=["current_amount", "line_amount"])
    line.assessment.amount = line.current_amount
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
