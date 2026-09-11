"""
CASH is a distinct payment channel from OTC (over-the-counter teller) — a cash
payment has no bank-side transaction feed to reconcile against by definition,
so it must never surface as a reconciliation exception. See PaymentChannel.CASH
and PaymentChannel.NO_FEED_EXPECTED.
"""
import pytest
from django.db import transaction

from apps.payments.models import ChannelTransactionFeed, Payment, PaymentChannel
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="CSH")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="csh-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="CSHITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


@pytest.mark.django_db(transaction=True)
def test_cash_is_in_channel_catalogue_with_no_required_fields(scoped, api_client):
    r = api_client.get("/api/v1/channels")
    assert r.status_code == 200, r.content
    entries = {row["code"]: row for row in r.json()}
    assert "CASH" in entries
    assert entries["CASH"]["required_fields"] == []


@pytest.mark.django_db(transaction=True)
def test_cash_selectable_on_payment_recording(scoped, authed_api_client):
    from apps.billing.services import issue_bill

    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"],
    )
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/payments",
        {"bill_id": bill.id, "amount": "10000.00", "channel_code": PaymentChannel.CASH},
        format="json",
    )
    assert r.status_code == 201, r.content
    payment = Payment.objects.get(pk=r.json()["id"])
    assert payment.channel.code == PaymentChannel.CASH
    assert payment.bank_txn_ref == ""
    # No bank feed exists for cash — nothing to match against, so no feed row at all.
    assert not ChannelTransactionFeed.objects.filter(channel=payment.channel).exists()


@pytest.mark.django_db(transaction=True)
def test_cash_webhook_is_rejected(scoped, api_client):
    """CASH has no bank/webhook integration — it's recorded directly, never via
    the generic channel webhook."""
    r = api_client.post(
        "/api/v1/channels/CASH/webhook", data={"amount": "100"}, content_type="application/json",
    )
    assert r.status_code == 400, r.content
    assert "Unknown channel code" in r.json()["error"]
