"""
Serializer fields added for the frontend handoff (BACKEND_HANDOFF.md items 3
and 4): consultant_name on bills, payer identity on payments.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="SER")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="ser-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="SERITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


@pytest.mark.django_db(transaction=True)
def test_bill_serializer_consultant_name_present(scoped, authed_api_client, make_consultant, make_user, make_payer):
    council, ward, item = scoped["council"], scoped["ward"], scoped["item"]
    consultant = make_consultant(council, name="Serializer Co")
    consultant_user = make_user(council, username="ser-consultant", access_level=AppRole.CONSULTANT, consultant=consultant)
    payer = make_payer(council, ward, consultant_user, name="Consultant Payer")
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=consultant_user)

    r = authed_api_client(scoped["admin"]).get("/api/v1/bills")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["id"] == bill.id)
    assert row["consultant_name"] == "Serializer Co"


@pytest.mark.django_db(transaction=True)
def test_bill_serializer_consultant_name_none_for_council_direct(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    r = authed_api_client(admin).get("/api/v1/bills")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["id"] == bill.id)
    assert row["consultant_name"] is None


@pytest.mark.django_db(transaction=True)
def test_payment_serializer_includes_payer_identity(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/payments")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["id"] == payment.id)
    assert row["full_name"] == payer.full_name
    assert row["payer_ref"] == payer.payer_ref
