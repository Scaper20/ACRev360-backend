"""
Regression tests for the 2026 hardening pass — one test per behavior change the
pass made, so the suite catches a backslide even if a later edit touches only
the happy path:

- S2 — platform-tier (council=null) list reads no longer come back empty
  (PlatformWideListMixin materializes per-council inside RLS contexts).
- S3 — the partial UNIQUE(channel, bank_txn_ref) turns a duplicate bank
  reference into a 409, never a double post or a 500.
- S4 — a must-change-password account is 428-gated to everything except the
  whitelisted auth endpoints until it changes its password, then the flag is
  lifted and the same token's behaviour reflects that on a fresh login.
- S7 — OTC settlement idempotency via get_or_create on the feed row.
- U1 — malformed list-filter params (payer/date/department) 400 instead of 500.
- U2 — otherwise-hardcoded 404s (portfolio end, exception resolve).
- U4 — bills gained a pk-based retrieve (previously absent).
- U6 — deleting a bill line that has FIFO allocations is refused with 409.
- U8 — a zero/negative assessment quantity or line_amount is rejected.
"""
from decimal import Decimal

import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.payments.models import Payment, PaymentChannel
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council


def _rows(payload):
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="HAR")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="har-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="HARITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


def _bill(scoped, quantity=1):
    return issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": quantity}], actor=scoped["admin"],
    )


# --------------------------------------------------------------------------
# S3 — duplicate (channel, bank_txn_ref) is a 409, not a double post.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_duplicate_bank_txn_ref_returns_409_not_double_post(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    bill = _bill(scoped, quantity=2)  # 20000 outstanding, so the re-post would be processed
    body = {"bill_id": bill.id, "amount": "10000.00", "channel_code": PaymentChannel.POS, "bank_txn_ref": "RRN-DUP-1"}

    first = client.post("/api/v1/payments", body, format="json")
    assert first.status_code == 201, first.content

    replay = client.post("/api/v1/payments", body, format="json")
    assert replay.status_code == 409, replay.content
    assert "already recorded" in replay.json()["error"]

    bill.refresh_from_db()
    assert Payment.objects.filter(bill=bill).count() == 1
    assert bill.amount_paid == Decimal("10000.00")


# --------------------------------------------------------------------------
# S7 — OTC settlement is safe to re-send (same-ref rows are skipped).
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_otc_settlement_dedupes_repeated_references(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    bill = _bill(scoped)
    row = {"tellerRef": "OTC-SAME", "branchCode": "BR1", "amount": 10000, "billRef": bill.bill_ref}

    first = client.post("/api/v1/channels/OTC/settlement", [row, row], format="json")
    assert first.status_code == 200, first.content
    assert first.json()["posted"] == 1
    assert first.json()["duplicates_skipped"] == 1
    assert first.json()["exceptions"] == []

    replay = client.post("/api/v1/channels/OTC/settlement", [row], format="json")
    assert replay.json()["posted"] == 0
    assert replay.json()["duplicates_skipped"] == 1

    bill.refresh_from_db()
    assert Payment.objects.filter(bill=bill).count() == 1
    assert bill.amount_paid == Decimal("10000.00")


# --------------------------------------------------------------------------
# U5 — an OTC settlement body that isn't a list is a 400, not silently "[]".
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_otc_settlement_rejects_non_list_body(scoped, authed_api_client):
    resp = authed_api_client(scoped["admin"]).post(
        "/api/v1/channels/OTC/settlement", {"tellerRef": "X"}, format="json"
    )
    assert resp.status_code == 400
    assert "array" in resp.json()["error"]


# --------------------------------------------------------------------------
# S2 — platform-tier (council=null) list sees every council, none empty.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_platform_wide_consultant_list_across_councils(make_council, make_consultant, authed_api_client):
    council_a = make_council(code="PAA")
    council_b = make_council(code="PBB")
    with council_arg_context(council_a):
        make_consultant(council_a, name="Alpha Firm", contract_ref="CR-ALPHA")
    with council_arg_context(council_b):
        make_consultant(council_b, name="Beta Firm", contract_ref="CR-BETA")

    role, _ = AppRole.objects.get_or_create(name="PLATFORM_ADMIN_PLAT", defaults={"access_level": AppRole.PLATFORM_ADMIN})
    from apps.accounts.models import AppUser

    platform = AppUser.objects.create_user(username="plat-admin", password="testpass12345", full_name="Platform Admin", council=None, role=role)
    resp = authed_api_client(platform).get("/api/v1/consultants")
    assert resp.status_code == 200, resp.content
    names = {row["consultant_name"] for row in _rows(resp.json())}
    assert names == {"Alpha Firm", "Beta Firm"}


from contextlib import contextmanager  # noqa: E402


@contextmanager
def council_arg_context(council):
    with transaction.atomic():
        set_council_context(council.id)
        yield


# --------------------------------------------------------------------------
# S4 — must-change-password 428 gate, whitelist, and post-change lift.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_must_change_password_gates_non_whitelisted_until_changed(scoped, authed_api_client):
    from apps.accounts.models import AppUser
    from apps.accounts.tokens import AppTokenObtainPairSerializer

    user = AppUser.objects.create_user(
        username="forced-user", password="testpass12345", full_name="Forced", council=scoped["council"],
        role=scoped["admin"].role, must_change_password=True,
    )
    client = authed_api_client(user)

    locked = client.get("/api/v1/bills")
    assert locked.status_code == 428
    # DRF's exception handler keeps the `code` sibling for the frontend to branch on.
    assert locked.json()["code"] == "password_change_required"

    # The whitelisted auth endpoints stay reachable so the user can act.
    assert client.get("/api/v1/auth/me").status_code == 200
    changed = client.post(
        "/api/v1/auth/change-password",
        {"current_password": "testpass12345", "new_password": "FreshPass-2026"},
        format="json",
    )
    assert changed.status_code == 204, changed.content
    user.refresh_from_db()
    assert not user.must_change_password

    # The forced-change claim is baked into the old token, so re-login (a fresh
    # token built after the flag cleared) is what unblocks the rest of the API.
    fresh_access = AppTokenObtainPairSerializer.get_token(user).access_token
    assert fresh_access["must_change_password"] is False
    from rest_framework.test import APIClient

    fresh_client = APIClient()
    fresh_client.credentials(HTTP_AUTHORIZATION=f"Bearer {fresh_access}")
    assert fresh_client.get("/api/v1/bills").status_code == 200


# --------------------------------------------------------------------------
# U4 — bills gained a pk-based retrieve.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_bill_retrieve_by_pk(scoped, authed_api_client):
    bill = _bill(scoped)
    resp = authed_api_client(scoped["admin"]).get(f"/api/v1/bills/{bill.id}")
    assert resp.status_code == 200, resp.content
    assert resp.json()["bill_ref"] == bill.bill_ref
    assert resp.json()["total_amount"] == "10000.00"


# --------------------------------------------------------------------------
# U6 — a bill line with FIFO allocations can't be deleted.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_delete_line_with_allocations_is_409(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    bill = _bill(scoped)
    paid = client.post(
        "/api/v1/payments",
        {"bill_id": bill.id, "amount": "10000.00", "channel_code": PaymentChannel.CASH},
        format="json",
    )
    assert paid.status_code == 201, paid.content

    line_id = bill.lines.get().id
    resp = client.delete(f"/api/v1/bills/{bill.id}/lines/{line_id}")
    assert resp.status_code == 409, resp.content
    assert "can't be deleted" in resp.json()["error"]


# --------------------------------------------------------------------------
# U8 — zero/negative quantities and line amounts are rejected.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_zero_quantity_cannot_be_assessed(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    resp = client.post(
        "/api/v1/bills",
        {"payer_id": scoped["payer"].id, "lines": [{"revenue_item_id": scoped["item"].id, "quantity": 0}]},
        format="json",
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# U1 — malformed filter params 400, not 500.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_payments_list_rejects_bad_payer_and_date_params(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    assert client.get("/api/v1/payments", {"payer": "abc"}).status_code == 400
    assert client.get("/api/v1/payments", {"date_from": "2026-13-01"}).status_code == 400


@pytest.mark.django_db(transaction=True)
def test_revenue_items_rejects_bad_department_param(scoped, authed_api_client):
    resp = authed_api_client(scoped["admin"]).get("/api/v1/revenue-items", {"department": "abc"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# U2 — hardcoded .get()s that used to 500 now 404.
# --------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_end_portfolio_unknown_id_is_404(scoped, authed_api_client, make_consultant):
    with council_arg_context(scoped["council"]):
        consultant = make_consultant(scoped["council"], contract_ref="CR-END")
    resp = authed_api_client(scoped["admin"]).post(f"/api/v1/consultants/{consultant.id}/portfolio/999999/end")
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_resolve_exception_unknown_id_is_404(scoped, authed_api_client):
    resp = authed_api_client(scoped["admin"]).post(
        "/api/v1/reconciliation/exceptions/999999/resolve", {"note": "nope"}, format="json"
    )
    assert resp.status_code == 404