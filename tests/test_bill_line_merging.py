"""
The same revenue item (under the exact same band/tier) landing twice on one
bill merges into a single line with combined quantity/amount, instead of two
visibly duplicate rows — see billing.services.issue_bill/add_bill_line. A
different band/tier for the same item is a genuinely distinct charge and is
never merged.
"""
import pytest
from django.db import transaction

from apps.billing.models import Bill
from apps.billing.services import add_bill_line, issue_bill
from apps.revenue.models import RateBand
from apps.revenue.services import replace_rate_bands
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="MRG")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="mrg-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="MRGITEM", rate=5000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


@pytest.mark.django_db(transaction=True)
def test_issue_bill_merges_two_lines_for_the_same_flat_item(scoped):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[
            {"council_revenue_item": scoped["item"], "quantity": 1},
            {"council_revenue_item": scoped["item"], "quantity": 2},
        ],
    )
    assert bill.lines.count() == 1
    [line] = bill.lines.all()
    assert line.assessment.quantity == 3
    assert line.line_amount == 15000
    assert bill.total_amount == 15000


@pytest.mark.django_db(transaction=True)
def test_issue_bill_keeps_different_bands_of_the_same_item_separate(scoped):
    item = scoped["item"]
    [beer, wine] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[
            {"label": "Beer Parlor", "rate_mode": RateBand.FLAT, "flat_amount": 5000},
            {"label": "Wine Bar", "rate_mode": RateBand.FLAT, "flat_amount": 8000},
        ],
    )
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[
            {"council_revenue_item": item, "quantity": 1, "rate_band": beer},
            {"council_revenue_item": item, "quantity": 1, "rate_band": wine},
        ],
    )
    assert bill.lines.count() == 2
    assert bill.total_amount == 13000


@pytest.mark.django_db(transaction=True)
def test_issue_bill_merges_an_explicit_line_with_a_swept_in_draft(scoped):
    from apps.billing.services import create_draft_assessment

    create_draft_assessment(payer=scoped["payer"], council_revenue_item=scoped["item"], actor=scoped["admin"], quantity=1)
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 4}],
        bill_all_drafts=True,
    )
    assert bill.lines.count() == 1
    [line] = bill.lines.all()
    assert line.assessment.quantity == 5
    assert line.line_amount == 25000


@pytest.mark.django_db(transaction=True)
def test_add_bill_line_merges_into_an_existing_line_for_the_same_item(scoped):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    [original_line] = bill.lines.all()

    line = add_bill_line(bill=bill, council_revenue_item=scoped["item"], quantity=2, actor=scoped["admin"])

    assert line.id == original_line.id
    assert bill.lines.count() == 1
    line.refresh_from_db()
    assert line.assessment.quantity == 3
    assert line.line_amount == 15000
    bill.refresh_from_db()
    assert bill.total_amount == 15000


@pytest.mark.django_db(transaction=True)
def test_add_bill_line_with_a_different_band_stays_a_separate_line(scoped):
    item = scoped["item"]
    [beer, wine] = replace_rate_bands(
        council_revenue_item=item, actor=scoped["admin"],
        bands=[
            {"label": "Beer Parlor", "rate_mode": RateBand.FLAT, "flat_amount": 5000},
            {"label": "Wine Bar", "rate_mode": RateBand.FLAT, "flat_amount": 8000},
        ],
    )
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": item, "quantity": 1, "rate_band": beer}],
    )
    add_bill_line(bill=bill, council_revenue_item=item, quantity=1, actor=scoped["admin"], rate_band=wine)

    assert bill.lines.count() == 2
    bill.refresh_from_db()
    assert bill.total_amount == 13000


@pytest.mark.django_db(transaction=True)
def test_add_bill_line_merge_cancels_the_superseded_assessment(scoped):
    from apps.billing.models import Assessment

    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    before = Assessment.objects.filter(payer=scoped["payer"]).count()
    add_bill_line(bill=bill, council_revenue_item=scoped["item"], quantity=1, actor=scoped["admin"])
    after = Assessment.objects.filter(payer=scoped["payer"]).count()

    assert after == before + 1  # the merged-away assessment still exists, just CANCELLED
    assert Assessment.objects.filter(payer=scoped["payer"], status=Assessment.CANCELLED).count() == 1
    assert Bill.objects.get(id=bill.id).lines.count() == 1
