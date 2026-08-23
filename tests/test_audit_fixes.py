"""
Fixes from the frontend audit (payer registration, bill enumeration, payments)
plus the new payer/bill delete capability.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.models import Assessment, Bill
from apps.billing.services import issue_bill
from apps.payments.models import Payment, PaymentChannel
from apps.payments.services import post_payment
from apps.registry.models import Payer
from apps.revenue.models import RateBand
from apps.revenue.services import replace_rate_bands
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="AUD")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="aud-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="AUDITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


# --------------------------------------------------------- payer registration


@pytest.mark.django_db(transaction=True)
def test_registering_payer_with_banded_item_400s_not_500s(scoped, authed_api_client):
    """A revenue_item_id that has open rate bands can't be enumerated without a
    band selection the CreatePayer endpoint has no way to accept — must be a
    clean 400, never an unhandled 500."""
    replace_rate_bands(
        council_revenue_item=scoped["item"], actor=scoped["admin"],
        bands=[{"label": "Small", "rate_mode": RateBand.RANGE, "min_amount": 1000, "max_amount": 5000}],
    )
    before = Payer.objects.count()
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        "/api/v1/payers",
        {
            "payer_type": "INDIVIDUAL", "full_name": "Should Not Be Created", "ward": scoped["ward"].id,
            "revenue_item_ids": [scoped["item"].id],
        },
        format="json",
    )
    assert resp.status_code == 400, resp.data
    assert "error" in resp.data
    # and no half-created payer left behind
    assert Payer.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_admin_can_assign_registered_payer_to_a_consultant(scoped, authed_api_client, make_consultant, make_user):
    consultant = make_consultant(scoped["council"], name="Assignable Co")
    manager = make_user(scoped["council"], username="assignable-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        "/api/v1/payers",
        {"payer_type": "INDIVIDUAL", "full_name": "Assigned Payer", "ward": scoped["ward"].id, "assigned_consultant_id": consultant.id},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    payer = Payer.objects.get(id=resp.data["id"])
    assert payer.enumerated_by_id == manager.id


@pytest.mark.django_db(transaction=True)
def test_registering_payer_without_consultant_assignment_defaults_to_actor(scoped, authed_api_client):
    """The normal case — no assigned_consultant_id given at all — must keep
    working exactly as before: enumerated_by is whoever's actually posting."""
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        "/api/v1/payers",
        {"payer_type": "INDIVIDUAL", "full_name": "Self Registered Payer", "ward": scoped["ward"].id},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    payer = Payer.objects.get(id=resp.data["id"])
    assert payer.enumerated_by_id == scoped["admin"].id


@pytest.mark.django_db(transaction=True)
def test_non_admin_cannot_assign_payer_to_a_different_consultant(scoped, authed_api_client, make_consultant, make_user):
    """A consultant/agent passing assigned_consultant_id has no business doing
    so — silently ignored, same handling UpdateProfileSerializer gives other
    admin-managed fields a non-admin caller has no business setting."""
    consultant = make_consultant(scoped["council"], name="Other Co")
    caller_consultant = make_consultant(scoped["council"], name="Caller Co", contract_ref="CR-2")
    caller = make_user(scoped["council"], username="caller-mgr", access_level=AppRole.CONSULTANT, consultant=caller_consultant)
    client = authed_api_client(caller)
    resp = client.post(
        "/api/v1/payers",
        {"payer_type": "INDIVIDUAL", "full_name": "Sneaky Assignment", "ward": scoped["ward"].id, "assigned_consultant_id": consultant.id},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    payer = Payer.objects.get(id=resp.data["id"])
    assert payer.enumerated_by_id == caller.id


@pytest.mark.django_db(transaction=True)
def test_assigning_payer_to_consultant_with_no_login_is_rejected(scoped, authed_api_client, make_consultant):
    consultant = make_consultant(scoped["council"], name="No Login Co", contract_ref="CR-NL")
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        "/api/v1/payers",
        {"payer_type": "INDIVIDUAL", "full_name": "Should Not Be Created", "ward": scoped["ward"].id, "assigned_consultant_id": consultant.id},
        format="json",
    )
    assert resp.status_code == 400, resp.data


# ------------------------------------------------------------- money guards


@pytest.mark.django_db(transaction=True)
def test_negative_payment_amount_rejected(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    client = authed_api_client(scoped["admin"])
    resp = client.post("/api/v1/payments", {"bill_id": bill.id, "amount": "-500"}, format="json")
    assert resp.status_code == 400
    bill.refresh_from_db()
    assert bill.amount_paid == 0


@pytest.mark.django_db(transaction=True)
def test_zero_payment_amount_rejected(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    client = authed_api_client(scoped["admin"])
    resp = client.post("/api/v1/payments", {"bill_id": bill.id, "amount": "0"}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_negative_bill_line_quantity_rejected(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        "/api/v1/bills",
        {"payer_id": scoped["payer"].id, "lines": [{"revenue_item_id": scoped["item"].id, "quantity": "-1"}]},
        format="json",
    )
    assert resp.status_code == 400
    assert not Bill.objects.filter(payer=scoped["payer"]).exists()


# --------------------------------------------------------------- payments


@pytest.mark.django_db(transaction=True)
def test_payment_serializer_includes_terminal_and_poster(scoped, authed_api_client, make_field_agent, make_terminal, make_user):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    agent_user = make_user(scoped["council"], username="aud-agentuser", access_level=AppRole.AGENT)
    agent = make_field_agent(scoped["council"], agent_user, ward=scoped["ward"])
    terminal = make_terminal(scoped["council"], agent, scoped["ward"])
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    payment = post_payment(
        council_id=scoped["council"].id, bill=bill, channel=channel, amount=10000,
        posted_by=scoped["admin"], terminal=terminal,
    )

    r = authed_api_client(scoped["admin"]).get("/api/v1/payments")
    row = next(row for row in r.json()["results"] if row["id"] == payment.id)
    assert row["terminal_code"] == terminal.terminal_id
    assert row["posted_by_name"] == scoped["admin"].full_name


@pytest.mark.django_db(transaction=True)
def test_payments_endpoint_filters_by_channel_and_q(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    pos, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    ussd, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.USSD)
    post_payment(council_id=scoped["council"].id, bill=bill, channel=pos, amount=4000, posted_by=scoped["admin"])
    post_payment(council_id=scoped["council"].id, bill=bill, channel=ussd, amount=1000, posted_by=scoped["admin"])

    client = authed_api_client(scoped["admin"])
    r = client.get("/api/v1/payments?channel=USSD")
    assert r.status_code == 200
    assert all(row["channel_code"] == "USSD" for row in r.json()["results"])
    assert len(r.json()["results"]) == 1

    r = client.get(f"/api/v1/payments?q={scoped['payer'].full_name}")
    assert len(r.json()["results"]) == 2


@pytest.mark.django_db(transaction=True)
def test_reverse_payment_recomputes_bill_and_marks_reversed(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    payment = post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=10000, posted_by=scoped["admin"])
    bill.refresh_from_db()
    assert bill.status == Bill.PAID

    client = authed_api_client(scoped["admin"])
    resp = client.post(f"/api/v1/payments/{payment.id}/reverse", {"reason": "miskeyed amount"}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["txn_status"] == "REVERSED"

    bill.refresh_from_db()
    assert bill.amount_paid == 0
    assert bill.status == Bill.ISSUED


@pytest.mark.django_db(transaction=True)
def test_reverse_already_reversed_payment_rejected(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    payment = post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=10000, posted_by=scoped["admin"])
    client = authed_api_client(scoped["admin"])
    client.post(f"/api/v1/payments/{payment.id}/reverse", {}, format="json")
    resp = client.post(f"/api/v1/payments/{payment.id}/reverse", {}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_reverse_payment_requires_council_admin(scoped, authed_api_client, make_user):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    payment = post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=10000, posted_by=scoped["admin"])
    consultant_user = make_user(scoped["council"], username="aud-consultant", access_level=AppRole.CONSULTANT)
    resp = authed_api_client(consultant_user).post(f"/api/v1/payments/{payment.id}/reverse", {}, format="json")
    assert resp.status_code == 403


# ------------------------------------------------------------------- KYC


@pytest.mark.django_db(transaction=True)
def test_kyc_status_change_updates_and_audits(scoped, authed_api_client):
    assert scoped["payer"].kyc_status == Payer.PENDING
    client = authed_api_client(scoped["admin"])
    resp = client.post(f"/api/v1/payers/{scoped['payer'].id}/kyc-status", {"kyc_status": "VERIFIED"}, format="json")
    assert resp.status_code == 200, resp.data
    scoped["payer"].refresh_from_db()
    assert scoped["payer"].kyc_status == Payer.VERIFIED


@pytest.mark.django_db(transaction=True)
def test_kyc_status_change_requires_council_admin(scoped, authed_api_client, make_user):
    agent_user = make_user(scoped["council"], username="aud-kycagent", access_level=AppRole.AGENT)
    resp = authed_api_client(agent_user).post(f"/api/v1/payers/{scoped['payer'].id}/kyc-status", {"kyc_status": "VERIFIED"}, format="json")
    assert resp.status_code == 403


# ---------------------------------------------------------------- deletion


@pytest.mark.django_db(transaction=True)
def test_delete_payer_with_no_bills_succeeds(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    resp = client.delete(f"/api/v1/payers/{scoped['payer'].id}")
    assert resp.status_code == 204, resp.content
    assert not Payer.objects.filter(id=scoped["payer"].id).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_payer_with_bills_blocked(scoped, authed_api_client):
    issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    client = authed_api_client(scoped["admin"])
    resp = client.delete(f"/api/v1/payers/{scoped['payer'].id}")
    assert resp.status_code == 409
    assert Payer.objects.filter(id=scoped["payer"].id).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_bill_with_no_payments_succeeds_and_clears_assessments(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    assessment_id = bill.lines.first().assessment_id
    client = authed_api_client(scoped["admin"])
    resp = client.delete(f"/api/v1/bills/{bill.id}")
    assert resp.status_code == 204, resp.content
    assert not Bill.objects.filter(id=bill.id).exists()
    assert not Assessment.objects.filter(id=assessment_id).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_bill_with_payments_blocked(scoped, authed_api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=5000, posted_by=scoped["admin"])
    client = authed_api_client(scoped["admin"])
    resp = client.delete(f"/api/v1/bills/{bill.id}")
    assert resp.status_code == 409
    assert Bill.objects.filter(id=bill.id).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_bill_requires_council_admin(scoped, authed_api_client, make_user):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    consultant_user = make_user(scoped["council"], username="aud-billconsultant", access_level=AppRole.CONSULTANT)
    resp = authed_api_client(consultant_user).delete(f"/api/v1/bills/{bill.id}")
    assert resp.status_code == 403
    assert Bill.objects.filter(id=bill.id).exists()


@pytest.mark.django_db(transaction=True)
def test_delete_payer_requires_council_admin(scoped, authed_api_client, make_user):
    agent_user = make_user(scoped["council"], username="aud-delagent", access_level=AppRole.AGENT)
    resp = authed_api_client(agent_user).delete(f"/api/v1/payers/{scoped['payer'].id}")
    assert resp.status_code == 403
    assert Payer.objects.filter(id=scoped["payer"].id).exists()
