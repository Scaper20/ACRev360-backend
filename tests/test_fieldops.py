"""
The field-agent mobile app's backend: a ward-scoped worklist and an offline
sync-replay endpoint that funnels queued PAYMENT/PAYER records through the
same post_payment()/create_payer() the online path uses — see
apps.fieldops.services. Built alongside apps/field (the PWA), porting the
old Flask prototype's /api/mobile/worklist and /api/mobile/sync.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.models import Bill
from apps.billing.services import issue_bill
from apps.fieldops.models import MobileSyncRecord
from apps.payments.models import Payment
from apps.registry.models import Payer
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_field_agent, make_revenue_item):
    council = make_council(code="FLD")
    with transaction.atomic():
        set_council_context(council.id)
        ward_a = make_ward(council, code="WA", name="Ward A")
        ward_b = make_ward(council, code="WB", name="Ward B")
        admin = make_user(council, username="fld-admin")
        agent_user = make_user(council, username="fld-agent", access_level=AppRole.AGENT)
        agent = make_field_agent(council, agent_user, ward=ward_a, agent_code="AGT-FLD-1")
        item = make_revenue_item(council, code="FLDITEM", rate=5000)
        yield {
            "council": council, "ward_a": ward_a, "ward_b": ward_b, "admin": admin,
            "agent_user": agent_user, "agent": agent, "item": item,
        }


# --- worklist ---

@pytest.mark.django_db(transaction=True)
def test_worklist_scoped_to_agents_ward(scoped, authed_api_client, make_payer):
    in_ward = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="In Ward", phone="08030000001")
    make_payer(scoped["council"], scoped["ward_b"], scoped["admin"], name="Other Ward", phone="08030000002")

    r = authed_api_client(scoped["agent_user"]).get("/api/v1/mobile/worklist")
    assert r.status_code == 200, r.content
    names = {row["full_name"] for row in r.json()["results"]}
    assert names == {"In Ward"}
    assert r.json()["results"][0]["id"] == in_ward.id


@pytest.mark.django_db(transaction=True)
def test_worklist_empty_when_agent_has_no_ward(scoped, authed_api_client, make_payer, make_field_agent, make_user):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Someone", phone="08030000003")
    no_ward_user = make_user(scoped["council"], username="fld-agent-noward", access_level=AppRole.AGENT)
    make_field_agent(scoped["council"], no_ward_user, ward=None, agent_code="AGT-FLD-NOWARD")

    r = authed_api_client(no_ward_user).get("/api/v1/mobile/worklist")
    assert r.status_code == 200, r.content
    assert r.json()["results"] == []


@pytest.mark.django_db(transaction=True)
def test_worklist_orders_by_outstanding_balance_desc(scoped, authed_api_client, make_payer):
    small = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Small Balance", phone="08030000004")
    large = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Large Balance", phone="08030000005")
    issue_bill(council_id=scoped["council"].id, payer=small, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])
    issue_bill(council_id=scoped["council"].id, payer=large, lines=[{"council_revenue_item": scoped["item"], "quantity": 3}], actor=scoped["admin"])

    r = authed_api_client(scoped["agent_user"]).get("/api/v1/mobile/worklist")
    ordered_names = [row["full_name"] for row in r.json()["results"]]
    assert ordered_names == ["Large Balance", "Small Balance"]


@pytest.mark.django_db(transaction=True)
def test_worklist_search_matches_name_or_ref(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Findable Trader", phone="08030000006")
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Other Trader", phone="08030000007")

    r = authed_api_client(scoped["agent_user"]).get("/api/v1/mobile/worklist?q=Findable")
    assert {row["full_name"] for row in r.json()["results"]} == {"Findable Trader"}


@pytest.mark.django_db(transaction=True)
def test_worklist_forbidden_for_non_agent(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/mobile/worklist")
    assert r.status_code == 403


# --- sync: payments ---

@pytest.mark.django_db(transaction=True)
def test_sync_payment_accepted_and_posted(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer One", phone="08030000008")
    bill = issue_bill(council_id=scoped["council"].id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])

    r = authed_api_client(scoped["agent_user"]).post(
        "/api/v1/mobile/sync",
        {"records": [{"client_id": "c-pay-1", "entity_type": "PAYMENT", "payload": {"bill_id": bill.id, "amount": "5000.00", "channel_code": "OTC"}}]},
        format="json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["client_id"] == "c-pay-1"
    payment = Payment.objects.get(payment_ref=body["accepted"][0]["result_ref"])
    assert payment.posted_by_id == scoped["agent_user"].id
    assert payment.amount == 5000
    assert MobileSyncRecord.objects.filter(client_id="c-pay-1", status=MobileSyncRecord.ACCEPTED).exists()


@pytest.mark.django_db(transaction=True)
def test_sync_payment_retried_client_id_is_idempotent(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer Two", phone="08030000009")
    bill = issue_bill(council_id=scoped["council"].id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])
    body_payload = {"records": [{"client_id": "c-pay-retry", "entity_type": "PAYMENT", "payload": {"bill_id": bill.id, "amount": "5000.00", "channel_code": "OTC"}}]}
    client = authed_api_client(scoped["agent_user"])

    first = client.post("/api/v1/mobile/sync", body_payload, format="json")
    second = client.post("/api/v1/mobile/sync", body_payload, format="json")

    assert first.json()["accepted"][0]["result_ref"] == second.json()["accepted"][0]["result_ref"]
    assert Payment.objects.filter(bill=bill).count() == 1


@pytest.mark.django_db(transaction=True)
def test_sync_payment_against_cancelled_bill_is_rejected_not_500(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer Three", phone="08030000010")
    bill = issue_bill(council_id=scoped["council"].id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])
    bill.status = Bill.CANCELLED
    bill.save(update_fields=["status"])

    r = authed_api_client(scoped["agent_user"]).post(
        "/api/v1/mobile/sync",
        {"records": [{"client_id": "c-pay-cancelled", "entity_type": "PAYMENT", "payload": {"bill_id": bill.id, "amount": "5000.00", "channel_code": "OTC"}}]},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert len(r.json()["rejected"]) == 1
    assert not Payment.objects.filter(bill=bill).exists()


# --- sync: payers ---

@pytest.mark.django_db(transaction=True)
def test_sync_payer_accepted(scoped, authed_api_client):
    r = authed_api_client(scoped["agent_user"]).post(
        "/api/v1/mobile/sync",
        {"records": [{
            "client_id": "c-payer-1", "entity_type": "PAYER",
            "payload": {
                "payer_type": "INDIVIDUAL", "full_name": "New Field Payer", "phone": "08030000011",
                "address": "1 Market Rd", "ward": scoped["ward_a"].id, "revenue_item_ids": [scoped["item"].id],
            },
        }]},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert len(r.json()["accepted"]) == 1
    payer = Payer.objects.get(payer_ref=r.json()["accepted"][0]["result_ref"])
    assert payer.enumerated_by_id == scoped["agent_user"].id
    assert payer.ward_id == scoped["ward_a"].id


@pytest.mark.django_db(transaction=True)
def test_sync_payer_with_geo_creates_enumerated_asset(scoped, authed_api_client):
    from apps.registry.models import EnumeratedAsset

    r = authed_api_client(scoped["agent_user"]).post(
        "/api/v1/mobile/sync",
        {"records": [{
            "client_id": "c-payer-geo", "entity_type": "PAYER",
            "payload": {
                "payer_type": "INDIVIDUAL", "full_name": "Geo Payer", "phone": "08030000014",
                "address": "3 Market Rd", "ward": scoped["ward_a"].id, "geo": {"lat": "9.043200", "lng": "7.397100"},
            },
        }]},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert len(r.json()["accepted"]) == 1
    payer = Payer.objects.get(payer_ref=r.json()["accepted"][0]["result_ref"])
    asset = EnumeratedAsset.objects.get(payer=payer)
    assert asset.asset_type == EnumeratedAsset.PREMISES
    assert str(asset.geo_lat) == "9.043200"


@pytest.mark.django_db(transaction=True)
def test_sync_payer_duplicate_phone_is_conflict_not_created_twice(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Existing Payer", phone="08030000012")

    r = authed_api_client(scoped["agent_user"]).post(
        "/api/v1/mobile/sync",
        {"records": [{
            "client_id": "c-payer-dupe", "entity_type": "PAYER",
            "payload": {
                "payer_type": "INDIVIDUAL", "full_name": "Duplicate Attempt", "phone": "08030000012",
                "address": "2 Market Rd", "ward": scoped["ward_a"].id,
            },
        }]},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert len(r.json()["conflicts"]) == 1
    assert Payer.objects.filter(phone="08030000012").count() == 1


# --- sync: mixed batch + permissions ---

@pytest.mark.django_db(transaction=True)
def test_sync_one_bad_record_does_not_block_the_rest_of_the_batch(scoped, authed_api_client, make_payer):
    payer = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer Four", phone="08030000013")
    bill = issue_bill(council_id=scoped["council"].id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])

    r = authed_api_client(scoped["agent_user"]).post(
        "/api/v1/mobile/sync",
        {"records": [
            {"client_id": "c-bad-bill", "entity_type": "PAYMENT", "payload": {"bill_id": 999999, "amount": "5000.00", "channel_code": "OTC"}},
            {"client_id": "c-good-pay", "entity_type": "PAYMENT", "payload": {"bill_id": bill.id, "amount": "5000.00", "channel_code": "OTC"}},
        ]},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert len(r.json()["rejected"]) == 1
    assert len(r.json()["accepted"]) == 1


@pytest.mark.django_db(transaction=True)
def test_sync_forbidden_for_non_agent(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post("/api/v1/mobile/sync", {"records": []}, format="json")
    assert r.status_code == 403
