import datetime

from django.db import transaction

from apps.audit.services import audit
from apps.revenue.models import RateSchedule


@transaction.atomic
def change_rate(*, council_revenue_item, new_amount, actor, effective_from=None):
    """Rate changes never mutate history: close the current open row, open a new
    one. See V2_ARCHITECTURE.md §7.6."""
    effective_from = effective_from or datetime.date.today()

    current = council_revenue_item.rate_schedules.filter(effective_to__isnull=True).first()
    if current:
        current.effective_to = effective_from
        current.save(update_fields=["effective_to"])

    new_row = RateSchedule.objects.create(
        council_revenue_item=council_revenue_item,
        rate_amount=new_amount,
        effective_from=effective_from,
    )

    audit(
        council_id=council_revenue_item.council_id,
        actor=actor,
        action="RATE_CHANGED",
        entity_type="COUNCIL_REVENUE_ITEM",
        entity_id=council_revenue_item.id,
        detail={"old_rate": str(current.rate_amount) if current else None, "new_rate": str(new_amount)},
    )
    return new_row
