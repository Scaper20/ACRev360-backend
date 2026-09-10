"""
PR 7 Part A: an agent's payer/bill/payment lists show only payers they
personally registered (Payer.enumerated_by), not their whole consultant's
portfolio. Payer.enumerated_by already gets set at registration
(create_payer()) — this is purely a scoping-layer change in
apps.common.scoping.portfolio_filter.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.registry.models import Payer
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_consultant, make_revenue_item):
    council = make_council(code="AGS")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="ags-admin")
        consultant = make_consultant(council, name="AGS Co", contract_ref="CR-AGS")
        agent_a = make_user(council, username="ags-agent-a", access_level=AppRole.AGENT, consultant=consultant)
        agent_b = make_user(council, username="ags-agent-b", access_level=AppRole.AGENT, consultant=consultant)
        payer_a = Payer.objects.create(
            council=council, payer_ref="C-0000101", payer_type=Payer.BUSINESS,
            full_name="Registered By A", phone="08050000001", ward=ward, enumerated_by=agent_a,
        )
        payer_b = Payer.objects.create(
            council=council, payer_ref="C-0000102", payer_type=Payer.BUSINESS,
            full_name="Registered By B", phone="08050000002", ward=ward, enumerated_by=agent_b,
        )
        item = make_revenue_item(council, code="AGSITEM", rate=10000)
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        yield {
            "council": council, "ward": ward, "admin": admin, "consultant": consultant,
            "agent_a": agent_a, "agent_b": agent_b, "payer_a": payer_a, "payer_b": payer_b,
            "item": item, "channel": channel,
        }


@pytest.mark.django_db(transaction=True)
def test_agent_payer_list_scoped_to_own_registrations(scoped, authed_api_client):
    r = authed_api_client(scoped["agent_a"]).get("/api/v1/payers")
    assert r.status_code == 200, r.content
    ids = {row["id"] for row in r.json()["results"]}
    assert ids == {scoped["payer_a"].id}


@pytest.mark.django_db(transaction=True)
def test_agent_bill_list_scoped_to_own_registrations(scoped, authed_api_client):
    council, admin, item = scoped["council"], scoped["admin"], scoped["item"]
    bill_a = issue_bill(council_id=council.id, payer=scoped["payer_a"], lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    issue_bill(council_id=council.id, payer=scoped["payer_b"], lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    r = authed_api_client(scoped["agent_a"]).get("/api/v1/bills")
    assert r.status_code == 200, r.content
    ids = {row["id"] for row in r.json()["results"]}
    assert ids == {bill_a.id}


@pytest.mark.django_db(transaction=True)
def test_agent_payment_list_scoped_to_own_registrations(scoped, authed_api_client):
    council, admin, item, channel = scoped["council"], scoped["admin"], scoped["item"], scoped["channel"]
    bill_a = issue_bill(council_id=council.id, payer=scoped["payer_a"], lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    bill_b = issue_bill(council_id=council.id, payer=scoped["payer_b"], lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    payment_a = post_payment(council_id=council.id, bill=bill_a, channel=channel, amount=10000, posted_by=admin)
    post_payment(council_id=council.id, bill=bill_b, channel=channel, amount=10000, posted_by=admin)

    r = authed_api_client(scoped["agent_a"]).get("/api/v1/payments")
    assert r.status_code == 200, r.content
    ids = {row["id"] for row in r.json()["results"]}
    assert ids == {payment_a.id}


@pytest.mark.django_db(transaction=True)
def test_consultant_can_assign_payer_to_agent(scoped, authed_api_client, make_user):
    from apps.accounts.models import FieldAgent

    manager = make_user(scoped["council"], username="ags-manager2", access_level=AppRole.CONSULTANT, consultant=scoped["consultant"])
    agent_field = FieldAgent.objects.create(council=scoped["council"], agent_code="AGT-B", user=scoped["agent_b"])

    r = authed_api_client(manager).post(
        f"/api/v1/agents/{agent_field.id}/assign-payer", {"payer_id": scoped["payer_a"].id}, format="json",
    )
    assert r.status_code == 200, r.content
    scoped["payer_a"].refresh_from_db()
    assert scoped["payer_a"].assigned_agent_id == scoped["agent_b"].id


@pytest.mark.django_db(transaction=True)
def test_assigned_payer_moves_exclusively_to_new_agent(scoped, authed_api_client, make_user):
    """Once assigned away, the ORIGINAL registering agent loses visibility —
    "exclusive to agent" per the client's chosen design — while the new
    agent gains it and the consultant manager keeps seeing everything."""
    from apps.accounts.models import FieldAgent

    manager = make_user(scoped["council"], username="ags-manager3", access_level=AppRole.CONSULTANT, consultant=scoped["consultant"])
    agent_field_b = FieldAgent.objects.create(council=scoped["council"], agent_code="AGT-B2", user=scoped["agent_b"])

    authed_api_client(manager).post(
        f"/api/v1/agents/{agent_field_b.id}/assign-payer", {"payer_id": scoped["payer_a"].id}, format="json",
    )

    r_a = authed_api_client(scoped["agent_a"]).get("/api/v1/payers")
    assert {row["id"] for row in r_a.json()["results"]} == set()

    r_b = authed_api_client(scoped["agent_b"]).get("/api/v1/payers")
    # agent_b sees both: payer_a (newly assigned) and payer_b (their own,
    # via the enumerated_by fallback — unaffected by the assignment above).
    assert {row["id"] for row in r_b.json()["results"]} == {scoped["payer_a"].id, scoped["payer_b"].id}


@pytest.mark.django_db(transaction=True)
def test_consultant_still_sees_whole_team_portfolio(scoped, authed_api_client, make_user):
    """Confirms the AGENT branch didn't accidentally narrow CONSULTANT/
    REVENUE_OFFICER scoping — a consultant manager still sees every agent's
    payers under them, not just their own."""
    consultant_manager = make_user(
        scoped["council"], username="ags-manager", access_level=AppRole.CONSULTANT, consultant=scoped["consultant"],
    )
    r = authed_api_client(consultant_manager).get("/api/v1/payers")
    assert r.status_code == 200, r.content
    ids = {row["id"] for row in r.json()["results"]}
    assert ids == {scoped["payer_a"].id, scoped["payer_b"].id}
