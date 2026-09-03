"""
Optional filtering/sorting params added to the existing Payer and Bill list
endpoints (additive, backward-compatible — the Payer Registry and Bills List
pages keep the same serializers, response shape and pagination).

Covers the two things most likely to go wrong quietly: a joined filter
duplicating rows in a paginated list, and a scoped role widening its own
visibility through the new `consultant_id` param.
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.billing.services import issue_bill
from apps.revenue.models import RateBand
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_consultant, make_revenue_item):
    council = make_council(code="LFLT")
    with transaction.atomic():
        set_council_context(council.id)
        ward_a = make_ward(council, code="LFA", name="Filter Ward A")
        ward_b = make_ward(council, code="LFB", name="Filter Ward B")
        admin = make_user(council, username="lflt-admin")
        consultant = make_consultant(council, name="Filter Co", contract_ref="CR-LFLT")
        manager = make_user(council, username="lflt-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
        item = make_revenue_item(council, code="LFLTITEM", name="Filter Item", rate=10000)
        yield {
            "council": council, "ward_a": ward_a, "ward_b": ward_b, "admin": admin,
            "consultant": consultant, "manager": manager, "item": item,
        }


# --- payers ---

@pytest.mark.django_db(transaction=True)
def test_payer_filter_by_ward(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Ward A Payer", phone="09010000001")
    make_payer(scoped["council"], scoped["ward_b"], scoped["admin"], name="Ward B Payer", phone="09010000002")

    r = authed_api_client(scoped["admin"]).get(f"/api/v1/payers?ward_id={scoped['ward_a'].id}")
    assert r.status_code == 200, r.content
    assert {row["full_name"] for row in r.json()["results"]} == {"Ward A Payer"}


@pytest.mark.django_db(transaction=True)
def test_payer_filter_by_consultant(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["manager"], name="Consultant Payer", phone="09020000001")
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Council Direct Payer", phone="09020000002")

    r = authed_api_client(scoped["admin"]).get(f"/api/v1/payers?consultant_id={scoped['consultant'].id}")
    assert r.status_code == 200, r.content
    assert {row["full_name"] for row in r.json()["results"]} == {"Consultant Payer"}


@pytest.mark.django_db(transaction=True)
def test_payer_filter_by_date_range(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Today Payer", phone="09030000001")
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    included = authed_api_client(scoped["admin"]).get(f"/api/v1/payers?date_from={today.isoformat()}")
    assert included.status_code == 200, included.content
    assert "Today Payer" in {row["full_name"] for row in included.json()["results"]}

    excluded = authed_api_client(scoped["admin"]).get(f"/api/v1/payers?date_from={tomorrow.isoformat()}")
    assert excluded.json()["results"] == []


@pytest.mark.django_db(transaction=True)
def test_payer_ordering_both_directions(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Aaa First Payer", phone="09040000001")
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Zzz Last Payer", phone="09040000002")
    client = authed_api_client(scoped["admin"])

    asc = client.get("/api/v1/payers?ordering=full_name")
    assert asc.status_code == 200, asc.content
    assert asc.json()["results"][0]["full_name"] == "Aaa First Payer"

    desc = client.get("/api/v1/payers?ordering=-full_name")
    assert desc.json()["results"][0]["full_name"] == "Zzz Last Payer"


@pytest.mark.django_db(transaction=True)
def test_payer_list_unchanged_when_no_filters_given(scoped, authed_api_client, make_payer):
    """Backward compatibility: absent params must leave the existing queryset,
    default ordering (full_name) and paginated response shape exactly as before."""
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Aaa Baseline", phone="09050000001")
    make_payer(scoped["council"], scoped["ward_b"], scoped["admin"], name="Zzz Baseline", phone="09050000002")

    r = authed_api_client(scoped["admin"]).get("/api/v1/payers")
    assert r.status_code == 200, r.content
    body = r.json()
    assert set(body) >= {"count", "results"}
    assert [row["full_name"] for row in body["results"]] == ["Aaa Baseline", "Zzz Baseline"]
    assert body["results"][0]["payer_ref"]  # serializer shape unchanged


@pytest.mark.django_db(transaction=True)
def test_payer_invalid_filter_values_are_400(scoped, authed_api_client):
    client = authed_api_client(scoped["admin"])
    assert client.get("/api/v1/payers?ward_id=abc").status_code == 400
    assert client.get("/api/v1/payers?date_from=not-a-date").status_code == 400


# --- bills ---

@pytest.mark.django_db(transaction=True)
def test_bill_filter_by_ward_and_consultant(scoped, authed_api_client, make_payer):
    council, admin, item = scoped["council"], scoped["admin"], scoped["item"]
    ward_a_payer = make_payer(council, scoped["ward_a"], scoped["manager"], name="Bill Ward A", phone="09060000001")
    ward_b_payer = make_payer(council, scoped["ward_b"], admin, name="Bill Ward B", phone="09060000002")
    bill_a = issue_bill(council_id=council.id, payer=ward_a_payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=scoped["manager"])
    issue_bill(council_id=council.id, payer=ward_b_payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    client = authed_api_client(admin)

    by_ward = client.get(f"/api/v1/bills?ward_id={scoped['ward_a'].id}")
    assert by_ward.status_code == 200, by_ward.content
    assert [row["id"] for row in by_ward.json()["results"]] == [bill_a.id]

    by_consultant = client.get(f"/api/v1/bills?consultant_id={scoped['consultant'].id}")
    assert [row["id"] for row in by_consultant.json()["results"]] == [bill_a.id]


@pytest.mark.django_db(transaction=True)
def test_bill_filter_by_revenue_item_does_not_duplicate_multi_band_bill(scoped, authed_api_client, make_payer):
    """A bill can legitimately carry two lines for the same revenue item under
    different rate bands — the lines join would return that bill twice without
    .distinct(), which in a paginated list means a visibly duplicated row."""
    council, admin = scoped["council"], scoped["admin"]
    banded_item = scoped["item"]
    band_a = RateBand.objects.create(
        council_revenue_item=banded_item, label="Band A", rate_mode=RateBand.FLAT,
        flat_amount=4000, effective_from=datetime.date.today(),
    )
    band_b = RateBand.objects.create(
        council_revenue_item=banded_item, label="Band B", rate_mode=RateBand.FLAT,
        flat_amount=6000, effective_from=datetime.date.today(),
    )
    payer = make_payer(council, scoped["ward_a"], admin, name="Multi Band Payer", phone="09070000001")
    bill = issue_bill(
        council_id=council.id, payer=payer,
        lines=[
            {"council_revenue_item": banded_item, "rate_band": band_a},
            {"council_revenue_item": banded_item, "rate_band": band_b},
        ],
        actor=admin,
    )

    r = authed_api_client(admin).get(f"/api/v1/bills?revenue_item_id={banded_item.id}")
    assert r.status_code == 200, r.content
    assert [row["id"] for row in r.json()["results"]] == [bill.id]
    assert r.json()["count"] == 1


@pytest.mark.django_db(transaction=True)
def test_bill_filter_by_value_range_and_date_range(scoped, authed_api_client, make_payer, make_revenue_item):
    council, admin = scoped["council"], scoped["admin"]
    cheap_item = make_revenue_item(council, code="LFLTCHEAP", name="Cheap Item", rate=2000)
    payer = make_payer(council, scoped["ward_a"], admin, name="Value Payer", phone="09080000001")
    cheap = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": cheap_item, "quantity": 1}], actor=admin)
    pricey = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=admin)
    client = authed_api_client(admin)

    min_only = client.get("/api/v1/bills?value_min=5000")
    assert min_only.status_code == 200, min_only.content
    assert [row["id"] for row in min_only.json()["results"]] == [pricey.id]

    max_only = client.get("/api/v1/bills?value_max=5000")
    assert [row["id"] for row in max_only.json()["results"]] == [cheap.id]

    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert client.get(f"/api/v1/bills?date_from={tomorrow}").json()["results"] == []


@pytest.mark.django_db(transaction=True)
def test_bill_ordering_by_total_amount(scoped, authed_api_client, make_payer, make_revenue_item):
    council, admin = scoped["council"], scoped["admin"]
    cheap_item = make_revenue_item(council, code="LFLTORDER", name="Order Item", rate=1000)
    payer = make_payer(council, scoped["ward_a"], admin, name="Order Payer", phone="09090000001")
    cheap = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": cheap_item, "quantity": 1}], actor=admin)
    pricey = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=admin)
    client = authed_api_client(admin)

    asc = client.get("/api/v1/bills?ordering=total_amount")
    assert asc.status_code == 200, asc.content
    assert [row["id"] for row in asc.json()["results"]] == [cheap.id, pricey.id]

    desc = client.get("/api/v1/bills?ordering=-total_amount")
    assert [row["id"] for row in desc.json()["results"]] == [pricey.id, cheap.id]


@pytest.mark.django_db(transaction=True)
def test_bill_existing_status_and_payer_filters_still_work(scoped, authed_api_client, make_payer):
    """Backward compatibility for the params that already existed."""
    council, admin, item = scoped["council"], scoped["admin"], scoped["item"]
    payer_one = make_payer(council, scoped["ward_a"], admin, name="Existing One", phone="09100000001")
    payer_two = make_payer(council, scoped["ward_a"], admin, name="Existing Two", phone="09100000002")
    bill_one = issue_bill(council_id=council.id, payer=payer_one, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    issue_bill(council_id=council.id, payer=payer_two, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
    client = authed_api_client(admin)

    by_payer = client.get(f"/api/v1/bills?payer={payer_one.id}")
    assert [row["id"] for row in by_payer.json()["results"]] == [bill_one.id]

    by_status = client.get("/api/v1/bills?status=ISSUED")
    assert {row["status"] for row in by_status.json()["results"]} == {"ISSUED"}

    by_q = client.get("/api/v1/bills?q=Existing One")
    assert [row["id"] for row in by_q.json()["results"]] == [bill_one.id]


@pytest.mark.django_db(transaction=True)
def test_bill_malformed_payer_param_is_400_not_500(scoped, authed_api_client):
    """Pre-existing exposure: the raw param went straight into .filter(payer_id=...),
    and the ORM's bare ValueError isn't a DRF APIException, so it 500'd."""
    r = authed_api_client(scoped["admin"]).get("/api/v1/bills?payer=abc")
    assert r.status_code == 400, r.content


# --- scoping: the new consultant_id param must never widen a scoped role's view ---

@pytest.mark.django_db(transaction=True)
def test_consultant_cannot_widen_scope_via_consultant_id_param(scoped, authed_api_client, make_consultant, make_user, make_payer):
    council, admin, item = scoped["council"], scoped["admin"], scoped["item"]
    other_consultant = make_consultant(council, name="Other Filter Co", contract_ref="CR-LFLT-OTHER")
    other_manager = make_user(council, username="lflt-other-mgr", access_level=AppRole.CONSULTANT, consultant=other_consultant)
    other_payer = make_payer(council, scoped["ward_a"], other_manager, name="Other Firm Payer", phone="09110000001")
    issue_bill(council_id=council.id, payer=other_payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=other_manager)

    # scoped["manager"] belongs to a different consultant and has no payers of its own.
    client = authed_api_client(scoped["manager"])
    payers = client.get(f"/api/v1/payers?consultant_id={other_consultant.id}")
    assert payers.status_code == 200, payers.content
    assert payers.json()["results"] == []

    bills = client.get(f"/api/v1/bills?consultant_id={other_consultant.id}")
    assert bills.json()["results"] == []

    # ...while the council admin, who is not portfolio-scoped, does see them.
    admin_payers = authed_api_client(admin).get(f"/api/v1/payers?consultant_id={other_consultant.id}")
    assert {row["full_name"] for row in admin_payers.json()["results"]} == {"Other Firm Payer"}
