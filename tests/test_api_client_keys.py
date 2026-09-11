"""
API keys: expiry, scoping, and last-used tracking (see APIClient in
apps/payments/models.py and apps.channels.services.authenticate_webhook_client).
is_active already served as the revocation flag and the plaintext secret was
already only ever returned once at creation — both left untouched here.
"""
import datetime
import hashlib
import hmac
import json

import pytest
from django.db import transaction
from django.utils import timezone

from apps.billing.services import issue_bill
from apps.payments.crypto import encrypt_secret
from apps.payments.models import APIClient, PaymentChannel
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="KEY")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="key-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="KEYITEM", rate=10000)
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item, "channel": channel}


def _webhook(api_client, bill_ref, secret, rrn="RRN-1", amount=10000):
    body = json.dumps({"terminalId": "T1", "rrn": rrn, "amount": amount, "billRef": bill_ref}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return api_client.post(
        "/api/v1/channels/POS/webhook", data=body, content_type="application/json",
        HTTP_X_ACREV360_SIGNATURE=sig,
    )


@pytest.mark.django_db(transaction=True)
def test_expired_key_is_rejected_at_webhook_auth(scoped, api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"],
    )
    secret = "expired-secret"
    APIClient.objects.create(
        council=scoped["council"], channel=scoped["channel"], api_key="key_expired",
        secret_encrypted=encrypt_secret(secret), expires_at=timezone.now() - datetime.timedelta(days=1),
    )
    r = _webhook(api_client, bill.bill_ref, secret)
    assert r.status_code == 401, r.content


@pytest.mark.django_db(transaction=True)
def test_key_with_null_expiry_never_expires(scoped, api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"],
    )
    secret = "forever-secret"
    client = APIClient.objects.create(
        council=scoped["council"], channel=scoped["channel"], api_key="key_forever",
        secret_encrypted=encrypt_secret(secret), expires_at=None,
    )
    assert client.last_used_at is None
    r = _webhook(api_client, bill.bill_ref, secret)
    assert r.status_code == 201, r.content
    client.refresh_from_db()
    assert client.last_used_at is not None


@pytest.mark.django_db(transaction=True)
def test_key_missing_required_scope_is_rejected(scoped, api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"],
    )
    secret = "unscoped-secret"
    APIClient.objects.create(
        council=scoped["council"], channel=scoped["channel"], api_key="key_unscoped",
        secret_encrypted=encrypt_secret(secret), scopes=[],
    )
    r = _webhook(api_client, bill.bill_ref, secret)
    assert r.status_code == 401, r.content


@pytest.mark.django_db(transaction=True)
def test_create_key_with_expiry_and_scopes_via_api(scoped, authed_api_client):
    expires = (timezone.now() + datetime.timedelta(days=30)).isoformat()
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/api-clients",
        {"channel": scoped["channel"].id, "expires_at": expires, "scopes": [APIClient.SCOPE_WEBHOOK_POST]},
        format="json",
    )
    assert r.status_code == 201, r.content
    assert "secret" in r.json()
    client = APIClient.objects.get(pk=r.json()["id"])
    assert client.scopes == [APIClient.SCOPE_WEBHOOK_POST]
    assert client.expires_at is not None


@pytest.mark.django_db(transaction=True)
def test_create_key_without_expiry_defaults_to_never_expiring(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/api-clients", {"channel": scoped["channel"].id}, format="json",
    )
    assert r.status_code == 201, r.content
    client = APIClient.objects.get(pk=r.json()["id"])
    assert client.expires_at is None
    assert client.scopes == [APIClient.SCOPE_WEBHOOK_POST]


@pytest.mark.django_db(transaction=True)
def test_revoke_action_deactivates_key_and_blocks_future_webhooks(scoped, authed_api_client, api_client):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"],
    )
    secret = "revoke-me-secret"
    client = APIClient.objects.create(
        council=scoped["council"], channel=scoped["channel"], api_key="key_revoke", secret_encrypted=encrypt_secret(secret),
    )

    r = authed_api_client(scoped["admin"]).post(f"/api/v1/api-clients/{client.id}/revoke")
    assert r.status_code == 200, r.content
    assert r.json()["is_active"] is False

    r2 = _webhook(api_client, bill.bill_ref, secret)
    assert r2.status_code == 401, r2.content
