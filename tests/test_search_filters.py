"""
`q` search params added across list-heavy admin screens — DebtCaseViewSet,
ReceiptViewSet, AuditLogViewSet, CommissionSettlementViewSet — reported as
tedious to search once a council has "a lot of people" in any of these
lists. One representative test per endpoint; the SubConsultant/FieldAgent
search additions from the same pass are covered in test_accounts.py.
"""
import datetime

import pytest
from django.db import transaction

from apps.audit.services import audit
from apps.billing.services import issue_bill
from apps.enforcement.services import refresh_debt
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.settlements.models import CommissionSettlement
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="SRCH")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="srch-admin")
        item = make_revenue_item(council, code="SRCHITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "item": item}


@pytest.mark.django_db(transaction=True)
def test_debt_search_matches_bill_ref_and_payer_name(scoped, authed_api_client, make_payer):
    council, admin, item = scoped["council"], scoped["admin"], scoped["item"]
    findable = make_payer(council, scoped["ward"], admin, name="Findable Debtor", phone="08011110001")
    other = make_payer(council, scoped["ward"], admin, name="Other Debtor", phone="08011110002")
    for payer in (findable, other):
        issue_bill(
            council_id=council.id, payer=payer, due_date=datetime.date.today() - datetime.timedelta(days=40),
            lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin,
        )
    refresh_debt(council_id=council.id, actor=admin)

    r = authed_api_client(admin).get("/api/v1/debt?q=Findable")
    assert r.status_code == 200, r.content
    names = {row["full_name"] for row in r.json()["results"]}
    assert names == {"Findable Debtor"}


@pytest.mark.django_db(transaction=True)
def test_receipt_search_matches_payer_name(scoped, authed_api_client, make_payer):
    council, admin, item = scoped["council"], scoped["admin"], scoped["item"]
    findable = make_payer(council, scoped["ward"], admin, name="Findable Payer", phone="08022220001")
    other = make_payer(council, scoped["ward"], admin, name="Other Payer", phone="08022220002")
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    for payer in (findable, other):
        bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
        post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/receipts?q=Findable")
    assert r.status_code == 200, r.content
    assert len(r.json()["results"]) == 1


@pytest.mark.django_db(transaction=True)
def test_audit_search_matches_action_and_actor(scoped, authed_api_client, make_user):
    council, admin = scoped["council"], scoped["admin"]
    other_actor = make_user(council, username="srch-other-actor")
    audit(council_id=council.id, actor=admin, action="FINDABLE_ACTION", entity_type="TEST", entity_id=1, detail={})
    audit(council_id=council.id, actor=other_actor, action="UNRELATED_ACTION", entity_type="TEST", entity_id=2, detail={})

    r = authed_api_client(admin).get("/api/v1/audit?q=FINDABLE_ACTION")
    assert r.status_code == 200, r.content
    actions = {row["action"] for row in r.json()["results"]}
    assert actions == {"FINDABLE_ACTION"}

    r_actor = authed_api_client(admin).get("/api/v1/audit?q=srch-admin")
    actions_by_actor = {row["action"] for row in r_actor.json()["results"]}
    assert "FINDABLE_ACTION" in actions_by_actor
    assert "UNRELATED_ACTION" not in actions_by_actor


@pytest.mark.django_db(transaction=True)
def test_settlements_search_matches_consultant_name(scoped, authed_api_client, make_consultant):
    council, admin = scoped["council"], scoped["admin"]
    findable = make_consultant(council, name="Findable Settlement Co", contract_ref="CR-SETTLE-FIND")
    other = make_consultant(council, name="Other Settlement Co", contract_ref="CR-SETTLE-OTHER")
    period = datetime.date.today()
    for consultant in (findable, other):
        CommissionSettlement.objects.create(
            council=council, consultant=consultant, period_start=period - datetime.timedelta(days=30), period_end=period,
            gross_collections=100000, commission_rate=30, commission_amount=30000,
            status=CommissionSettlement.COMPUTED, computed_by=admin,
        )

    r = authed_api_client(admin).get("/api/v1/settlements?q=Findable")
    assert r.status_code == 200, r.content
    names = {row["consultant_name"] for row in r.json()["results"]}
    assert names == {"Findable Settlement Co"}
