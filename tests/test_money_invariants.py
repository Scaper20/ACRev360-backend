"""
The money-path invariants carried forward from the prototype as non-negotiable —
V2_ARCHITECTURE.md §7: one payment path, terminal-state refusal, arrears
consolidation never double-counts, webhook replays are idempotent.
"""
import hashlib
import hmac
import json

import pytest
from django.db import transaction

from apps.billing.models import Bill
from apps.billing.services import BillingError, issue_bill
from apps.payments.crypto import encrypt_secret
from apps.payments.models import APIClient, PaymentChannel
from apps.payments.services import PaymentRejected, post_payment
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    """A council + ward + admin user + payer + revenue item, with RLS context set
    for the duration of the test."""
    council = make_council(code="MNY")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="mny-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="MNYITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


@pytest.mark.django_db(transaction=True)
def test_terminal_bill_states_refuse_payment(scoped):
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)

    bill = issue_bill(
        council_id=council.id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=admin,
    )
    bill.status = Bill.CANCELLED
    bill.save(update_fields=["status"])

    with pytest.raises(PaymentRejected):
        post_payment(council_id=council.id, bill=bill, channel=channel, amount=10000, posted_by=admin)


@pytest.mark.django_db(transaction=True)
def test_superseded_bill_refuses_payment_and_names_successor(scoped):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)

    original = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    consolidated = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    original.refresh_from_db()
    assert original.status == Bill.SUPERSEDED
    assert original.superseded_by_id == consolidated.id

    with pytest.raises(PaymentRejected) as exc:
        post_payment(council_id=council.id, bill=original, channel=channel, amount=10000, posted_by=admin)
    assert consolidated.bill_ref in str(exc.value)


@pytest.mark.django_db(transaction=True)
def test_arrears_consolidation_conserves_outstanding(scoped, make_revenue_item):
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    item_a = scoped["item"]
    item_b = make_revenue_item(council, code="MNYITEM2", rate=7000)

    bill_a = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)
    bill_b = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}], actor=admin)

    outstanding_before = bill_a.balance + bill_b.balance
    assert outstanding_before == 17000

    consolidated = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    bill_a.refresh_from_db()
    bill_b.refresh_from_db()
    assert bill_a.status == Bill.SUPERSEDED
    assert bill_b.status == Bill.SUPERSEDED
    assert consolidated.arrears_amount == 17000
    # billed (own lines) contribution of the consolidated bill is zero — the
    # arrears were already billed once on bill_a/bill_b, not billed again here.
    assert consolidated.total_amount - consolidated.arrears_amount == 0
    # but nothing owed was lost or double-counted:
    assert consolidated.balance == outstanding_before


@pytest.mark.django_db(transaction=True)
def test_bill_detail_lists_which_prior_bills_were_consolidated(scoped, authed_api_client, make_revenue_item):
    # The arrears row only ever showed a lump total — nothing named *which*
    # prior bills it came from, even though Bill.supersedes already has that
    # data. Surfaced live: an admin reading a consolidated bill had no way to
    # see which of a payer's old bills it actually covered.
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    item_a = scoped["item"]
    item_b = make_revenue_item(council, code="MNYITEM3", rate=7000)

    bill_a = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)
    bill_b = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}], actor=admin)
    consolidated = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    r = authed_api_client(admin).get(f"/api/v1/bills/{consolidated.id}/detail")
    assert r.status_code == 200, r.content
    superseded = {row["bill_ref"]: row["amount"] for row in r.json()["superseded_bills"]}
    assert superseded == {bill_a.bill_ref: "10000.00", bill_b.bill_ref: "7000.00"}


@pytest.mark.django_db(transaction=True)
def test_public_bill_lookup_also_lists_superseded_bills(scoped, api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    original = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    consolidated = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    r = api_client.get(f"/api/v1/bills/{consolidated.bill_ref}")
    assert r.status_code == 200, r.content
    assert r.json()["superseded_bills"] == [{"bill_ref": original.bill_ref, "amount": "10000.00"}]


@pytest.mark.django_db(transaction=True)
def test_bill_needs_at_least_one_line_or_arrears(scoped):
    with pytest.raises(BillingError):
        issue_bill(council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"])


@pytest.mark.django_db(transaction=True)
def test_webhook_replay_is_idempotent(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    secret = "test-webhook-secret"
    APIClient.objects.create(council=council, channel=channel, api_key="key_test", secret_encrypted=encrypt_secret(secret))

    body = json.dumps({"terminalId": "T1", "rrn": "RRN-IDEMPOTENT-1", "amount": 10000, "billRef": bill.bill_ref}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    client = authed_api_client(admin)
    r1 = client.post(
        "/api/v1/channels/POS/webhook", data=body, content_type="application/json",
        HTTP_X_ACREV360_SIGNATURE=sig,
    )
    assert r1.status_code == 201, r1.content
    r2 = client.post(
        "/api/v1/channels/POS/webhook", data=body, content_type="application/json",
        HTTP_X_ACREV360_SIGNATURE=sig,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"

    bill.refresh_from_db()
    assert bill.amount_paid == 10000  # not double-posted
    assert bill.payments.count() == 1
