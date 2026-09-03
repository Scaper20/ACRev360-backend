"""
The money-path invariants carried forward from the prototype as non-negotiable —
V2_ARCHITECTURE.md §7: one payment path, terminal-state refusal, arrears
consolidation never double-counts, webhook replays are idempotent.
"""
import hashlib
import hmac
import json
from decimal import Decimal

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
    rows = {row["bill_ref"]: row for row in r.json()["superseded_bills"]}
    assert {ref: row["amount"] for ref, row in rows.items()} == {bill_a.bill_ref: "10000.00", bill_b.bill_ref: "7000.00"}

    # Line-item detail behind each superseded bill's lump amount — see item 6
    # of the frontend's backend requirements doc: roll_arrears never touches
    # a superseded bill's own BillLines, so they were always there to expose.
    assert [line["harmonised_code"] for line in rows[bill_a.bill_ref]["lines"]] == ["MNYITEM"]
    assert rows[bill_a.bill_ref]["lines"][0]["line_amount"] == "10000.00"
    assert [line["harmonised_code"] for line in rows[bill_b.bill_ref]["lines"]] == ["MNYITEM3"]
    assert rows[bill_b.bill_ref]["lines"][0]["line_amount"] == "7000.00"


@pytest.mark.django_db(transaction=True)
def test_public_bill_lookup_also_lists_superseded_bills(scoped, api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    original = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    consolidated = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    r = api_client.get(f"/api/v1/bills/{consolidated.bill_ref}")
    assert r.status_code == 200, r.content
    superseded = r.json()["superseded_bills"]
    assert len(superseded) == 1
    assert superseded[0]["bill_ref"] == original.bill_ref
    assert superseded[0]["amount"] == "10000.00"
    assert [line["harmonised_code"] for line in superseded[0]["lines"]] == ["MNYITEM"]


@pytest.mark.django_db(transaction=True)
def test_multi_level_consolidation_includes_full_recursive_line_history(scoped, authed_api_client, make_revenue_item):
    """Bug: SupersededBillSerializer.lines sourced from the bare `lines`
    manager — one prior bill's own *direct* lines only. A bill consolidated
    more than once (bill1 -> bill2 -> bill3, matching the reported
    000006 -> 000010 -> 000011 case) silently dropped bill1's line once
    bill2 (itself a consolidation of bill1) got superseded by bill3 in turn:
    bill3.supersedes only ever contains bill2 (bill1 is already SUPERSEDED
    and excluded from issue_bill's open_bills query by the time bill3 rolls
    up), so reading bill2.lines directly never reaches bill1's line at all.
    Bill.all_arrears_lines() fixes this by recursing through `supersedes` at
    each level instead of reading `.lines` directly."""
    council, payer, admin = scoped["council"], scoped["payer"], scoped["admin"]
    # Explicit ₦5,000 items, not scoped["item"] (which is ₦10,000) — matches
    # the reported repro's own figures exactly.
    item_a = make_revenue_item(council, code="MNYITEM4A", rate=5000)
    item_b = make_revenue_item(council, code="MNYITEM4B", rate=5000)

    bill1 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)
    bill2 = issue_bill(
        council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}],
        roll_arrears=True, actor=admin,
    )
    bill3 = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)

    bill1.refresh_from_db()
    bill2.refresh_from_db()
    assert bill1.status == Bill.SUPERSEDED
    assert bill2.status == Bill.SUPERSEDED
    assert bill3.supersedes.count() == 1  # bill1 isn't directly here — it's one level deeper, under bill2
    assert bill3.arrears_amount == Decimal("10000")
    assert bill3.total_amount == Decimal("10000")

    # Model-level: the recursive invariant itself.
    all_lines = bill3.all_arrears_lines()
    assert sum((line.line_amount for line in all_lines), start=Decimal("0")) == bill3.total_amount
    assert {line.assessment.council_revenue_item.harmonised_code for line in all_lines} == {
        item_a.harmonised_code, item_b.harmonised_code,
    }

    # API-level: bill3's one superseded_bills entry (bill2) must carry both
    # its own line AND bill1's, not just its own.
    r = authed_api_client(admin).get(f"/api/v1/bills/{bill3.id}/detail")
    assert r.status_code == 200, r.content
    superseded = r.json()["superseded_bills"]
    assert len(superseded) == 1
    assert superseded[0]["bill_ref"] == bill2.bill_ref
    assert superseded[0]["amount"] == "10000.00"
    codes = {line["harmonised_code"] for line in superseded[0]["lines"]}
    assert codes == {item_a.harmonised_code, item_b.harmonised_code}
    line_sum = sum((Decimal(line["line_amount"]) for line in superseded[0]["lines"]), start=Decimal("0"))
    assert line_sum == Decimal(superseded[0]["amount"])


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
