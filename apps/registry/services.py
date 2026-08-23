from django.db import transaction

from apps.audit.services import audit
from apps.billing.services import create_draft_assessment
from apps.common.refs import finalize_ref, placeholder_ref
from apps.registry.models import Payer


class DuplicatePayer(Exception):
    def __init__(self, duplicate_of: Payer):
        self.duplicate_of = duplicate_of
        super().__init__(f"Possible duplicate of {duplicate_of.payer_ref}")


@transaction.atomic
def create_payer(*, council_id, actor, revenue_item_ids=None, force=False, enumerated_by=None, **fields) -> tuple[Payer, int]:
    """Individual and business registration are separate flows with distinct ID
    formats — see PRD.md §4.2. Also backs the offline mobile sync path (a payer
    captured offline is created for real on sync, through this same function).

    `enumerated_by` defaults to `actor` (whoever's actually making the call) —
    the normal case, self-registration by a consultant/agent. An admin
    registering on a consultant's behalf passes a different `enumerated_by`
    explicitly (resolved by the view from the chosen consultant's own user),
    since `enumerated_by` is what every portfolio-scoping check elsewhere in
    the system (`common.scoping.portfolio_filter`) keys off — this is a real
    ownership assignment, not just an audit-trail note."""
    phone = fields.get("phone")
    if phone and not force:
        existing = Payer.objects.filter(council_id=council_id, phone=phone).first()
        if existing:
            raise DuplicatePayer(existing)

    if fields.get("payer_type") == Payer.INDIVIDUAL:
        fields["business_size"] = None
        fields.setdefault("tin", "")
    else:
        fields.setdefault("nin_bvn_hash", "")

    payer = Payer.objects.create(
        council_id=council_id,
        payer_ref=placeholder_ref(),
        enumerated_by=enumerated_by or actor,
        **fields,
    )
    prefix = "IND" if payer.payer_type == Payer.INDIVIDUAL else "C"
    finalize_ref(payer, "payer_ref", f"{prefix}-{payer.id:07d}")

    draft_count = 0
    for item in revenue_item_ids or []:
        create_draft_assessment(payer=payer, council_revenue_item=item, actor=actor)
        draft_count += 1

    audit(
        council_id=council_id, actor=actor, action="PAYER_ENUMERATED", entity_type="PAYER", entity_id=payer.id,
        detail={"payer_ref": payer.payer_ref, "draft_assessments_created": draft_count},
    )
    return payer, draft_count
