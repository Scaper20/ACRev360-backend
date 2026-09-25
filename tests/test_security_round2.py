"""
Regression tests for the second security review (2026-09-24 deep probe): every
finding it produced that was fixable in code has a test here that fails without
the fix. Findings are referred to by the short names used in the review.
"""
import threading

import pytest
from django.core.cache import cache
from django.db import connection, transaction
from rest_framework.throttling import SimpleRateThrottle

from apps.accounts.models import AppRole, AppUser
from apps.audit.models import AuditLog
from apps.billing.models import Bill
from apps.billing.services import DuplicateBill, issue_bill
from apps.payments.models import Receipt
from apps.registry.models import Payer
from apps.tenancy.context import set_council_context


@pytest.fixture
def world(make_council, make_ward, make_user, make_payer, make_revenue_item, make_field_agent):
    """One council, an admin, an agent registered as the owner of one payer,
    a second payer the agent has nothing to do with, and a bill on each."""
    council = make_council(code="SR2")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="sr2-admin")
        agent_user = make_user(council, username="sr2-agent", access_level=AppRole.AGENT)
        make_field_agent(council, agent_user, ward=ward, agent_code="AGT-SR2")
        item = make_revenue_item(council, code="SR2ITEM", rate=10000)
        own_payer = make_payer(council, ward, agent_user, name="Amina Bello", phone="08031234567")
        other_payer = make_payer(council, ward, admin, name="Chidi Okafor", phone="08037654321")
        own_bill = issue_bill(
            council_id=council.id, payer=own_payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin,
        )
        other_bill = issue_bill(
            council_id=council.id, payer=other_payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin,
        )
        yield {
            "council": council, "ward": ward, "admin": admin, "agent_user": agent_user, "item": item,
            "own_payer": own_payer, "other_payer": other_payer, "own_bill": own_bill, "other_bill": other_bill,
        }


# --- public bill lookup: identity masked for anonymous callers -------------------

@pytest.mark.django_db(transaction=True)
def test_anonymous_bill_lookup_masks_payer_identity(world, api_client):
    r = api_client.get(f"/api/v1/bills/{world['own_bill'].bill_ref}")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["pii_masked"] is True
    assert body["full_name"] == "A*** B***"
    assert body["phone"].endswith("567") and body["phone"].count("*") == 8
    assert body["address"] == ""
    assert body["payer_ref"] != world["own_payer"].payer_ref
    # What a ratepayer actually needs is still there.
    assert body["bill_ref"] == world["own_bill"].bill_ref
    assert float(body["balance"]) == 10000 and body["status"] == "ISSUED"
    for leaked in ("Amina", "Bello", "08031234567"):
        assert leaked not in r.content.decode()


@pytest.mark.django_db(transaction=True)
def test_staff_of_the_bills_council_get_full_detail(world, authed_api_client):
    r = authed_api_client(world["admin"]).get(f"/api/v1/bills/{world['own_bill'].bill_ref}")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["pii_masked"] is False
    assert body["full_name"] == "Amina Bello" and body["phone"] == "08031234567"
    assert body["payer_ref"] == world["own_payer"].payer_ref


@pytest.mark.django_db(transaction=True)
def test_agent_sees_full_detail_only_for_their_own_payers(world, authed_api_client):
    client = authed_api_client(world["agent_user"])
    own = client.get(f"/api/v1/bills/{world['own_bill'].bill_ref}").json()
    other = client.get(f"/api/v1/bills/{world['other_bill'].bill_ref}").json()
    assert own["pii_masked"] is False and own["full_name"] == "Amina Bello"
    assert other["pii_masked"] is True and other["full_name"] == "C*** O***"


@pytest.mark.django_db(transaction=True)
def test_staff_of_another_council_get_masked_detail(world, make_council, make_user, authed_api_client):
    other_council = make_council(code="SR2B")
    with transaction.atomic():
        set_council_context(other_council.id)
        outsider = make_user(other_council, username="sr2-outsider")
    r = authed_api_client(outsider).get(f"/api/v1/bills/{world['own_bill'].bill_ref}")
    assert r.status_code == 200, r.content
    assert r.json()["pii_masked"] is True


@pytest.mark.django_db(transaction=True)
def test_public_lookup_is_rate_limited_per_client(world, api_client, monkeypatch):
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"public_lookup": "3/min"})
    cache.clear()
    codes = [api_client.get(f"/api/v1/bills/{world['own_bill'].bill_ref}").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    # Unknown references are counted too — a scan can't dodge the limit by missing.
    assert api_client.get("/api/v1/bills/SR2/2026/999999").status_code == 429


@pytest.mark.django_db(transaction=True)
def test_receipt_verification_counts_atomically_and_is_rate_limited(world, api_client, monkeypatch):
    from apps.payments.models import PaymentChannel
    from apps.payments.services import post_payment

    with transaction.atomic():
        set_council_context(world["council"].id)
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.CASH)
        payment = post_payment(
            council_id=world["council"].id, bill=world["own_bill"], channel=channel, amount=1000, posted_by=world["admin"],
        )
        token = payment.receipt.qr_token
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"public_lookup": "3/min"})
    cache.clear()
    counts = [api_client.get(f"/api/v1/verify/{token}") for _ in range(4)]
    assert [r.status_code for r in counts] == [200, 200, 200, 429]
    assert [r.json()["verified_count"] for r in counts[:3]] == [1, 2, 3]
    with transaction.atomic():
        set_council_context(world["council"].id)
        assert Receipt.objects.get(qr_token=token).verified_count == 3


# --- unauthenticated input must be a 4xx, never a 500 ----------------------------

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("body", [{"text": 123}, {"text": ["1", "2"]}, {"text": None}, [1, 2, 3], "text"])
def test_ussd_rejects_malformed_input_without_a_500(api_client, body):
    r = api_client.post("/api/v1/channels/USSD/session", body, format="json")
    assert r.status_code == 200
    assert r.content.decode() == "END Invalid input."


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("amount", ["abc", "NaN", "Infinity", "-5", "0", None, [1], {"a": 1}, True, "1e999999"])
def test_webhook_bad_amount_is_a_400_not_a_500(world, api_client, amount):
    payload = {"terminalId": "T1", "rrn": "R1", "amount": amount, "billRef": world["own_bill"].bill_ref}
    r = api_client.post("/api/v1/channels/POS/webhook", payload, format="json")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("payload", [[1, 2], "x", 7, {"billRef": 5, "amount": 1, "rrn": "r", "terminalId": "t"}])
def test_webhook_non_object_or_non_string_billref_is_a_400(api_client, payload):
    r = api_client.post("/api/v1/channels/POS/webhook", payload, format="json")
    assert r.status_code == 400, r.content


# --- weak passwords at onboarding -----------------------------------------------

_WEAK = ["1", "password", "short", "acrev360-2026", "MyAcrev360Pass!", "1234567890"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("weak", _WEAK)
def test_stakeholder_onboarding_rejects_weak_passwords(world, authed_api_client, weak):
    r = authed_api_client(world["admin"]).post(
        "/api/v1/stakeholders", {"email": "weak-stake@example.com", "full_name": "Weak Stake", "password": weak}, format="json",
    )
    assert r.status_code == 400, r.content
    assert "password" in r.json()
    assert not AppUser.objects.filter(username="weak-stake").exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("weak", ["1", "password", "acrev360-2026"])
def test_agent_and_ratepayer_onboarding_reject_weak_passwords(world, authed_api_client, weak):
    client = authed_api_client(world["admin"])
    agent = client.post("/api/v1/agents", {"email": "weak-agent@example.com", "full_name": "Weak Agent", "password": weak}, format="json")
    assert agent.status_code == 400 and "password" in agent.json(), agent.content
    invite = client.post(
        f"/api/v1/payers/{world['own_payer'].id}/invite-ratepayer",
        {"email": "weak-ratepayer@example.com", "password": weak}, format="json",
    )
    assert invite.status_code == 400 and "password" in invite.json(), invite.content
    assert not AppUser.objects.filter(username__in=["weak-agent", "weak-ratepayer"]).exists()


@pytest.mark.django_db(transaction=True)
def test_strong_explicit_password_is_still_accepted(world, authed_api_client):
    r = authed_api_client(world["admin"]).post(
        "/api/v1/stakeholders",
        {"email": "strong-stake@example.com", "full_name": "Strong Stake", "password": "Correct-Horse-Battery-7"}, format="json",
    )
    assert r.status_code == 201, r.content
    assert AppUser.objects.get(username="strong-stake").check_password("Correct-Horse-Battery-7")


@pytest.mark.django_db(transaction=True)
def test_omitting_the_password_still_works(world, authed_api_client):
    r = authed_api_client(world["admin"]).post(
        "/api/v1/stakeholders", {"email": "default-stake@example.com", "full_name": "Default Stake"}, format="json",
    )
    assert r.status_code == 201, r.content


# --- duplicate bill race --------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_concurrent_bill_issue_for_one_payer_creates_exactly_one_bill(
    make_council, make_ward, make_user, make_payer, make_revenue_item,
):
    """Six simultaneous issue attempts (a double-click, a retry, two staff) — the
    row lock on the payer makes all but one see the first bill and raise
    DuplicateBill. Before the lock they all passed the check together (4 open
    bills from 6 requests in the original probe). The setup is committed for
    real (no surrounding transaction) so the other connections can see it."""
    council = make_council(code="RCE")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="rce-admin")
        item = make_revenue_item(council, code="RCEITEM", rate=10000)
        payer = make_payer(council, ward, admin)

    outcomes, errors = [], []
    barrier = threading.Barrier(6)

    def attempt():
        try:
            barrier.wait()
            with transaction.atomic():
                set_council_context(council.id)
                issue_bill(
                    council_id=council.id, payer=Payer.objects.get(pk=payer.pk),
                    lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin,
                )
            outcomes.append("issued")
        except DuplicateBill:
            outcomes.append("duplicate")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert sorted(outcomes) == ["duplicate"] * 5 + ["issued"]
    with transaction.atomic():
        set_council_context(council.id)
        assert Bill.objects.filter(payer=payer).exclude(status__in=Bill.TERMINAL_STATUSES).count() == 1


# --- mobile worklist: an agent's own payers only ---------------------------------

@pytest.mark.django_db(transaction=True)
def test_worklist_shows_only_payers_the_agent_owns_or_was_assigned(world, authed_api_client):
    with transaction.atomic():
        set_council_context(world["council"].id)
        # Assigned to this agent by a supervisor — belongs on the list.
        assigned = Payer.objects.get(pk=world["other_payer"].pk)
        assigned.assigned_agent = world["agent_user"]
        assigned.save(update_fields=["assigned_agent"])
    r = authed_api_client(world["agent_user"]).get("/api/v1/mobile/worklist")
    assert r.status_code == 200, r.content
    assert {row["full_name"] for row in r.json()["results"]} == {"Amina Bello", "Chidi Okafor"}


@pytest.mark.django_db(transaction=True)
def test_worklist_hides_other_agents_and_other_firms_payers(world, make_user, make_payer, make_field_agent, authed_api_client):
    with transaction.atomic():
        set_council_context(world["council"].id)
        rival = make_user(world["council"], username="sr2-rival-agent", access_level=AppRole.AGENT)
        make_field_agent(world["council"], rival, ward=world["ward"], agent_code="AGT-SR2-RIVAL")
        make_payer(world["council"], world["ward"], rival, name="Rival Firm Payer", phone="08035550000")
        reassigned = Payer.objects.get(pk=world["own_payer"].pk)
        reassigned.assigned_agent = rival  # handed to somebody else: leaves this agent's view
        reassigned.save(update_fields=["assigned_agent"])
    r = authed_api_client(world["agent_user"]).get("/api/v1/mobile/worklist")
    assert r.status_code == 200, r.content
    assert r.json()["results"] == []  # the rival's payer, the admin's payer, and the reassigned one


# --- NUL bytes in the query string -----------------------------------------------

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["/api/v1/payers", "/api/v1/bills", "/api/v1/payments", "/api/v1/consultants", "/api/v1/audit"])
@pytest.mark.parametrize("nul", ["%00", "abc%00def"])
def test_nul_byte_in_query_string_is_a_400_not_a_500(world, authed_api_client, path, nul):
    r = authed_api_client(world["admin"]).get(f"{path}?q={nul}")
    assert r.status_code == 400, (path, r.status_code)
    assert "error" in r.json()


@pytest.mark.django_db(transaction=True)
def test_a_literal_percent_sign_in_a_search_still_works(world, authed_api_client):
    assert authed_api_client(world["admin"]).get("/api/v1/payers?q=100%25").status_code == 200


# --- email is the login: changing it needs the password --------------------------

@pytest.mark.django_db(transaction=True)
def test_changing_login_email_requires_the_current_password(world, authed_api_client):
    admin = world["admin"]
    client = authed_api_client(admin)
    original = admin.email

    no_pw = client.patch("/api/v1/auth/me", {"email": "attacker@example.com"}, format="json")
    assert no_pw.status_code == 400 and "current_password" in no_pw.json()
    wrong = client.patch("/api/v1/auth/me", {"email": "attacker@example.com", "current_password": "nope"}, format="json")
    assert wrong.status_code == 400 and "current_password" in wrong.json()
    admin.refresh_from_db()
    assert admin.email == original

    ok = client.patch(
        "/api/v1/auth/me", {"email": "new-address@example.com", "current_password": "testpass12345"}, format="json",
    )
    assert ok.status_code == 200, ok.content
    admin.refresh_from_db()
    assert admin.email == "new-address@example.com"
    entry = AuditLog.objects.get(action="EMAIL_CHANGED", entity_id=str(admin.id))
    assert entry.detail == {"old_email": original, "new_email": "new-address@example.com"}


@pytest.mark.django_db(transaction=True)
def test_profile_save_with_unchanged_email_needs_no_password(world, authed_api_client):
    admin = world["admin"]
    r = authed_api_client(admin).patch(
        "/api/v1/auth/me", {"full_name": "Renamed Admin", "email": admin.email.upper(), "phone": "08030001111"}, format="json",
    )
    assert r.status_code == 200, r.content
    assert AppUser.objects.get(pk=admin.pk).full_name == "Renamed Admin"


@pytest.mark.django_db(transaction=True)
def test_email_change_cannot_collide_case_insensitively(world, authed_api_client, make_user):
    with transaction.atomic():
        set_council_context(world["council"].id)
        victim = make_user(world["council"], username="sr2-victim")
    r = authed_api_client(world["admin"]).patch(
        "/api/v1/auth/me", {"email": victim.email.upper(), "current_password": "testpass12345"}, format="json",
    )
    assert r.status_code == 400 and "email" in r.json(), r.content


# --- logins are audited ------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_successful_and_failed_logins_are_audited(world, api_client):
    admin = world["admin"]
    bad = api_client.post("/api/v1/auth/login", {"email": admin.email, "password": "wrong-password"}, format="json")
    good = api_client.post("/api/v1/auth/login", {"email": admin.email, "password": "testpass12345"}, format="json")
    assert (bad.status_code, good.status_code) == (401, 200)
    with transaction.atomic():
        set_council_context(world["council"].id)
        rows = list(AuditLog.objects.filter(entity_type="APP_USER", entity_id=str(admin.id)).order_by("id"))
    actions = [(row.action, row.actor_id) for row in rows]
    assert ("LOGIN_FAILED", admin.id) in actions and ("LOGIN_SUCCEEDED", admin.id) in actions
    failed = next(row for row in rows if row.action == "LOGIN_FAILED")
    assert failed.detail == {"reason": "bad_password"}
    assert "wrong-password" not in str([row.detail for row in rows])


@pytest.mark.django_db(transaction=True)
def test_login_for_unknown_email_is_not_audited_and_still_401s(world, api_client):
    def audit_rows():
        with transaction.atomic():
            set_council_context(world["council"].id)
            return AuditLog.objects.count()

    before = audit_rows()
    r = api_client.post("/api/v1/auth/login", {"email": "nobody@example.com", "password": "x"}, format="json")
    assert r.status_code == 401
    assert audit_rows() == before


@pytest.mark.django_db(transaction=True)
def test_platform_tier_user_can_change_password(make_role, authed_api_client):
    """Council-less (platform) users have no audit_log row to write; the
    password change must still succeed instead of 500ing on the audit insert."""
    role = make_role(name="SUPER_ADMIN", access_level=AppRole.SUPER_ADMIN)
    user = AppUser.objects.create_user(username="sr2-platform", password="testpass12345", full_name="Platform", role=role)
    r = authed_api_client(user).post(
        "/api/v1/auth/change-password",
        {"current_password": "testpass12345", "new_password": "A-Much-Better-Passphrase-9"}, format="json",
    )
    assert r.status_code == 204, r.content


# --- platform-tier lists must load related rows inside each council's RLS context -----

@pytest.fixture
def platform_user(make_role):
    def _make(username, access_level):
        role = make_role(name=access_level, access_level=access_level)
        return AppUser.objects.create_user(username=username, password="testpass12345", full_name=username, role=role)

    return _make


@pytest.mark.django_db(transaction=True)
def test_platform_consultant_list_carries_the_registration_payer_ref(
    world, make_consultant, make_council, platform_user, authed_api_client,
):
    """Found when the same API sweep was run as the owner role and as an RLS-enforced
    role: for council-less callers the consultant list lost registration_payer_ref
    (null instead of the payer's reference) because the payer was loaded lazily
    *after* the council's RLS context had closed. Production had RLS effectively off,
    so it never showed there."""
    consultant = make_consultant(world["council"], name="Registered Firm", contract_ref="CR-REG")
    consultant.registration_payer = world["own_payer"]
    consultant.save(update_fields=["registration_payer"])
    # A council created *after* the first is iterated last, which leaves its context
    # set once the loop ends — exactly the production shape (two councils) in which
    # a lazy load of the first council's rows gets hidden by RLS.
    make_council(code="SR2LAST")
    r = authed_api_client(platform_user("sr2-super", AppRole.SUPER_ADMIN)).get("/api/v1/consultants")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["consultant_name"] == "Registered Firm")
    assert row["registration_payer_ref"] == world["own_payer"].payer_ref
    assert row["has_login"] is False


@pytest.mark.django_db(transaction=True)
def test_platform_settlement_list_carries_consultant_name_in_a_stable_order(
    world, make_consultant, make_council, platform_user, authed_api_client,
):
    import datetime

    from apps.settlements.models import CommissionSettlement

    first = make_consultant(world["council"], name="Alpha Firm", contract_ref="CR-A")
    second = make_consultant(world["council"], name="Bravo Firm", contract_ref="CR-B")
    start, end = datetime.date(2026, 1, 1), datetime.date(2026, 1, 31)
    for consultant in (second, first):  # inserted out of id order on purpose
        CommissionSettlement.objects.create(
            council=world["council"], consultant=consultant, period_start=start, period_end=end,
            gross_collections=1000, commission_rate=10, commission_amount=100, computed_by=world["admin"],
        )
    make_council(code="SR2LAST")  # created last, so iterated last; see the consultant-list test above
    client = authed_api_client(platform_user("sr2-finance", AppRole.FINANCE_ADMIN))
    results = client.get("/api/v1/settlements").json()["results"]
    assert {row["consultant_name"] for row in results} == {"Alpha Firm", "Bravo Firm"}
    # Two settlements share a period_start: the order must not depend on how the database happens to return ties.
    assert [row["id"] for row in results] == sorted(row["id"] for row in results)
    assert [row["id"] for row in client.get("/api/v1/settlements").json()["results"]] == [row["id"] for row in results]


# --- client-address diagnostic -----------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_client_ip_diagnostic_reports_only_the_callers_own_headers(world, authed_api_client, api_client):
    assert api_client.get("/api/v1/ops/client-ip").status_code == 401
    r = authed_api_client(world["admin"]).get("/api/v1/ops/client-ip", HTTP_X_FORWARDED_FOR="198.51.100.7, 203.0.113.9")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["x_forwarded_for"] == "198.51.100.7, 203.0.113.9"
    assert body["remote_addr"] == "127.0.0.1"
    assert body["num_proxies"] is None and body["resolved_ip"] is None  # unset: header chain isn't a single IP


# --- admin login form is throttled -------------------------------------------------

@pytest.mark.django_db
def test_admin_login_form_is_rate_limited(client, settings):
    settings.ADMIN_LOGIN_ATTEMPTS = 3
    cache.clear()
    codes = [client.post("/admin/login/", {"username": "x", "password": "y"}).status_code for _ in range(5)]
    assert codes[:3] != [429, 429, 429] and 429 not in codes[:3]
    assert codes[3:] == [429, 429]
