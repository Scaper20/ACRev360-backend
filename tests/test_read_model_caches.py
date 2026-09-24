"""
The memoised read models (dashboard totals, the revenue-item catalogue, the
bill-reference -> council map): they must serve from cache when nothing changed,
and never serve stale data once something did — a payment just recorded has to
show on the next dashboard load, an edited rate on the next catalogue load.
"""
import pytest
from django.core.cache import cache
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.revenue.services import change_rate
from apps.tenancy.context import BILL_REF_PREFIX_CACHE_KEY, resolve_council_from_bill_ref, set_council_context
from apps.tenancy.models import CouncilConfig


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="CCH")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="cch-admin")
        payer = make_payer(council, ward, admin)
        item = make_revenue_item(council, code="CCHITEM", rate=10000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item": item}


# --- dashboard ---------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_dashboard_summary_is_served_from_cache_until_a_write(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    first = client.get("/api/v1/dashboard/summary")
    assert first.status_code == 200, first.content

    # Second identical request: no database work at all beyond the (RLS) session
    # setup — the totals come out of the cache.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as ctx:
        second = client.get("/api/v1/dashboard/summary")
    assert second.json() == first.json()
    aggregate_queries = [q for q in ctx.captured_queries if "FROM \"bill\"" in q["sql"] or "FROM \"payment\"" in q["sql"]]
    assert aggregate_queries == []


@pytest.mark.django_db(transaction=True)
def test_dashboard_reflects_a_new_bill_and_payment_immediately(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    assert client.get("/api/v1/dashboard/summary").json()["bills"] == 0  # primes the cache

    bill = issue_bill(
        council_id=scoped["council"].id, payer=scoped["payer"],
        lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"],
    )
    after_bill = client.get("/api/v1/dashboard/summary").json()
    assert after_bill["bills"] == 1 and float(after_bill["billed"]) == 10000 and float(after_bill["collected"]) == 0

    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.CASH)
    post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=4000, posted_by=scoped["admin"])
    after_payment = client.get("/api/v1/dashboard/summary").json()
    assert float(after_payment["collected"]) == 4000 and float(after_payment["outstanding"]) == 6000


@pytest.mark.django_db(transaction=True)
def test_dashboard_cache_is_scoped_per_role_and_council(scoped, authed_api_client, make_user, make_council):
    admin_client = authed_api_client(scoped["admin"])
    admin_client.get("/api/v1/dashboard/summary")  # primes the admin's cache entry

    other = make_council(code="CCH2")
    with transaction.atomic():
        set_council_context(other.id)
        outsider = make_user(other, username="cch-outsider")
    from rest_framework.test import APIClient

    from apps.accounts.tokens import AppTokenObtainPairSerializer

    other_client = APIClient()
    other_client.credentials(HTTP_AUTHORIZATION=f"Bearer {AppTokenObtainPairSerializer.get_token(outsider).access_token}")
    # The other council's dashboard is its own (empty), never the first council's cached one.
    assert other_client.get("/api/v1/dashboard/summary").json()["payers"] == 0
    assert admin_client.get("/api/v1/dashboard/summary").json()["payers"] == 1


# --- revenue-item catalogue ----------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_revenue_items_sends_an_etag_and_answers_304_when_unchanged(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    first = client.get("/api/v1/revenue-items")
    assert first.status_code == 200, first.content
    etag = first["ETag"]
    assert etag and first["Cache-Control"] == "private, no-cache" and "Authorization" in first["Vary"]

    revalidated = client.get("/api/v1/revenue-items", HTTP_IF_NONE_MATCH=etag)
    assert revalidated.status_code == 304
    assert revalidated.content == b""

    again = client.get("/api/v1/revenue-items")
    assert again.status_code == 200 and again.json() == first.json() and again["ETag"] == etag


@pytest.mark.django_db(transaction=True)
def test_revenue_items_reflects_a_rate_change_on_the_very_next_request(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    before = client.get("/api/v1/revenue-items")
    old_etag = before["ETag"]
    assert float(before.json()["results"][0]["current_rate"]) == 10000

    change_rate(council_revenue_item=scoped["item"], new_amount=12500, actor=scoped["admin"])

    stale_check = client.get("/api/v1/revenue-items", HTTP_IF_NONE_MATCH=old_etag)
    assert stale_check.status_code == 200, "an edited rate must invalidate the old ETag"
    assert stale_check["ETag"] != old_etag
    assert float(stale_check.json()["results"][0]["current_rate"]) == 12500


@pytest.mark.django_db(transaction=True)
def test_revenue_items_cache_does_not_leak_between_scopes(scoped, authed_api_client, make_user, make_consultant):
    consultant = make_consultant(scoped["council"])
    with transaction.atomic():
        set_council_context(scoped["council"].id)
        consultant_user = make_user(scoped["council"], username="cch-consultant", access_level=AppRole.CONSULTANT, consultant=consultant)
    admin_view = authed_api_client(scoped["admin"]).get("/api/v1/revenue-items")
    assert len(admin_view.json()["results"]) == 1  # primes the admin's cached catalogue

    # A consultant with no portfolio entries sees none of it, cache or not.
    consultant_view = authed_api_client(consultant_user).get("/api/v1/revenue-items")
    assert consultant_view.status_code == 200
    assert consultant_view.json()["results"] == []
    assert consultant_view["ETag"] != admin_view["ETag"]


# --- bill_ref prefix -> council ------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_bill_ref_prefix_map_is_cached_and_dropped_when_config_changes(scoped):
    council = scoped["council"]
    assert resolve_council_from_bill_ref("CCH/2026/000001").id == council.id
    assert cache.get(BILL_REF_PREFIX_CACHE_KEY) == {"CCH": council.id}
    assert resolve_council_from_bill_ref("NOPE/2026/000001") is None
    assert resolve_council_from_bill_ref(None) is None
    assert resolve_council_from_bill_ref(12345) is None

    config = CouncilConfig.objects.get(council=council)
    config.bill_ref_prefix = "CCX"
    config.save(update_fields=["bill_ref_prefix"])
    assert cache.get(BILL_REF_PREFIX_CACHE_KEY) is None
    assert resolve_council_from_bill_ref("CCX/2026/000001").id == council.id
    assert resolve_council_from_bill_ref("CCH/2026/000001") is None
