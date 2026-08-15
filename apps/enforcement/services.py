from django.db import transaction
from django.utils import timezone

from apps.audit.services import audit
from apps.billing.models import Bill
from apps.enforcement.models import DebtCase


def close_debt_case_for_bill(bill: Bill) -> None:
    """A case auto-closes the moment its bill is fully paid — or superseded by an
    arrears consolidation — regardless of what stage it was at. See TDD.md §4.4a."""
    case = getattr(bill, "debt_case", None)
    if case and case.enforcement_stage != DebtCase.CLOSED:
        case.enforcement_stage = DebtCase.CLOSED
        case.closed_at = timezone.now()
        case.save(update_fields=["enforcement_stage", "closed_at", "updated_at"])


def _bucket_for(days_overdue: int) -> str:
    if days_overdue <= 30:
        return DebtCase.BUCKET_0_30
    if days_overdue <= 60:
        return DebtCase.BUCKET_31_60
    if days_overdue <= 90:
        return DebtCase.BUCKET_61_90
    return DebtCase.BUCKET_OVER_90


@transaction.atomic
def refresh_debt(*, council_id, actor) -> dict:
    """Re-ages overdue bills and opens cases — replaces the prototype's manual
    'Refresh Ageing' button (scheduled via Celery in production, see
    V2_ARCHITECTURE.md §9)."""
    today = timezone.localdate()
    overdue_bills = Bill.objects.filter(
        council_id=council_id, status__in=[Bill.ISSUED, Bill.PART_PAID, Bill.OVERDUE], due_date__lt=today
    )

    opened, updated = 0, 0
    for bill in overdue_bills:
        if bill.status != Bill.OVERDUE:
            bill.status = Bill.OVERDUE
            bill.save(update_fields=["status", "updated_at"])

        days_overdue = (today - bill.due_date).days
        bucket = _bucket_for(days_overdue)
        case = getattr(bill, "debt_case", None)
        if case is None:
            DebtCase.objects.create(council_id=council_id, bill=bill, ageing_bucket=bucket)
            opened += 1
        elif case.ageing_bucket != bucket and case.enforcement_stage != DebtCase.CLOSED:
            case.ageing_bucket = bucket
            case.save(update_fields=["ageing_bucket", "updated_at"])
            updated += 1

    audit(council_id=council_id, actor=actor, action="DEBT_REFRESHED", entity_type="COUNCIL", entity_id=council_id, detail={"opened": opened, "updated": updated})
    return {"opened": opened, "updated": updated}


@transaction.atomic
def escalate(*, debt_case: DebtCase, actor):
    ladder = DebtCase.ENFORCEMENT_LADDER
    idx = ladder.index(debt_case.enforcement_stage)
    if idx >= len(ladder) - 1:
        raise ValueError("Debt case is already at the final stage")
    old_stage = debt_case.enforcement_stage
    debt_case.enforcement_stage = ladder[idx + 1]
    debt_case.reminder_count += 1
    if debt_case.enforcement_stage == DebtCase.CLOSED:
        debt_case.closed_at = timezone.now()
    debt_case.save(update_fields=["enforcement_stage", "reminder_count", "closed_at", "updated_at"])
    audit(
        council_id=debt_case.council_id, actor=actor, action="DEBT_ESCALATED", entity_type="DEBT_CASE", entity_id=debt_case.id,
        detail={"old_stage": old_stage, "new_stage": debt_case.enforcement_stage},
    )
    return debt_case
