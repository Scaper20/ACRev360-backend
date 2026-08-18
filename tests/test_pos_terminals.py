"""
POS terminal fields added for the frontend handoff (BACKEND_HANDOFF.md item 5):
bank_terminal_id, and a per-terminal "collected" total sourced from a queryset
annotation rather than a per-row SerializerMethodField, specifically to avoid
an N+1 on the terminal list endpoint.
"""
import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.payments.models import Payment, PaymentChannel
from apps.payments.services import post_payment
from apps.billing.services import issue_bill
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item, make_field_agent, make_terminal):
    council = make_council(code="TRM")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="trm-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="TRMITEM", rate=10000)
        agent_user = make_user(council, username="trm-agent")
        agent = make_field_agent(council, agent_user, ward=ward)
        terminal = make_terminal(council, agent, ward, terminal_id="T-1", bank_terminal_id="BANK-1")
        yield {
            "council": council, "ward": ward, "admin": admin, "payer": payer, "item": item,
            "agent": agent, "terminal": terminal,
        }


@pytest.mark.django_db(transaction=True)
def test_post_payment_accepts_optional_terminal(scoped):
    council, payer, admin, item, terminal = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item"], scoped["terminal"],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    with_terminal = post_payment(council_id=council.id, bill=bill, channel=channel, amount=5000, posted_by=admin, terminal=terminal)
    assert with_terminal.terminal_id == terminal.id

    bill2 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    without_terminal = post_payment(council_id=council.id, bill=bill2, channel=channel, amount=5000, posted_by=admin)
    assert without_terminal.terminal_id is None


@pytest.mark.django_db(transaction=True)
def test_post_payment_via_api_resolves_terminal_id(scoped, authed_api_client):
    council, payer, admin, item, terminal = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item"], scoped["terminal"],
    )
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    r = authed_api_client(admin).post("/api/v1/payments", {
        "bill_id": bill.id, "amount": "10000", "channel_code": PaymentChannel.POS, "terminal_id": terminal.id,
    })
    assert r.status_code == 201, r.content
    payment = Payment.objects.get(pk=r.json()["id"])
    assert payment.terminal_id == terminal.id


@pytest.mark.django_db(transaction=True)
def test_post_payment_via_api_without_terminal_id(scoped, authed_api_client):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    r = authed_api_client(admin).post("/api/v1/payments", {
        "bill_id": bill.id, "amount": "10000", "channel_code": PaymentChannel.POS,
    })
    assert r.status_code == 201, r.content
    payment = Payment.objects.get(pk=r.json()["id"])
    assert payment.terminal_id is None


@pytest.mark.django_db(transaction=True)
def test_terminal_id_cross_council_404s(scoped, authed_api_client, make_council, make_ward, make_user, make_field_agent, make_terminal):
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    with transaction.atomic():
        other_council = make_council(code="OTR")
        set_council_context(other_council.id)
        other_ward = make_ward(other_council)
        other_agent_user = make_user(other_council, username="otr-agent")
        other_agent = make_field_agent(other_council, other_agent_user, ward=other_ward)
        other_terminal = make_terminal(other_council, other_agent, other_ward, terminal_id="T-OTHER")

    r = authed_api_client(admin).post("/api/v1/payments", {
        "bill_id": bill.id, "amount": "10000", "channel_code": PaymentChannel.POS, "terminal_id": other_terminal.id,
    })
    assert r.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_terminal_id_zero_404s_rather_than_silently_dropping(scoped, authed_api_client):
    """terminal_id=0 must be treated as an explicit (invalid) id, not as
    'not provided' — a falsy-but-not-None check would silently post the
    payment with no terminal instead of failing loudly."""
    council, payer, admin, item = scoped["council"], scoped["payer"], scoped["admin"], scoped["item"]
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)

    r = authed_api_client(admin).post("/api/v1/payments", {
        "bill_id": bill.id, "amount": "10000", "channel_code": PaymentChannel.POS, "terminal_id": 0,
    })
    assert r.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_terminal_collected_total_annotation(scoped, authed_api_client):
    council, payer, admin, item, terminal = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item"], scoped["terminal"],
    )
    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)

    bill1 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    bill2 = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    post_payment(council_id=council.id, bill=bill1, channel=channel, amount=4000, posted_by=admin, terminal=terminal)
    post_payment(council_id=council.id, bill=bill2, channel=channel, amount=6000, posted_by=admin, terminal=terminal)

    failed_bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    failed_payment = post_payment(council_id=council.id, bill=failed_bill, channel=channel, amount=9999, posted_by=admin, terminal=terminal)
    failed_payment.txn_status = Payment.FAILED
    failed_payment.save(update_fields=["txn_status"])

    r = authed_api_client(admin).get("/api/v1/terminals")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json()["results"] if row["id"] == terminal.id)
    assert row["collected"] == "10000.00"
    assert row["bank_terminal_id"] == "BANK-1"


@pytest.mark.django_db(transaction=True)
def test_terminal_collected_annotation_query_count(scoped, authed_api_client, make_field_agent, make_terminal, make_user):
    council, ward = scoped["council"], scoped["ward"]
    for i in range(3):
        u = make_user(council, username=f"trm-agent-extra-{i}")
        agent = make_field_agent(council, u, ward=ward, agent_code=f"AGT-EXTRA-{i}")
        make_terminal(council, agent, ward, terminal_id=f"T-EXTRA-{i}")

    client = authed_api_client(scoped["admin"])
    with CaptureQueriesContext(connection) as ctx:
        r = client.get("/api/v1/terminals")
    assert r.status_code == 200, r.content
    assert len(r.json()["results"]) == 4  # scoped["terminal"] + 3 extra
    # One query for the annotated terminal list (plus the auth/permission
    # lookups already paid regardless of terminal count) — not one per row.
    assert len(ctx.captured_queries) < 10
