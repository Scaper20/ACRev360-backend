"""
PR 5: admin drill-down into a consultant's bills behind one settlement
(GET /settlements/{id}/bills) and a consultant's own dashboard
(GET /settlements/my-summary). compute_settlements() itself is unchanged —
this is a views gap, not a computation rewrite.
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.settlements.models import CommissionSettlement
from apps.settlements.services import compute_settlements
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_consultant, make_payer, make_revenue_item):
    council = make_council(code="STL")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="stl-admin")
        consultant = make_consultant(council, name="Stella Consulting", contract_ref="CR-STL", rate=10)
        consultant_user = make_user(
            council, username="stl-consultant", access_level=AppRole.CONSULTANT, consultant=consultant,
        )
        payer = make_payer(council, ward, consultant_user, name="Stella Payer", phone="08020000001")
        item = make_revenue_item(council, code="STLITEM", rate=10000)
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        yield {
            "council": council, "ward": ward, "admin": admin, "consultant": consultant,
            "consultant_user": consultant_user, "payer": payer, "item": item, "channel": channel,
        }


def _compute_current_period(scoped):
    today = datetime.date.today()
    return compute_settlements(
        council_id=scoped["council"].id,
        period_start=today.replace(month=1, day=1), period_end=today,
        actor=scoped["admin"],
    )


@pytest.mark.django_db(transaction=True)
def test_admin_can_filter_settlements_by_consultant_id(scoped, authed_api_client, make_consultant):
    other = make_consultant(scoped["council"], name="Other Co", contract_ref="CR-OTHER")
    compute_settlements(
        council_id=scoped["council"].id, period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 12, 31),
        actor=scoped["admin"],
    )

    r = authed_api_client(scoped["admin"]).get(f"/api/v1/settlements?consultant_id={scoped['consultant'].id}")
    assert r.status_code == 200, r.content
    assert all(row["consultant"] == scoped["consultant"].id for row in r.json()["results"])
    assert not any(row["consultant"] == other.id for row in r.json()["results"])


@pytest.mark.django_db(transaction=True)
def test_admin_drilldown_shows_per_bill_commission_and_status(scoped, authed_api_client):
    council, payer, admin, consultant, item, channel = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["consultant"], scoped["item"], scoped["channel"],
    )
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=admin)

    settlements = _compute_current_period(scoped)
    settlement = next(s for s in settlements if s.consultant_id == consultant.id)
    settlement.status = CommissionSettlement.APPROVED
    settlement.save(update_fields=["status"])

    r = authed_api_client(admin).get(f"/api/v1/settlements/{settlement.id}/bills")
    assert r.status_code == 200, r.content
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["bill_ref"] == bill.bill_ref
    assert rows[0]["payer_name"] == payer.full_name
    assert rows[0]["collected"] == "10000.00"
    assert rows[0]["commission"] == "1000.00"  # 10% of 10000
    assert rows[0]["status"] == "APPROVED"


@pytest.mark.django_db(transaction=True)
def test_consultant_cannot_drilldown_into_another_consultants_settlement(scoped, authed_api_client, make_consultant, make_user):
    other_consultant = make_consultant(scoped["council"], name="Rival Co", contract_ref="CR-RIVAL")
    other_user = make_user(
        scoped["council"], username="stl-rival", access_level=AppRole.CONSULTANT, consultant=other_consultant,
    )
    settlements = _compute_current_period(scoped)
    own_settlement = next((s for s in settlements if s.consultant_id == scoped["consultant"].id), None)
    assert own_settlement is not None

    r = authed_api_client(other_user).get(f"/api/v1/settlements/{own_settlement.id}/bills")
    assert r.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_my_summary_returns_year_approved_and_settled_totals(scoped, authed_api_client):
    council, payer, admin, consultant_user, item, channel = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["consultant_user"], scoped["item"], scoped["channel"],
    )
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=admin)

    settlements = _compute_current_period(scoped)
    settlement = settlements[0]
    settlement.status = CommissionSettlement.SETTLED
    settlement.save(update_fields=["status"])

    r = authed_api_client(consultant_user).get("/api/v1/settlements/my-summary")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total_this_year"] == "1000.00"
    assert body["settled_total"] == "1000.00"
    assert body["approved_total"] == "0.00"
    assert len(body["bills"]) == 1
    assert body["bills"][0]["bill_ref"] == bill.bill_ref


@pytest.mark.django_db(transaction=True)
def test_my_summary_empty_before_any_settlement_computed(scoped, authed_api_client):
    r = authed_api_client(scoped["consultant_user"]).get("/api/v1/settlements/my-summary")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total_this_year"] == "0.00"
    assert body["bills"] == []
