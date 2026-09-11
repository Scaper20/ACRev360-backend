"""
PR 4: FIFO payment allocation (PaymentAllocation) + itemized arrears per
revenue item on BillLine.current_amount/arrears_amount, replacing the old
bill-level Bill.arrears_amount lump sum. Sequenced after PR 3 (duplicate-bill
guard) since both touch issue_bill().
"""
import pytest
from django.db import transaction

from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment, reverse_payment
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="FIFO")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="fifo-admin")
        payer = make_payer(council, ward, admin)
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "channel": channel}


@pytest.mark.django_db(transaction=True)
def test_fifo_allocates_across_lines_in_order(scoped, make_revenue_item):
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item1 = make_revenue_item(council, code="FIFOA", rate=20)
    item2 = make_revenue_item(council, code="FIFOB", rate=20)
    item3 = make_revenue_item(council, code="FIFOC", rate=20)
    bill = issue_bill(
        council_id=council.id, payer=payer,
        lines=[
            {"council_revenue_item": item1, "quantity": 1},
            {"council_revenue_item": item2, "quantity": 1},
            {"council_revenue_item": item3, "quantity": 1},
        ],
        actor=admin,
    )

    post_payment(council_id=council.id, bill=bill, channel=channel, amount=35, posted_by=admin)

    lines = list(bill.lines.order_by("position"))
    assert [l.paid_amount for l in lines] == [20, 15, 0]
    bill.refresh_from_db()
    assert bill.amount_paid == 35
    assert bill.balance == 25


@pytest.mark.django_db(transaction=True)
def test_overpayment_credits_payer_instead_of_negative_balance(scoped, make_revenue_item):
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item = make_revenue_item(council, code="FIFOD", rate=20)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    post_payment(council_id=council.id, bill=bill, channel=channel, amount=30, posted_by=admin)

    bill.refresh_from_db()
    payer.refresh_from_db()
    assert bill.amount_paid == 20
    assert bill.balance == 0
    assert payer.credit_balance == 10


@pytest.mark.django_db(transaction=True)
def test_item_with_current_and_arrears_is_one_line(scoped, make_revenue_item):
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    item_a = make_revenue_item(council, code="ARRA", rate=100)

    bill1 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)
    assert bill1.balance == 100  # left entirely unpaid

    bill2 = issue_bill(
        council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}],
        roll_arrears=True, actor=admin,
    )

    assert bill2.lines.filter(assessment__council_revenue_item=item_a).count() == 1
    line = bill2.lines.get(assessment__council_revenue_item=item_a)
    assert line.current_amount == 100
    assert line.arrears_amount == 100
    assert line.line_amount == 200
    assert bill2.arrears_amount == 100
    assert bill2.total_amount == 200


@pytest.mark.django_db(transaction=True)
def test_arrears_only_item_gets_its_own_line_with_zero_current(scoped, make_revenue_item):
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    item_b = make_revenue_item(council, code="ARRB", rate=50)
    bill1 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}], actor=admin)

    bill2 = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    line = bill2.lines.get(assessment__council_revenue_item=item_b)
    assert line.current_amount == 0
    assert line.arrears_amount == 50
    assert line.line_amount == 50
    assert bill2.total_amount == 50
    bill1.refresh_from_db()
    assert bill1.status == "SUPERSEDED"


@pytest.mark.django_db(transaction=True)
def test_arrears_itemization_reflects_per_line_partial_payment(scoped, make_revenue_item):
    """Prior FIFO allocation, not the old bill-level balance, decides how much
    of each item carries forward — a fully-paid line drops out entirely, a
    part-paid one carries only its own remainder."""
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item_a = make_revenue_item(council, code="ARRC", rate=100)
    item_c = make_revenue_item(council, code="ARRD", rate=50)
    bill1 = issue_bill(
        council_id=council.id, payer=payer,
        lines=[{"council_revenue_item": item_a, "quantity": 1}, {"council_revenue_item": item_c, "quantity": 1}],
        actor=admin,
    )
    post_payment(council_id=council.id, bill=bill1, channel=channel, amount=120, posted_by=admin)

    bill2 = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    assert not bill2.lines.filter(assessment__council_revenue_item=item_a).exists()
    line_c = bill2.lines.get(assessment__council_revenue_item=item_c)
    assert line_c.arrears_amount == 30
    assert bill2.total_amount == 30


@pytest.mark.django_db(transaction=True)
def test_bill_detail_api_exposes_current_arrears_and_paid_amounts(scoped, authed_api_client, make_revenue_item):
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item = make_revenue_item(council, code="ARRE", rate=100)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=40, posted_by=admin)

    r = authed_api_client(admin).get(f"/api/v1/bills/{bill.id}/detail")
    assert r.status_code == 200, r.content
    line = r.json()["lines"][0]
    assert line["current_amount"] == "100.00"
    assert line["arrears_amount"] == "0.00"
    assert line["paid_amount"] == "40.00"


@pytest.mark.django_db(transaction=True)
def test_public_bill_lookup_exposes_paid_amount_per_line(scoped, api_client, make_revenue_item):
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item = make_revenue_item(council, code="ARRF", rate=100)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=25, posted_by=admin)

    r = api_client.get(f"/api/v1/bills/{bill.bill_ref}")
    assert r.status_code == 200, r.content
    line = r.json()["lines"][0]
    assert line["paid_amount"] == "25.00"
    assert line["current_amount"] == "100.00"


@pytest.mark.django_db(transaction=True)
def test_payment_api_exposes_its_own_fifo_allocations(scoped, authed_api_client, make_revenue_item):
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item1 = make_revenue_item(council, code="ALLOC1", rate=20)
    item2 = make_revenue_item(council, code="ALLOC2", rate=20)
    bill = issue_bill(
        council_id=council.id, payer=payer,
        lines=[{"council_revenue_item": item1, "quantity": 1}, {"council_revenue_item": item2, "quantity": 1}],
        actor=admin,
    )
    payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=30, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/payments")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["id"] == payment.id)
    allocations = row["allocations"]
    assert len(allocations) == 2
    assert {a["harmonised_code"] for a in allocations} == {"ALLOC1", "ALLOC2"}
    assert sorted(a["amount"] for a in allocations) == ["10.00", "20.00"]


@pytest.mark.django_db(transaction=True)
def test_receipt_api_exposes_the_payments_own_allocation_slice(scoped, authed_api_client, make_revenue_item):
    """Distinct from `lines` (the bill's running totals across every payment
    ever made) — `allocations` is just this one payment's own FIFO slice."""
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item = make_revenue_item(council, code="ALLOC3", rate=100)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=40, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/receipts")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["payment"] == payment.id)
    assert len(row["allocations"]) == 1
    assert row["allocations"][0]["amount"] == "40.00"
    assert row["allocations"][0]["harmonised_code"] == "ALLOC3"
    # the bill-level line total (paid_amount) reflects the same single
    # payment here, but is a conceptually different number (cumulative).
    assert row["lines"][0]["paid_amount"] == "40.00"


@pytest.mark.django_db(transaction=True)
def test_reversing_an_overpayment_claws_back_only_what_was_actually_credited(scoped, make_revenue_item):
    """Bug found in review: reverse_payment() used to subtract the full
    original payment.amount from bill.amount_paid, even though post_payment()
    only ever credited the FIFO-applied portion there (the rest went to
    Payer.credit_balance). Reversing an overpaid payment must undo exactly
    what was actually applied — not the raw amount — and claw back the
    credit it created."""
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item = make_revenue_item(council, code="REVFIFO", rate=20)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=30, posted_by=admin)
    bill.refresh_from_db()
    payer.refresh_from_db()
    assert bill.amount_paid == 20
    assert payer.credit_balance == 10

    reverse_payment(payment=payment, actor=admin, reason="test reversal")

    bill.refresh_from_db()
    payer.refresh_from_db()
    assert bill.amount_paid == 0
    assert payer.credit_balance == 0


@pytest.mark.django_db(transaction=True)
def test_reversing_an_overpayment_does_not_wipe_a_prior_payments_contribution(scoped, make_revenue_item):
    """The full-amount subtraction bug was especially dangerous when a bill
    already had other confirmed payments on it: subtracting the raw
    (over-)payment amount could clobber money a *different* payment
    legitimately contributed."""
    council, payer, admin, channel = scoped["council"], scoped["payer"], scoped["admin"], scoped["channel"]
    item1 = make_revenue_item(council, code="REVFIFO2", rate=20)
    item2 = make_revenue_item(council, code="REVFIFO3", rate=20)
    bill = issue_bill(
        council_id=council.id, payer=payer,
        lines=[{"council_revenue_item": item1, "quantity": 1}, {"council_revenue_item": item2, "quantity": 1}],
        actor=admin,
    )

    first_payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=20, posted_by=admin)
    second_payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=25, posted_by=admin)
    bill.refresh_from_db()
    payer.refresh_from_db()
    assert bill.amount_paid == 40  # fully paid (20 + 20 applied)
    assert payer.credit_balance == 5  # 25 - 20 outstanding = 5 leftover

    reverse_payment(payment=second_payment, actor=admin, reason="test reversal")

    bill.refresh_from_db()
    payer.refresh_from_db()
    # first_payment's contribution must survive untouched.
    assert bill.amount_paid == 20
    assert payer.credit_balance == 0
    assert first_payment.txn_status == "CONFIRMED"


@pytest.mark.django_db(transaction=True)
def test_update_bill_line_rejects_amount_below_its_own_arrears(scoped, authed_api_client, make_revenue_item):
    """Bug found in review: editing a consolidated line's total down below its
    carried arrears_amount drove current_amount (and Assessment.amount)
    negative, silently. Must be a clean 400, not corrupted data."""
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    item = make_revenue_item(council, code="NEGCHK", rate=100)
    bill1 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    bill2 = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)
    line = bill2.lines.get(assessment__council_revenue_item=item)
    assert line.arrears_amount == 100

    r = authed_api_client(admin).put(f"/api/v1/bills/{bill2.id}/lines/{line.id}", {"line_amount": "50"}, format="json")
    assert r.status_code == 400, r.content
    line.refresh_from_db()
    assert line.current_amount == 0  # unchanged, not driven negative
    assert line.arrears_amount == 100


@pytest.mark.django_db(transaction=True)
def test_update_bill_line_accepts_amount_at_or_above_its_own_arrears(scoped, authed_api_client, make_revenue_item):
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    item = make_revenue_item(council, code="NEGCHK2", rate=100)
    bill1 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    bill2 = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)
    line = bill2.lines.get(assessment__council_revenue_item=item)

    r = authed_api_client(admin).put(f"/api/v1/bills/{bill2.id}/lines/{line.id}", {"line_amount": "150"}, format="json")
    assert r.status_code == 200, r.content
    line.refresh_from_db()
    assert line.arrears_amount == 100
    assert line.current_amount == 50
    assert line.line_amount == 150
