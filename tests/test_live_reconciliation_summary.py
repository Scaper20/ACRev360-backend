"""
PR 6: an always-current reconciliation dashboard view (GET
/reconciliation/live-summary) alongside the existing on-demand manual run —
computed fresh on every call, no ReconciliationRun/Exception rows touched.
Reuses the same matching rule as run_reconciliation via the shared
_match_feed_rows helper, so the two can never quietly define "matched"
differently from each other.
"""
import datetime

import pytest
from django.db import transaction

from apps.billing.services import issue_bill
from apps.payments.models import ChannelTransactionFeed, PaymentChannel
from apps.payments.services import post_payment
from apps.reconciliation.models import ReconciliationRun
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="LRS")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="lrs-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="LRSITEM", rate=10000)
        pos, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item, "pos": pos}


@pytest.mark.django_db(transaction=True)
def test_live_summary_reflects_current_totals_without_a_run(scoped, authed_api_client):
    council, payer, admin, item, pos = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item"], scoped["pos"],
    )
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill, channel=pos, amount=10000, posted_by=admin, bank_txn_ref="BNK-LRS-1")
    ChannelTransactionFeed.objects.create(
        council_id=council.id, channel=pos, bank_txn_ref="BNK-LRS-1", amount=10000,
    )

    r = authed_api_client(admin).get("/api/v1/reconciliation/live-summary")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total_platform"] == "10000.00"
    assert body["total_bank"] == "10000.00"
    assert body["unmatched_credits"] == []
    # Purely computed — no run was triggered to get this answer.
    assert not ReconciliationRun.objects.filter(council_id=council.id).exists()


@pytest.mark.django_db(transaction=True)
def test_live_summary_lists_unmatched_bank_credits(scoped, authed_api_client):
    council, admin, pos = scoped["council"], scoped["admin"], scoped["pos"]
    ChannelTransactionFeed.objects.create(council_id=council.id, channel=pos, bank_txn_ref="BNK-ORPHAN", amount=5000)

    r = authed_api_client(admin).get("/api/v1/reconciliation/live-summary")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total_bank"] == "5000.00"
    assert len(body["unmatched_credits"]) == 1
    assert body["unmatched_credits"][0]["bank_txn_ref"] == "BNK-ORPHAN"


@pytest.mark.django_db(transaction=True)
def test_live_summary_excludes_cash_from_bank_side_but_includes_it_platform_side(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    cash, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.CASH)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill, channel=cash, amount=10000, posted_by=admin)

    r = authed_api_client(admin).get("/api/v1/reconciliation/live-summary")
    assert r.status_code == 200, r.content
    body = r.json()
    # Cash counts toward "what we should have" overall...
    assert body["total_platform"] == "10000.00"
    # ...but never shows up as a false unmatched-bank-credit exception, since
    # it has no feed by definition.
    assert body["total_bank"] == "0.00"
    assert body["unmatched_credits"] == []


@pytest.mark.django_db(transaction=True)
def test_live_summary_accepts_explicit_date_param(scoped, authed_api_client):
    council, admin, pos = scoped["council"], scoped["admin"], scoped["pos"]
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    ChannelTransactionFeed.objects.create(
        council_id=council.id, channel=pos, bank_txn_ref="BNK-TODAY", amount=1000,
    )

    r = authed_api_client(admin).get(f"/api/v1/reconciliation/live-summary?date={yesterday}")
    assert r.status_code == 200, r.content
    assert r.json()["total_bank"] == "0.00"
    assert r.json()["unmatched_credits"] == []


@pytest.mark.django_db(transaction=True)
def test_manual_run_action_still_works_alongside_live_summary(scoped, authed_api_client):
    """The existing per-channel/per-date run is left completely untouched."""
    council, admin, pos = scoped["council"], scoped["admin"], scoped["pos"]
    client = authed_api_client(admin)

    r = client.get("/api/v1/reconciliation/live-summary")
    assert r.status_code == 200, r.content

    run_resp = client.post(
        "/api/v1/reconciliation/run",
        {"date": datetime.date.today().isoformat(), "channel_code": PaymentChannel.POS},
        format="json",
    )
    assert run_resp.status_code == 201, run_resp.content
    assert run_resp.json()["status"] == "BALANCED"
