"""
Dashboard aggregates added for the frontend handoff (BACKEND_HANDOFF.md items 1
and 2) — zero-state safety, correct grouping, and the two decisions the fields
depend on: commission_accrued excludes SETTLED/DISPUTED rows, and ward payers
counts total registered payers, not just payers who transacted.
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.models import Bill
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.settlements.models import CommissionSettlement
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="DSH")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="dsh-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="DSHITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


@pytest.mark.django_db(transaction=True)
def test_summary_zero_state(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.content
    body = r.json()

    assert body["billed"] == 0
    assert body["collected"] == 0
    assert body["bills"] == 0
    assert body["assessments"] == 0
    assert body["payers"] == 1  # the scoped fixture's payer is still registered even with no bills/payments
    assert body["active_agents"] == 0
    assert body["by_channel"] == []
    assert body["by_item"] == []
    assert len(body["trend"]) == 14
    assert all(day["amount"] == 0 for day in body["trend"])


@pytest.mark.django_db(transaction=True)
def test_summary_with_data(scoped, authed_api_client, make_revenue_item):
    council, payer, admin, item_a = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    item_b = make_revenue_item(council, code="DSHITEM2", rate=5000)

    bill = issue_bill(
        council_id=council.id, payer=payer,
        lines=[{"council_revenue_item": item_a, "quantity": 1}, {"council_revenue_item": item_b, "quantity": 1}],
        actor=admin,
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=15000, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.content
    body = r.json()

    assert body["bills"] == 1
    assert body["payers"] == 1
    assert body["collected"] == 15000

    assert len(body["by_channel"]) == 1
    assert body["by_channel"][0]["code"] == "POS"
    assert body["by_channel"][0]["amount"] == 15000

    by_item_names = {row["item_name"] for row in body["by_item"]}
    assert by_item_names == {item_a.item_name, item_b.item_name}
    assert sum(row["billed"] for row in body["by_item"]) == 15000

    today_str = datetime.date.today().isoformat()
    trend_by_day = {row["d"]: row["amount"] for row in body["trend"]}
    assert trend_by_day[today_str] == 15000
    assert sum(1 for amt in trend_by_day.values() if amt != 0) == 1


@pytest.mark.django_db(transaction=True)
def test_summary_portfolio_scoping_for_consultant(
    scoped, authed_api_client, make_consultant, make_user, make_payer, make_field_agent,
):
    council, ward, item = scoped["council"], scoped["ward"], scoped["item"]
    consultant = make_consultant(council, name="Portfolio Co")
    consultant_user = make_user(
        council, username="dsh-consultant", access_level=AppRole.CONSULTANT, consultant=consultant,
    )
    own_payer = make_payer(council, ward, consultant_user, name="Own Payer")
    issue_bill(
        council_id=council.id, payer=own_payer, lines=[{"council_revenue_item": item, "quantity": 1}],
        actor=consultant_user,
    )
    # council-direct payer + bill + agent, outside this consultant's portfolio
    issue_bill(
        council_id=council.id, payer=scoped["payer"], lines=[{"council_revenue_item": item, "quantity": 1}],
        actor=scoped["admin"],
    )
    make_field_agent(council, scoped["admin"], ward=ward, agent_code="COUNCIL-AGENT")
    # this consultant's own agent, inside the portfolio
    own_agent_user = make_user(council, username="dsh-consultant-agent", consultant=consultant)
    make_field_agent(council, own_agent_user, ward=ward, agent_code="PORTFOLIO-AGENT")

    r = authed_api_client(consultant_user).get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.content
    body = r.json()

    assert body["bills"] == 1
    assert body["assessments"] == 1
    assert body["payers"] == 1  # only own_payer, not scoped["payer"]
    assert body["active_agents"] == 1  # only the portfolio agent, not the council-direct one


@pytest.mark.django_db(transaction=True)
def test_summary_assessments_tracks_bills_after_supersession(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    # roll_arrears supersedes the original bill into a new consolidated one
    # that carries the debt forward as a lump arrears figure, with no fresh
    # BillLine of its own. The original Assessment's BillLine still points at
    # the now-SUPERSEDED bill, so a naive status=BILLED count would keep
    # counting it forever — assessments must track `bills` via the bill_lines
    # join and correctly drop to 0 here, not stay pinned at 1.
    issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    r = authed_api_client(admin).get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["bills"] == 1
    assert body["assessments"] == 0


@pytest.mark.django_db(transaction=True)
def test_global_zero_state(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/dashboard/global")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["by_consultant"] == []
    assert body["by_ward"] == []


@pytest.mark.django_db(transaction=True)
def test_global_commission_accrued_excludes_settled_and_disputed(
    scoped, authed_api_client, make_consultant, make_user, make_payer,
):
    council, ward, item = scoped["council"], scoped["ward"], scoped["item"]
    consultant = make_consultant(council, name="Accrual Co")
    consultant_user = make_user(
        council, username="dsh-accrual-consultant", access_level=AppRole.CONSULTANT, consultant=consultant,
    )
    payer = make_payer(council, ward, consultant_user, name="Accrual Payer")
    # by_consultant is built from payments, so this consultant needs at least
    # one confirmed payment to appear as a row at all.
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=consultant_user)
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=consultant_user)

    period = datetime.date.today()
    for i, status in enumerate([
        CommissionSettlement.COMPUTED, CommissionSettlement.APPROVED,
        CommissionSettlement.SETTLED, CommissionSettlement.DISPUTED,
    ]):
        CommissionSettlement.objects.create(
            council=council, consultant=consultant,
            period_start=period - datetime.timedelta(days=30 * (i + 1)),
            period_end=period - datetime.timedelta(days=30 * i),
            gross_collections=100000, commission_rate=30, commission_amount=1000 * (i + 1),
            status=status, computed_by=scoped["admin"],
        )

    r = authed_api_client(scoped["admin"]).get("/api/v1/dashboard/global")
    assert r.status_code == 200, r.content
    rows = {row["consultant_name"]: row for row in r.json()["by_consultant"]}
    # COMPUTED (1000) + APPROVED (2000) only — SETTLED/DISPUTED excluded
    assert rows["Accrual Co"]["commission_accrued"] == 3000


@pytest.mark.django_db(transaction=True)
def test_global_collection_rate_null_when_billed_zero(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=5000, posted_by=admin)

    # Force the bill out of the billed_by_consultant aggregate (which excludes
    # CANCELLED/SUPERSEDED) while its payment history remains — an edge case
    # for the collection_rate guard, not a normal business flow.
    bill.status = Bill.CANCELLED
    bill.save(update_fields=["status"])

    r = authed_api_client(admin).get("/api/v1/dashboard/global")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["by_consultant"] if row["consultant_name"] == "Council Direct")
    assert row["billed"] == 0
    assert row["collection_rate"] is None


@pytest.mark.django_db(transaction=True)
def test_global_ward_payers_total_registered(scoped, authed_api_client, make_payer):
    council, ward, admin, item = scoped["council"], scoped["ward"], scoped["admin"], scoped["item"]
    make_payer(council, ward, admin, name="Second Payer", phone="08020000000")
    # by_ward is built from payments, so the ward needs at least one confirmed
    # payment to appear as a row at all — its "payers" count still comes from
    # a separate total-registered query, independent of who actually paid.
    bill = issue_bill(council_id=council.id, payer=scoped["payer"], lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=1000, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/dashboard/global")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["by_ward"] if row["ward_name"] == ward.ward_name)
    assert row["payers"] == 2
    assert row["collected"] == 1000


@pytest.mark.django_db(transaction=True)
def test_global_council_direct_grouping_unaffected(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/dashboard/global")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["by_consultant"] if row["consultant_name"] == "Council Direct")
    assert row["billed"] == 10000
    assert row["collected"] == 10000
    assert row["collection_rate"] == 100
    assert row["commission_accrued"] == 0
    assert row["status"] is None
