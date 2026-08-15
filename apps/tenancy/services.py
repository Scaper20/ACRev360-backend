import datetime

from django.db import transaction

from apps.audit.services import audit
from apps.revenue.models import CouncilRevenueItem, RateSchedule
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, CouncilConfig


@transaction.atomic
def onboard_council(*, council_code, council_name, config, actor) -> Council:
    """Create council -> activate template items -> set rates -> configure bank
    accounts/prefix/print identity -> create admin users. This function is the
    first step; `activate_template_item` is called once per item. See
    V2_ARCHITECTURE.md §4.3 — this flow *is* the multi-council feature.

    Onboarding is the one place a row is created for a council that doesn't exist
    yet when the request starts, so there's no RLS context to inherit — the new
    council's own id is set as the context as soon as it exists, in the same
    transaction, so the CouncilConfig insert satisfies the RLS WITH CHECK clause."""
    council = Council.objects.create(council_code=council_code, council_name=council_name)
    set_council_context(council.id)
    CouncilConfig.objects.create(council=council, **config)
    audit(
        council_id=council.id, actor=actor, action="COUNCIL_ONBOARDED", entity_type="COUNCIL", entity_id=council.id,
        detail={"council_code": council_code, "council_name": council_name},
    )
    return council


@transaction.atomic
def activate_template_item(*, council, template, rate_amount, actor, category=None) -> CouncilRevenueItem:
    """A council activates a global template item and attaches its own rate — see
    V2_ARCHITECTURE.md §4.1. Kuje's price and AMAC's price become two independent
    rate histories against the same shared item definition."""
    item = CouncilRevenueItem.objects.create(
        council=council,
        template=template,
        harmonised_code=template.harmonised_code,
        item_name=template.item_name,
        category=category or template.category,
        unit_of_charge=template.unit_of_charge,
    )
    RateSchedule.objects.create(council_revenue_item=item, rate_amount=rate_amount, effective_from=datetime.date.today())
    audit(
        council_id=council.id, actor=actor, action="REVENUE_ITEM_ACTIVATED", entity_type="COUNCIL_REVENUE_ITEM",
        entity_id=item.id, detail={"code": item.harmonised_code, "rate": str(rate_amount)},
    )
    return item
