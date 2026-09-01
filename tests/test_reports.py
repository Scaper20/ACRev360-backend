"""
Ad-hoc report endpoint (item 4 of the frontend's backend requirements doc) —
GET /api/v1/reports?entity=...&group_by=...&<filters>. Four entities (Payers/
Bills/Payments/Settlements), four dimensions (ward/revenue_item/consultant/
date), each entity scoped exactly like its own direct endpoint.
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.settlements.models import CommissionSettlement
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_consultant, make_revenue_item, make_payer):
    council = make_council(code="RPT")
    with transaction.atomic():
        set_council_context(council.id)
        ward_a = make_ward(council, code="WA", name="Ward A")
        ward_b = make_ward(council, code="WB", name="Ward B")
        admin = make_user(council, username="rpt-admin")
        consultant = make_consultant(council, name="Report Co", contract_ref="CR-RPT")
        manager = make_user(council, username="rpt-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
        item_a = make_revenue_item(council, code="RPTITEM1", name="Report Item One", rate=10000)
        item_b = make_revenue_item(council, code="RPTITEM2", name="Report Item Two", rate=5000)
        yield {
            "council": council, "ward_a": ward_a, "ward_b": ward_b, "admin": admin,
            "consultant": consultant, "manager": manager, "item_a": item_a, "item_b": item_b,
        }


@pytest.mark.django_db(transaction=True)
def test_payers_report_totals_and_group_by_ward(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer A1", phone="08010000001")
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer A2", phone="08010000002")
    make_payer(scoped["council"], scoped["ward_b"], scoped["admin"], name="Payer B1", phone="08010000003")

    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=PAYERS")
    assert r.status_code == 200, r.content
    assert r.json()["rows"] == [{"count": 3}]

    r_grouped = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=PAYERS&group_by=ward")
    assert r_grouped.status_code == 200, r_grouped.content
    rows = {row["ward"]: row["count"] for row in r_grouped.json()["rows"]}
    assert rows == {"Ward A": 2, "Ward B": 1}


@pytest.mark.django_db(transaction=True)
def test_bills_report_totals(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Bill Payer", phone="08020000001")
    issue_bill(council_id=scoped["council"].id, payer=payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["admin"])

    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=BILLS")
    assert r.status_code == 200, r.content
    row = r.json()["rows"][0]
    assert row["count"] == 1
    assert row["billed"] == "10000.00"
    assert row["arrears"] == "0.00"
    assert row["balance"] == "10000.00"


@pytest.mark.django_db(transaction=True)
def test_bills_report_group_by_consultant_labels_council_direct(scoped, authed_api_client, make_payer):
    council_direct_payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Direct Payer", phone="08030000001")
    consultant_payer = make_payer(scoped["council"], scoped["ward_a"], scoped["manager"], name="Consultant Payer", phone="08030000002")
    issue_bill(council_id=scoped["council"].id, payer=council_direct_payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["admin"])
    issue_bill(council_id=scoped["council"].id, payer=consultant_payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["manager"])

    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=BILLS&group_by=consultant")
    assert r.status_code == 200, r.content
    rows = {row["consultant"]: row["count"] for row in r.json()["rows"]}
    assert rows == {"Council Direct": 1, "Report Co": 1}


@pytest.mark.django_db(transaction=True)
def test_bills_report_group_by_revenue_item_fans_out_lines(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Multi Item Payer", phone="08040000001")
    issue_bill(
        council_id=scoped["council"].id, payer=payer,
        lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}, {"council_revenue_item": scoped["item_b"], "quantity": 1}],
        actor=scoped["admin"],
    )

    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=BILLS&group_by=revenue_item")
    assert r.status_code == 200, r.content
    rows = {row["revenue_item"]: row["billed"] for row in r.json()["rows"]}
    assert rows == {"Report Item One": "10000.00", "Report Item Two": "5000.00"}


@pytest.mark.django_db(transaction=True)
def test_payments_report_group_by_date(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payment Payer", phone="08050000001")
    bill = issue_bill(council_id=scoped["council"].id, payer=payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["admin"])
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=10000, posted_by=scoped["admin"])

    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=PAYMENTS&group_by=date")
    assert r.status_code == 200, r.content
    row = r.json()["rows"][0]
    assert row["date"] == datetime.date.today().isoformat()
    assert row["count"] == 1
    assert row["amount"] == "10000.00"


@pytest.mark.django_db(transaction=True)
def test_settlements_report_totals_and_group_by_consultant(scoped, authed_api_client):
    CommissionSettlement.objects.create(
        council_id=scoped["council"].id, consultant=scoped["consultant"],
        period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31),
        gross_collections=100000, commission_rate=30, commission_amount=30000, computed_by=scoped["admin"],
    )

    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=SETTLEMENTS")
    assert r.status_code == 200, r.content
    assert r.json()["rows"] == [{"count": 1, "commission_amount": "30000.00", "gross_collections": "100000.00"}]

    r_grouped = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=SETTLEMENTS&group_by=consultant")
    assert r_grouped.status_code == 200, r_grouped.content
    assert r_grouped.json()["rows"][0]["consultant"] == "Report Co"


@pytest.mark.django_db(transaction=True)
def test_report_rejects_unknown_entity(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=NOT_A_THING")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_report_rejects_invalid_dimension_for_entity(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=PAYERS&group_by=revenue_item")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_report_rejects_too_many_group_by_dimensions(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=BILLS&group_by=ward&group_by=consultant&group_by=date")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_report_rejects_revenue_item_filter_on_non_bills_entity(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get(f"/api/v1/reports?entity=PAYMENTS&revenue_item_id={scoped['item_a'].id}")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_report_rejects_malformed_date_filter(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=BILLS&date_from=not-a-date")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_global_view_cannot_access_any_report_entity(scoped, authed_api_client, make_user):
    stakeholder = make_user(scoped["council"], username="rpt-stake", access_level=AppRole.GLOBAL_VIEW)
    for entity in ("PAYERS", "BILLS", "PAYMENTS", "SETTLEMENTS"):
        r = authed_api_client(stakeholder).get(f"/api/v1/reports?entity={entity}")
        assert r.status_code == 403, (entity, r.content)


@pytest.mark.django_db(transaction=True)
def test_agent_cannot_access_settlements_report_but_can_access_bills(scoped, authed_api_client, make_user, make_field_agent):
    agent_user = make_user(scoped["council"], username="rpt-agent", access_level=AppRole.AGENT, consultant=scoped["consultant"])
    make_field_agent(scoped["council"], agent_user, ward=scoped["ward_a"], agent_code="AGT-RPT-1")

    r_settlements = authed_api_client(agent_user).get("/api/v1/reports?entity=SETTLEMENTS")
    assert r_settlements.status_code == 403, r_settlements.content

    r_bills = authed_api_client(agent_user).get("/api/v1/reports?entity=BILLS")
    assert r_bills.status_code == 200, r_bills.content


@pytest.mark.django_db(transaction=True)
def test_consultant_report_scoped_to_own_portfolio(scoped, authed_api_client, make_payer):
    own_payer = make_payer(scoped["council"], scoped["ward_a"], scoped["manager"], name="Own Portfolio Payer", phone="08060000001")
    other_payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Other Portfolio Payer", phone="08060000002")
    issue_bill(council_id=scoped["council"].id, payer=own_payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["manager"])
    issue_bill(council_id=scoped["council"].id, payer=other_payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["admin"])

    r = authed_api_client(scoped["manager"]).get("/api/v1/reports?entity=BILLS")
    assert r.status_code == 200, r.content
    assert r.json()["rows"] == [{"count": 1, "billed": "10000.00", "arrears": "0.00", "balance": "10000.00"}]


@pytest.mark.django_db(transaction=True)
def test_revenue_officer_report_matches_consultant_scope(scoped, authed_api_client, make_user, make_payer):
    officer = make_user(scoped["council"], username="rpt-officer", access_level=AppRole.REVENUE_OFFICER, consultant=scoped["consultant"])
    own_payer = make_payer(scoped["council"], scoped["ward_a"], scoped["manager"], name="Officer Scoped Payer", phone="08070000001")
    other_payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Officer Outside Payer", phone="08070000002")
    issue_bill(council_id=scoped["council"].id, payer=own_payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["manager"])
    issue_bill(council_id=scoped["council"].id, payer=other_payer, lines=[{"council_revenue_item": scoped["item_a"], "quantity": 1}], actor=scoped["admin"])

    r = authed_api_client(officer).get("/api/v1/reports?entity=BILLS")
    assert r.status_code == 200, r.content
    assert r.json()["rows"][0]["count"] == 1
