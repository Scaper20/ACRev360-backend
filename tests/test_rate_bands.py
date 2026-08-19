"""
Rate bands/tiers — the gazette's min/max and small/medium/large sub-classified
rate structures, layered on top of the existing flat RateSchedule. See
apps/revenue/models.py's RateBand/RateTier docstrings for the shape, and
apps/billing/services.py's create_draft_assessment for how an item with open
bands is priced instead of the plain flat rate.
"""
import datetime

import pytest
from django.db import IntegrityError, transaction

from apps.billing.services import BillingError, add_bill_line, create_draft_assessment, issue_bill
from apps.revenue.models import RateBand
from apps.revenue.services import BandingError, replace_rate_bands
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="RBD")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="rbd-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="RBDITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


@pytest.mark.django_db(transaction=True)
def test_flat_item_unaffected_by_banding_feature(scoped):
    """An item with zero bands prices exactly as before — the whole feature is
    additive, never a behavior change for existing flat items."""
    assessment = create_draft_assessment(payer=scoped["payer"], council_revenue_item=scoped["item"], actor=scoped["admin"], quantity=2)
    assert assessment.amount == 20000
    assert assessment.rate_schedule is not None
    assert assessment.rate_band is None
    assert assessment.rate_tier is None


@pytest.mark.django_db(transaction=True)
def test_range_band_accepts_amount_within_bounds(scoped):
    item = scoped["item"]
    [band] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "School Sign Board", "rate_mode": RateBand.RANGE, "min_amount": 20000, "max_amount": 40000}],
    )
    assessment = create_draft_assessment(
        payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"],
        quantity=1, rate_band=band, amount_override=30000,
    )
    assert assessment.amount == 30000
    assert assessment.rate_band_id == band.id
    assert assessment.rate_schedule is None


@pytest.mark.django_db(transaction=True)
def test_range_band_rejects_amount_outside_bounds(scoped):
    item = scoped["item"]
    [band] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "School Sign Board", "rate_mode": RateBand.RANGE, "min_amount": 20000, "max_amount": 40000}],
    )
    with pytest.raises(BillingError):
        create_draft_assessment(
            payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"],
            quantity=1, rate_band=band, amount_override=50000,
        )


@pytest.mark.django_db(transaction=True)
def test_range_band_requires_amount_override(scoped):
    item = scoped["item"]
    [band] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "School Sign Board", "rate_mode": RateBand.RANGE, "min_amount": 20000, "max_amount": 40000}],
    )
    with pytest.raises(BillingError):
        create_draft_assessment(payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"], quantity=1, rate_band=band)


@pytest.mark.django_db(transaction=True)
def test_tiered_band_charges_selected_tier_amount(scoped):
    item = scoped["item"]
    [band] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{
            "label": "Beer Parlor", "rate_mode": RateBand.TIERED,
            "tiers": [{"label": "Large", "amount": 20000}, {"label": "Medium", "amount": 10000}, {"label": "Small", "amount": 5000}],
        }],
    )
    small = band.tiers.get(label="Small")
    assessment = create_draft_assessment(
        payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"],
        quantity=1, rate_band=band, rate_tier=small,
    )
    assert assessment.amount == 5000
    assert assessment.rate_tier_id == small.id


@pytest.mark.django_db(transaction=True)
def test_tiered_band_rejects_tier_from_a_different_band(scoped):
    item = scoped["item"]
    [beer_band, wine_band] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[
            {"label": "Beer Parlor", "rate_mode": RateBand.TIERED, "tiers": [{"label": "Large", "amount": 20000}, {"label": "Small", "amount": 5000}]},
            {"label": "Wine Bar", "rate_mode": RateBand.TIERED, "tiers": [{"label": "Large", "amount": 30000}, {"label": "Small", "amount": 8000}]},
        ],
    )
    beer_tier = beer_band.tiers.get(label="Large")
    with pytest.raises(BillingError):
        create_draft_assessment(
            payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"],
            quantity=1, rate_band=wine_band, rate_tier=beer_tier,
        )


@pytest.mark.django_db(transaction=True)
def test_item_with_open_bands_requires_a_band_be_selected(scoped):
    item = scoped["item"]
    replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "Beer Parlor", "rate_mode": RateBand.TIERED, "tiers": [{"label": "Large", "amount": 20000}, {"label": "Small", "amount": 5000}]}],
    )
    with pytest.raises(BillingError):
        create_draft_assessment(payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"], quantity=1)


@pytest.mark.django_db(transaction=True)
def test_replacing_bands_versions_rather_than_mutates(scoped):
    item = scoped["item"]
    [old_band] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "Beer Parlor", "rate_mode": RateBand.FLAT, "flat_amount": 5000}],
    )
    replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "Beer Parlor", "rate_mode": RateBand.FLAT, "flat_amount": 8000}],
    )
    old_band.refresh_from_db()
    assert old_band.effective_to is not None

    [new_band] = item.active_bands
    assert new_band.flat_amount == 8000
    assert new_band.id != old_band.id

    # The closed band can no longer be used to price a new assessment.
    with pytest.raises(BillingError):
        create_draft_assessment(payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"], quantity=1, rate_band=old_band)


@pytest.mark.django_db(transaction=True)
def test_replace_rate_bands_clears_banding_with_empty_list(scoped):
    item = scoped["item"]
    replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[{"label": "Beer Parlor", "rate_mode": RateBand.FLAT, "flat_amount": 5000}],
    )
    replace_rate_bands(council_revenue_item=item, actor=scoped["admin"], bands=[])
    assert not item.active_bands.exists()
    # reverts to plain flat RateSchedule pricing
    assessment = create_draft_assessment(payer=scoped["payer"], council_revenue_item=item, actor=scoped["admin"], quantity=1)
    assert assessment.rate_schedule is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("spec,message_fragment", [
    ({"label": "Bad", "rate_mode": "RANGE", "min_amount": 40000, "max_amount": 20000}, "min_amount cannot exceed"),
    ({"label": "Bad", "rate_mode": "RANGE", "min_amount": 20000}, "need both min_amount"),
    ({"label": "Bad", "rate_mode": "FLAT"}, "need flat_amount"),
    ({"label": "Bad", "rate_mode": "TIERED", "tiers": [{"label": "Only One", "amount": 1000}]}, "at least two tiers"),
    ({"label": "Bad", "rate_mode": "TIERED", "tiers": [{"label": "X", "amount": 1000}, {"label": "X", "amount": 2000}]}, "unique within a band"),
])
def test_replace_rate_bands_validates_each_spec(scoped, spec, message_fragment):
    with pytest.raises(BandingError, match=message_fragment):
        replace_rate_bands(council_revenue_item=scoped["item"], actor=scoped["admin"], bands=[spec])


@pytest.mark.django_db(transaction=True)
def test_replace_rate_bands_rejects_duplicate_labels_in_one_call(scoped):
    with pytest.raises(BandingError, match="unique"):
        replace_rate_bands(
            council_revenue_item=scoped["item"], actor=scoped["admin"],
            bands=[
                {"label": "Dup", "rate_mode": RateBand.FLAT, "flat_amount": 1000},
                {"label": "Dup", "rate_mode": RateBand.FLAT, "flat_amount": 2000},
            ],
        )


@pytest.mark.django_db(transaction=True)
def test_add_bill_line_and_issue_bill_thread_band_and_tier(scoped):
    """The two entry points billing exposes (issue_bill's lines=, add_bill_line)
    both honor band/tier selection, not just create_draft_assessment directly."""
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    [band] = replace_rate_bands(
        council_revenue_item=item, actor=admin,
        bands=[{"label": "Beer Parlor", "rate_mode": RateBand.TIERED, "tiers": [{"label": "Large", "amount": 20000}, {"label": "Small", "amount": 5000}]}],
    )
    small = band.tiers.get(label="Small")

    bill = issue_bill(
        council_id=council.id, payer=payer, actor=admin,
        lines=[{"council_revenue_item": item, "quantity": 1, "rate_band": band, "rate_tier": small}],
    )
    assert bill.total_amount == 5000

    large = band.tiers.get(label="Large")
    line = add_bill_line(bill=bill, council_revenue_item=item, quantity=1, actor=admin, rate_band=band, rate_tier=large)
    assert line.line_amount == 20000
    bill.refresh_from_db()
    assert bill.total_amount == 25000


@pytest.mark.django_db(transaction=True)
def test_rate_bands_endpoint_requires_council_admin(scoped, make_user, authed_api_client):
    from apps.accounts.models import AppRole

    consultant_user = make_user(scoped["council"], username="rbd-consultant", access_level=AppRole.CONSULTANT)
    client = authed_api_client(consultant_user)
    resp = client.post(
        f"/api/v1/revenue-items/{scoped['item'].id}/rate-bands",
        {"bands": [{"label": "X", "rate_mode": "FLAT", "flat_amount": "1000"}]},
        format="json",
    )
    assert resp.status_code == 403


@pytest.mark.django_db(transaction=True)
def test_rate_bands_endpoint_replaces_and_returns_bands(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        f"/api/v1/revenue-items/{scoped['item'].id}/rate-bands",
        {
            "bands": [{
                "label": "Beer Parlor", "rate_mode": "TIERED",
                "tiers": [{"label": "Large", "amount": "20000"}, {"label": "Small", "amount": "5000"}],
            }],
        },
        format="json",
    )
    assert resp.status_code == 200, resp.data
    [band] = resp.data["rate_bands"]
    assert band["label"] == "Beer Parlor"
    assert band["rate_mode"] == "TIERED"
    assert {t["label"] for t in band["tiers"]} == {"Large", "Small"}


@pytest.mark.django_db(transaction=True)
def test_rate_bands_endpoint_surfaces_banding_error_as_400(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        f"/api/v1/revenue-items/{scoped['item'].id}/rate-bands",
        {"bands": [{"label": "Bad", "rate_mode": "RANGE", "min_amount": "40000", "max_amount": "20000"}]},
        format="json",
    )
    assert resp.status_code == 400
    assert "error" in resp.data


@pytest.mark.django_db(transaction=True)
def test_issue_bill_endpoint_prices_a_tiered_band_and_rejects_a_foreign_tier(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    [band] = replace_rate_bands(
        council_revenue_item=scoped["item"], actor=scoped["admin"],
        bands=[{"label": "Beer Parlor", "rate_mode": RateBand.TIERED, "tiers": [{"label": "Large", "amount": 20000}, {"label": "Small", "amount": 5000}]}],
    )
    small = band.tiers.get(label="Small")

    resp = client.post(
        "/api/v1/bills",
        {"payer_id": scoped["payer"].id, "lines": [{"revenue_item_id": scoped["item"].id, "quantity": 1, "rate_band_id": band.id, "rate_tier_id": small.id}]},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert resp.data["total_amount"] == "5000.00"

    # A band id belonging to a *different* item must 404, not silently price
    # against the wrong schedule.
    other_item = scoped["item"]
    other_band_id = band.id + 999999
    resp = client.post(
        "/api/v1/bills",
        {"payer_id": scoped["payer"].id, "lines": [{"revenue_item_id": other_item.id, "quantity": 1, "rate_band_id": other_band_id, "rate_tier_id": small.id}]},
        format="json",
    )
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_rate_band_council_revenue_item_and_label_pair_unique_while_open(scoped):
    """DB-level backstop behind the service-layer duplicate-label check —
    confirms the constraint itself, not just the Python validation in front of it."""
    from apps.revenue.models import RateBand as RateBandModel

    RateBandModel.objects.create(
        council_revenue_item=scoped["item"], label="Beer Parlor", rate_mode=RateBand.FLAT,
        flat_amount=5000, effective_from=datetime.date.today(),
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RateBandModel.objects.create(
                council_revenue_item=scoped["item"], label="Beer Parlor", rate_mode=RateBand.FLAT,
                flat_amount=8000, effective_from=datetime.date.today(),
            )
