"""
POST /api/v1/receipts/{id}/send — email (Resend) / SMS (Termii) delivery.
Both providers are unconfigured by default in test settings (no API keys),
so most of these exercise the "not configured" / "no contact info" paths for
real rather than mocking; the two "actually sends" tests mock requests.post
directly since hitting the real Resend/Termii APIs from a test run isn't
possible without live keys.
"""
from unittest.mock import patch

import pytest
from django.db import transaction

from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="RCP")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="rcp-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="RCPITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


def _make_receipt(scoped):
    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"], actor=scoped["admin"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    payment = post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=10000, posted_by=scoped["admin"])
    return payment.receipt


@pytest.mark.django_db(transaction=True)
def test_send_with_no_contact_info_attempts_neither_channel(scoped, authed_api_client):
    scoped["payer"].email = ""
    scoped["payer"].phone = ""
    scoped["payer"].save(update_fields=["email", "phone"])
    receipt = _make_receipt(scoped)

    resp = authed_api_client(scoped["admin"]).post(f"/api/v1/receipts/{receipt.id}/send")
    assert resp.status_code == 200, resp.data
    assert resp.data == {
        "email": {"attempted": False, "reason": "no email on file"},
        "sms": {"attempted": False, "reason": "no phone on file"},
    }


@pytest.mark.django_db(transaction=True)
def test_send_with_contact_info_but_no_api_keys_reports_not_configured(scoped, authed_api_client):
    scoped["payer"].email = "payer@example.com"
    scoped["payer"].phone = "08010000000"
    scoped["payer"].save(update_fields=["email", "phone"])
    receipt = _make_receipt(scoped)

    resp = authed_api_client(scoped["admin"]).post(f"/api/v1/receipts/{receipt.id}/send")
    assert resp.status_code == 200, resp.data
    assert resp.data["email"] == {"attempted": False, "reason": "RESEND_API_KEY not configured"}
    assert resp.data["sms"] == {"attempted": False, "reason": "TERMII_API_KEY not configured"}


@pytest.mark.django_db(transaction=True)
def test_send_success_calls_both_providers_and_audits(scoped, authed_api_client, settings):
    settings.RESEND_API_KEY = "test-resend-key"
    settings.TERMII_API_KEY = "test-termii-key"
    scoped["payer"].email = "payer@example.com"
    scoped["payer"].phone = "08010000000"
    scoped["payer"].save(update_fields=["email", "phone"])
    receipt = _make_receipt(scoped)

    with patch("apps.payments.notifications.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        resp = authed_api_client(scoped["admin"]).post(f"/api/v1/receipts/{receipt.id}/send")

    assert resp.status_code == 200, resp.data
    assert resp.data["email"] == {"attempted": True, "sent": True}
    assert resp.data["sms"] == {"attempted": True, "sent": True}
    assert mock_post.call_count == 2

    resend_call, termii_call = mock_post.call_args_list
    assert resend_call.args[0] == "https://api.resend.com/emails"
    assert resend_call.kwargs["json"]["to"] == ["payer@example.com"]
    assert termii_call.args[0] == "https://api.ns.termii.com/api/sms/send"
    assert termii_call.kwargs["json"]["to"] == "2348010000000"  # normalized from 08010000000

    from apps.audit.models import AuditLog
    assert AuditLog.objects.filter(council=scoped["council"], action="RECEIPT_SENT", entity_id=str(receipt.id)).exists()


@pytest.mark.django_db(transaction=True)
def test_send_provider_failure_reported_not_raised(scoped, authed_api_client, settings):
    settings.RESEND_API_KEY = "test-resend-key"
    scoped["payer"].email = "payer@example.com"
    scoped["payer"].phone = ""
    scoped["payer"].save(update_fields=["email", "phone"])
    receipt = _make_receipt(scoped)

    with patch("apps.payments.notifications.requests.post") as mock_post:
        mock_post.return_value.status_code = 422
        mock_post.return_value.text = "Invalid `to` field"
        resp = authed_api_client(scoped["admin"]).post(f"/api/v1/receipts/{receipt.id}/send")

    assert resp.status_code == 200, resp.data
    assert resp.data["email"] == {"attempted": True, "sent": False, "error": "Invalid `to` field"}


@pytest.mark.django_db(transaction=True)
def test_send_requires_receipt_permission_role(scoped, authed_api_client, make_user):
    from apps.accounts.models import AppRole

    receipt = _make_receipt(scoped)
    stakeholder = make_user(scoped["council"], username="rcp-stakeholder", access_level=AppRole.GLOBAL_VIEW)
    resp = authed_api_client(stakeholder).post(f"/api/v1/receipts/{receipt.id}/send")
    assert resp.status_code == 403
