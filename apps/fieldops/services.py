from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce

from apps.billing.models import Bill
from apps.billing.services import BillingError
from apps.fieldops.models import MobileSyncRecord
from apps.payments.models import PaymentChannel, POSTerminal
from apps.payments.services import PaymentRejected, post_payment
from apps.registry.models import EnumeratedAsset, Payer
from apps.registry.services import DuplicatePayer, create_payer
from apps.revenue.models import CouncilRevenueItem


def get_worklist(*, council_id, agent, q=None):
    """Ward-scoped payer list, sorted by outstanding balance — the agent's
    field worklist. Deliberately NOT portfolio-scoped by revenue item: a
    payer owing on a mix of items still belongs on the agent's list so they
    can collect against whichever of those items they *are* assigned; item
    scoping applies at enumeration/registration time instead (the existing
    GET /revenue-items already does this correctly for AGENT — see
    CouncilRevenueItemViewSet.get_queryset()). An agent with no ward assigned
    gets an empty list rather than the whole council — an unset ward reads as
    a setup gap, not an intentional "see everyone" grant."""
    if not agent.assigned_ward_id:
        return Payer.objects.none()

    qs = Payer.objects.filter(council_id=council_id, ward_id=agent.assigned_ward_id)
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(payer_ref__icontains=q))

    non_terminal = ~Q(bills__status__in=Bill.TERMINAL_STATUSES)
    qs = qs.annotate(
        outstanding=Coalesce(
            Sum(F("bills__total_amount") - F("bills__amount_paid"), filter=non_terminal),
            Decimal("0"),
        )
    ).order_by("-outstanding", "full_name")
    return qs


def _bucket_for(status_):
    return {"ACCEPTED": "accepted", "CONFLICT": "conflicts", "REJECTED": "rejected"}[status_]


@transaction.atomic
def replay_sync_record(*, council_id, agent, actor, client_id, record_type, payload):
    """Replays one offline-queued record through the same service functions
    the online path uses (post_payment / create_payer) — see
    payments.services.post_payment's own docstring: "the single money-in
    path every channel ... offline sync replay ... funnels through." Returns
    a (bucket, MobileSyncRecord) pair where bucket is 'accepted'/'conflicts'/
    'rejected', matching the old prototype's /api/mobile/sync response shape
    so the client-side queue logic ports with minimal change.

    Idempotent on client_id: a client_id already recorded (any outcome) is
    never reprocessed — it's returned as-is. A correction after a CONFLICT/
    REJECTED needs a new client_id, standard idempotency-key semantics; this
    isn't a place to retry-with-different-input under the same key.

    Two defensive layers found in audit, not by design from the start:
    - A batch retried before its first response arrives (exactly the flaky-
      connection scenario this whole app exists for) can have two requests
      racing on the same client_id. Both would pass the "does it exist yet"
      check, both would call post_payment()/create_payer(), and only then
      collide on MobileSyncRecord's unique constraint. Catching that inside
      a nested atomic() means the loser's side effects roll back to that
      savepoint (not the whole request) and it returns the winner's actual
      outcome — instead of an uncaught IntegrityError 500ing the whole batch.
    - _replay_payment/_replay_payer only anticipated specific failure modes
      (PaymentRejected, DuplicatePayer, BillingError). A malformed record
      from a genuinely corrupted local queue (this app is explicitly built
      to survive a killed app / dying battery mid-write) could throw
      anything else — caught broadly here so one bad record can't take the
      rest of the batch down with it.
    """
    existing = MobileSyncRecord.objects.filter(council_id=council_id, client_id=client_id).first()
    if existing is not None:
        return _bucket_for(existing.status), existing

    try:
        if record_type == MobileSyncRecord.PAYMENT:
            status_, result_ref, detail = _replay_payment(council_id=council_id, agent=agent, actor=actor, payload=payload)
        else:
            status_, result_ref, detail = _replay_payer(council_id=council_id, agent=agent, actor=actor, payload=payload)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        status_, result_ref, detail = MobileSyncRecord.REJECTED, "", {"error": f"Could not process this record: {exc}"}

    try:
        with transaction.atomic():
            sync_record = MobileSyncRecord.objects.create(
                council_id=council_id, client_id=client_id, record_type=record_type, agent=agent,
                status=status_, result_ref=result_ref, detail=detail,
            )
    except IntegrityError:
        existing = MobileSyncRecord.objects.get(council_id=council_id, client_id=client_id)
        return _bucket_for(existing.status), existing

    return _bucket_for(status_), sync_record


def _replay_payment(*, council_id, agent, actor, payload):
    from apps.payments.api.serializers import PostPaymentSerializer

    serializer = PostPaymentSerializer(data=payload)
    if not serializer.is_valid():
        return MobileSyncRecord.REJECTED, "", {"error": serializer.errors}
    data = serializer.validated_data

    bill = Bill.objects.filter(pk=data["bill_id"], council_id=council_id).select_related("payer").first()
    if bill is None:
        return MobileSyncRecord.REJECTED, "", {"error": f"Bill {data['bill_id']} not found"}
    # Mirrors get_worklist()'s own ward scoping — an agent's worklist only
    # ever shows their own ward's payers, so a payment against a bill
    # outside it couldn't have come from anything this app actually showed
    # them. Audit finding: the same gap exists via POST /payments directly
    # (reachable by AGENT role, not just this sync path) — flagged
    # separately rather than fixed here, since that endpoint is shared with
    # COUNCIL_ADMIN/CONSULTANT, for whom "any bill" is correct.
    if agent.assigned_ward_id is None or bill.payer.ward_id != agent.assigned_ward_id:
        return MobileSyncRecord.REJECTED, "", {"error": f"{bill.bill_ref} is not in your assigned ward"}
    channel, _ = PaymentChannel.objects.get_or_create(code=data["channel_code"])
    terminal = None
    if data.get("terminal_id") is not None:
        terminal = POSTerminal.objects.filter(pk=data["terminal_id"], council_id=council_id).first()

    try:
        payment = post_payment(
            council_id=council_id, bill=bill, channel=channel, terminal=terminal,
            amount=data["amount"], bank_txn_ref=data.get("bank_txn_ref", ""),
            posted_by=actor, geo=data.get("geo"),
        )
    except PaymentRejected as exc:
        return MobileSyncRecord.REJECTED, "", {"error": str(exc)}
    return MobileSyncRecord.ACCEPTED, payment.payment_ref, {}


def _replay_payer(*, council_id, agent, actor, payload):
    from apps.registry.api.serializers import CreatePayerSerializer

    # geo isn't a CreatePayerSerializer field — Payer carries no geo columns,
    # EnumeratedAsset does (see below). Split it out before validation so an
    # unrecognized key doesn't even get the chance to matter either way.
    geo = payload.get("geo") or {}
    body = {k: v for k, v in payload.items() if k != "geo"}

    # Same ward boundary as _replay_payment above, checked before validation
    # so a mismatched ward doesn't waste a DuplicatePayer check against data
    # the agent had no business submitting in the first place.
    if agent.assigned_ward_id is None or str(body.get("ward")) != str(agent.assigned_ward_id):
        return MobileSyncRecord.REJECTED, "", {"error": "Payer's ward must match your assigned ward"}

    serializer = CreatePayerSerializer(data=body)
    if not serializer.is_valid():
        return MobileSyncRecord.REJECTED, "", {"error": serializer.errors}
    data = dict(serializer.validated_data)
    revenue_item_ids = data.pop("revenue_item_ids", [])
    data.pop("force", None)  # offline registration never force-creates past a duplicate — the agent resolves it on next sync
    items = list(CouncilRevenueItem.objects.filter(id__in=revenue_item_ids, council_id=council_id))

    try:
        payer, _draft_count = create_payer(council_id=council_id, actor=actor, revenue_item_ids=items, force=False, **data)
    except DuplicatePayer as exc:
        return MobileSyncRecord.CONFLICT, "", {"error": str(exc), "duplicate_of": exc.duplicate_of.payer_ref}
    except BillingError as exc:
        return MobileSyncRecord.REJECTED, "", {"error": str(exc)}

    if geo.get("lat") is not None and geo.get("lng") is not None:
        EnumeratedAsset.objects.create(
            council_id=council_id, payer=payer, asset_type=EnumeratedAsset.PREMISES,
            ward=payer.ward, geo_lat=geo["lat"], geo_lng=geo["lng"],
        )
    return MobileSyncRecord.ACCEPTED, payer.payer_ref, {}
