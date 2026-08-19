import datetime

from django.db import transaction

from apps.audit.services import audit
from apps.revenue.models import RateBand, RateSchedule, RateTier


class BandingError(Exception):
    """Raised for an invalid band/tier specification — callers map this to a 400."""


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


def _validate_band_spec(spec):
    label = (spec.get("label") or "").strip()
    mode = spec.get("rate_mode")
    if mode not in (RateBand.FLAT, RateBand.RANGE, RateBand.TIERED):
        raise BandingError(f"'{label or mode}': rate_mode must be FLAT, RANGE, or TIERED")

    if mode == RateBand.FLAT:
        if spec.get("flat_amount") is None:
            raise BandingError(f"'{label}': FLAT bands need flat_amount")
        if spec.get("min_amount") is not None or spec.get("max_amount") is not None or spec.get("tiers"):
            raise BandingError(f"'{label}': FLAT bands take only flat_amount")
    elif mode == RateBand.RANGE:
        min_amount, max_amount = spec.get("min_amount"), spec.get("max_amount")
        if min_amount is None or max_amount is None:
            raise BandingError(f"'{label}': RANGE bands need both min_amount and max_amount")
        if min_amount > max_amount:
            raise BandingError(f"'{label}': min_amount cannot exceed max_amount")
        if spec.get("flat_amount") is not None or spec.get("tiers"):
            raise BandingError(f"'{label}': RANGE bands take only min_amount/max_amount")
    elif mode == RateBand.TIERED:
        tiers = spec.get("tiers") or []
        if len(tiers) < 2:
            raise BandingError(f"'{label}': TIERED bands need at least two tiers")
        tier_labels = [t["label"].strip() for t in tiers]
        if len(set(tier_labels)) != len(tier_labels):
            raise BandingError(f"'{label}': tier labels must be unique within a band")
        if any(t.get("amount") is None for t in tiers):
            raise BandingError(f"'{label}': every tier needs an amount")
        if spec.get("flat_amount") is not None or spec.get("min_amount") is not None or spec.get("max_amount") is not None:
            raise BandingError(f"'{label}': TIERED bands take only tiers")


@transaction.atomic
def replace_rate_bands(*, council_revenue_item, bands, actor, effective_from=None):
    """Closes every currently open band for this item and opens the given
    replacement set — same never-mutate discipline as `change_rate`, applied to
    a whole band set at once (mirrors a gazette amendment superseding a whole
    schedule, not individual cells). `bands=[]` clears banding entirely, reverting
    the item to plain FLAT pricing via `RateSchedule`.

    `bands` is a list of dicts: `{label, rate_mode, flat_amount?, min_amount?,
    max_amount?, tiers?: [{label, amount}]}`.
    """
    effective_from = effective_from or datetime.date.today()

    labels = [(spec.get("label") or "").strip() for spec in bands]
    if len(set(labels)) != len(labels):
        raise BandingError("Band labels must be unique within an item")
    for spec in bands:
        _validate_band_spec(spec)

    open_bands = list(council_revenue_item.rate_bands.filter(effective_to__isnull=True))
    for band in open_bands:
        band.effective_to = effective_from
        band.save(update_fields=["effective_to"])

    created = []
    for i, spec in enumerate(bands):
        band = RateBand.objects.create(
            council_revenue_item=council_revenue_item,
            label=(spec.get("label") or "").strip(),
            sort_order=i,
            rate_mode=spec["rate_mode"],
            flat_amount=spec.get("flat_amount"),
            min_amount=spec.get("min_amount"),
            max_amount=spec.get("max_amount"),
            effective_from=effective_from,
        )
        for j, tier in enumerate(spec.get("tiers") or []):
            RateTier.objects.create(
                band=band, label=tier["label"].strip(), amount=tier["amount"], sort_order=j
            )
        created.append(band)

    audit(
        council_id=council_revenue_item.council_id,
        actor=actor,
        action="RATE_BANDS_REPLACED",
        entity_type="COUNCIL_REVENUE_ITEM",
        entity_id=council_revenue_item.id,
        detail={
            "old_band_count": len(open_bands),
            "new_bands": [{"label": b.label, "rate_mode": b.rate_mode} for b in created],
        },
    )
    return created
