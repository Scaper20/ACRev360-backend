from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.accounts.models import SubConsultant
from apps.audit.services import audit
from apps.payments.models import Payment
from apps.settlements.models import CommissionSettlement


@transaction.atomic
def compute_settlements(*, council_id, period_start, period_end, actor) -> list[CommissionSettlement]:
    """gross collections in the period x the consultant's contract rate. The rate
    is snapshotted onto the settlement row so a later contract change doesn't
    retroactively alter a past settlement — see SCHEMA.md §8."""
    results = []
    consultants = SubConsultant.objects.filter(council_id=council_id, status=SubConsultant.ACTIVE)

    for consultant in consultants:
        gross = (
            Payment.objects.filter(
                council_id=council_id,
                bill__payer__enumerated_by__consultant_id=consultant.id,
                txn_status=Payment.CONFIRMED,
                created_at__date__gte=period_start,
                created_at__date__lte=period_end,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0")
        )
        commission_amount = (gross * consultant.commission_rate / Decimal("100")).quantize(Decimal("0.01"))

        settlement, _created = CommissionSettlement.objects.update_or_create(
            council_id=council_id,
            consultant=consultant,
            period_start=period_start,
            period_end=period_end,
            defaults={
                "gross_collections": gross,
                "commission_rate": consultant.commission_rate,
                "commission_amount": commission_amount,
                "status": CommissionSettlement.COMPUTED,
                "computed_by": actor,
            },
        )
        results.append(settlement)

    audit(
        council_id=council_id, actor=actor, action="SETTLEMENTS_COMPUTED", entity_type="COUNCIL", entity_id=council_id,
        detail={"period_start": str(period_start), "period_end": str(period_end), "count": len(results)},
    )
    return results
