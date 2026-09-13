import decimal
import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.audit.models import AuditLog
from apps.billing.models import Assessment
from apps.revenue.models import CouncilRevenueItem, RevenueCategory, RevenueItemTemplate
from apps.tenancy.context import set_council_context
from apps.tenancy.services import activate_template_item


@pytest.fixture
def setup_data(request, make_council, make_ward, make_user, make_payer):
    node_id = "".join(c for c in request.node.name if c.isalnum())[:8]
    code = f"C{node_id}"
    council = make_council(code=code)
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username=f"admin-{node_id}", access_level=AppRole.COUNCIL_ADMIN)
        consultant_user = make_user(council, username=f"consultant-{node_id}", access_level=AppRole.CONSULTANT)
        category, _ = RevenueCategory.objects.get_or_create(name="Local Fees", sort_order=1)
        payer = make_payer(council, ward, admin, name="Test Payer")
        yield {
            "council": council,
            "ward": ward,
            "admin": admin,
            "consultant_user": consultant_user,
            "category": category,
            "payer": payer,
        }




@pytest.mark.django_db(transaction=True)
def test_council_admin_can_create_revenue_item(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-001",
        "item_name": "Market Stall Fee",
        "category_id": category.id,
        "unit_of_charge": "per stall / per day",
        "rate_amount": "1500.00",
        "bye_law_reference": "Part III Section 4",
        "bye_law_description": "Authorises daily fee on temporary market stalls.",
    }

    r = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    assert r.status_code == 201, r.content
    data = r.json()

    assert data["harmonised_code"] == "LOCAL-001"
    assert data["item_name"] == "Market Stall Fee"
    assert data["category"] == category.id
    assert data["unit_of_charge"] == "per stall / per day"
    assert data["current_rate"] == "1500.00"
    assert data["template"] is None
    assert data["is_active"] is True

    item = CouncilRevenueItem.objects.get(id=data["id"])
    assert item.bye_law_reference == "Part III Section 4"
    assert item.bye_law_description == "Authorises daily fee on temporary market stalls."

    # Audit log check
    assert AuditLog.objects.filter(
        council_id=setup_data["council"].id,
        action="REVENUE_ITEM_ACTIVATED",
        entity_id=item.id,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_create_revenue_item_duplicate_code_rejected(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-DUP",
        "item_name": "First Item",
        "category_id": category.id,
        "unit_of_charge": "annual",
        "rate_amount": "5000.00",
    }
    r1 = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    assert r1.status_code == 201

    r2 = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    assert r2.status_code == 400
    assert "harmonised_code" in r2.json()


@pytest.mark.django_db(transaction=True)
def test_harmonised_code_reusable_after_retiring_via_api(setup_data, authed_api_client):
    """uniq_item_code_per_council is a partial constraint (is_active=True
    only) specifically so retiring isn't a one-way lock on the code — see
    apps/revenue/models.py's own comment on the constraint."""
    admin = setup_data["admin"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-REUSE",
        "item_name": "Original Item",
        "category_id": category.id,
        "unit_of_charge": "annual",
        "rate_amount": "5000.00",
    }
    first_id = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json").json()["id"]

    retire_res = authed_api_client(admin).delete(f"/api/v1/revenue-items/{first_id}")
    assert retire_res.status_code == 204

    payload["item_name"] = "Reissued Item"
    r2 = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    assert r2.status_code == 201, r2.content
    second_id = r2.json()["id"]
    assert second_id != first_id

    # Both rows survive — the retired one keeps its history, the new one is
    # a separate row sharing only the code string, not the id.
    first = CouncilRevenueItem.objects.get(id=first_id)
    second = CouncilRevenueItem.objects.get(id=second_id)
    assert first.is_active is False
    assert first.item_name == "Original Item"
    assert second.is_active is True
    assert second.item_name == "Reissued Item"
    assert first.harmonised_code == second.harmonised_code == "LOCAL-REUSE"

    # And the newly-active one is what a third create attempt now collides with.
    r3 = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    assert r3.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_two_retired_items_can_share_a_harmonised_code(setup_data):
    """The partial index only constrains is_active=True rows — retiring,
    reissuing, and retiring again must not hit uniq_item_code_per_council on
    the second retirement, since both retired rows are simultaneously
    is_active=False at that point."""
    from apps.revenue.services import create_revenue_item, retire_revenue_item

    council = setup_data["council"]
    admin = setup_data["admin"]
    category = setup_data["category"]

    first = create_revenue_item(
        council=council, harmonised_code="LOCAL-TWICE", item_name="First", category=category,
        unit_of_charge="annual", rate_amount=decimal.Decimal("1000.00"), actor=admin,
    )
    retire_revenue_item(council_revenue_item=first, actor=admin)

    second = create_revenue_item(
        council=council, harmonised_code="LOCAL-TWICE", item_name="Second", category=category,
        unit_of_charge="annual", rate_amount=decimal.Decimal("2000.00"), actor=admin,
    )
    retire_revenue_item(council_revenue_item=second, actor=admin)

    assert CouncilRevenueItem.objects.filter(
        council=council, harmonised_code="LOCAL-TWICE", is_active=False
    ).count() == 2


@pytest.mark.django_db(transaction=True)
def test_create_revenue_item_non_admin_forbidden(setup_data, authed_api_client):
    consultant_user = setup_data["consultant_user"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-FORBID",
        "item_name": "Forbidden Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "2000.00",
    }
    r = authed_api_client(consultant_user).post("/api/v1/revenue-items", data=payload, format="json")
    assert r.status_code == 403


@pytest.mark.django_db(transaction=True)
def test_council_admin_can_retire_revenue_item_via_delete(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-RETIRE1",
        "item_name": "Old Charge",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "1000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]

    del_res = authed_api_client(admin).delete(f"/api/v1/revenue-items/{item_id}")
    assert del_res.status_code == 204

    item = CouncilRevenueItem.objects.get(id=item_id)
    assert item.is_active is False

    assert AuditLog.objects.filter(
        council_id=setup_data["council"].id,
        action="REVENUE_ITEM_RETIRED",
        entity_id=item_id,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_council_admin_can_retire_revenue_item_via_post_retire(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-RETIRE2",
        "item_name": "Old Charge 2",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "1000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]

    retire_res = authed_api_client(admin).post(f"/api/v1/revenue-items/{item_id}/retire")
    assert retire_res.status_code == 200
    assert retire_res.json()["is_active"] is False

    item = CouncilRevenueItem.objects.get(id=item_id)
    assert item.is_active is False


@pytest.mark.django_db(transaction=True)
def test_retire_revenue_item_non_admin_forbidden(setup_data, authed_api_client):
    admin = setup_data["admin"]
    consultant_user = setup_data["consultant_user"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-RETIRE3",
        "item_name": "Protected Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "1000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]

    r = authed_api_client(consultant_user).delete(f"/api/v1/revenue-items/{item_id}")
    assert r.status_code == 403

    r2 = authed_api_client(consultant_user).post(f"/api/v1/revenue-items/{item_id}/retire")
    assert r2.status_code == 403


@pytest.mark.django_db(transaction=True)
def test_retire_revenue_item_with_draft_assessment_rejected(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]
    payer = setup_data["payer"]

    payload = {
        "harmonised_code": "LOCAL-DRAFT-GUARD",
        "item_name": "Guarded Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "3000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]
    item = CouncilRevenueItem.objects.get(id=item_id)

    # Create a draft assessment for this revenue item
    Assessment.objects.create(
        council=setup_data["council"],
        payer=payer,
        council_revenue_item=item,
        quantity=1,
        amount=decimal.Decimal("3000.00"),
        status="DRAFT",
        created_by=admin,
    )


    r = authed_api_client(admin).delete(f"/api/v1/revenue-items/{item_id}")
    assert r.status_code == 409
    assert "active draft assessments" in r.json()["error"]


@pytest.mark.django_db(transaction=True)
def test_retire_revenue_item_with_outstanding_bill_rejected(setup_data, authed_api_client):
    from apps.billing.services import issue_bill

    admin = setup_data["admin"]
    category = setup_data["category"]
    payer = setup_data["payer"]

    payload = {
        "harmonised_code": "LOCAL-BILL-GUARD",
        "item_name": "Billed Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "2000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]
    item = CouncilRevenueItem.objects.get(id=item_id)

    bill = issue_bill(
        council_id=setup_data["council"].id, payer=payer, actor=admin,
        lines=[{"council_revenue_item": item, "quantity": 1}],
    )
    assert bill.status == "ISSUED"

    r = authed_api_client(admin).delete(f"/api/v1/revenue-items/{item_id}")
    assert r.status_code == 409
    assert "outstanding" in r.json()["error"]

    # Refused, not silently retired — the item is still active and still billable.
    item.refresh_from_db()
    assert item.is_active is True


@pytest.mark.django_db(transaction=True)
def test_retired_revenue_item_cannot_be_billed(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]
    payer = setup_data["payer"]

    payload = {
        "harmonised_code": "LOCAL-RETIRED-BILL",
        "item_name": "Retired Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "1000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]

    retire_res = authed_api_client(admin).delete(f"/api/v1/revenue-items/{item_id}")
    assert retire_res.status_code == 204

    # A retired item can't be resolved for a brand-new bill line — this is the
    # actual enforcement point; is_active hiding it from the Revenue Items
    # screen was never enough on its own.
    bill_res = authed_api_client(admin).post(
        "/api/v1/bills",
        data={"payer_id": payer.id, "lines": [{"revenue_item_id": item_id, "quantity": 1}]},
        format="json",
    )
    assert bill_res.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_retired_revenue_item_cannot_be_added_as_a_line_to_an_existing_bill(setup_data, authed_api_client):
    from apps.billing.services import issue_bill

    admin = setup_data["admin"]
    category = setup_data["category"]
    payer = setup_data["payer"]

    other_item_payload = {
        "harmonised_code": "LOCAL-OTHER",
        "item_name": "Other Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "500.00",
    }
    other_item_id = authed_api_client(admin).post(
        "/api/v1/revenue-items", data=other_item_payload, format="json"
    ).json()["id"]
    other_item = CouncilRevenueItem.objects.get(id=other_item_id)

    retired_payload = {
        "harmonised_code": "LOCAL-RETIRED-LINE",
        "item_name": "Will Be Retired",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "750.00",
    }
    retired_item_id = authed_api_client(admin).post(
        "/api/v1/revenue-items", data=retired_payload, format="json"
    ).json()["id"]
    authed_api_client(admin).delete(f"/api/v1/revenue-items/{retired_item_id}")

    bill = issue_bill(
        council_id=setup_data["council"].id, payer=payer, actor=admin,
        lines=[{"council_revenue_item": other_item, "quantity": 1}],
    )

    r = authed_api_client(admin).post(
        f"/api/v1/bills/{bill.id}/lines",
        data={"revenue_item_id": retired_item_id, "quantity": 1},
        format="json",
    )
    assert r.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_retired_revenue_item_excluded_from_list(setup_data, authed_api_client):
    admin = setup_data["admin"]
    category = setup_data["category"]

    payload = {
        "harmonised_code": "LOCAL-HIDE",
        "item_name": "Hidden Item",
        "category_id": category.id,
        "unit_of_charge": "flat",
        "rate_amount": "1000.00",
    }
    create_res = authed_api_client(admin).post("/api/v1/revenue-items", data=payload, format="json")
    item_id = create_res.json()["id"]

    list_before = authed_api_client(admin).get("/api/v1/revenue-items").json()["results"]
    assert any(i["id"] == item_id for i in list_before)

    authed_api_client(admin).delete(f"/api/v1/revenue-items/{item_id}")

    list_after = authed_api_client(admin).get("/api/v1/revenue-items").json()["results"]
    assert not any(i["id"] == item_id for i in list_after)


@pytest.mark.django_db(transaction=True)
def test_activate_template_item_works_via_helper(setup_data):
    council = setup_data["council"]
    admin = setup_data["admin"]
    category = setup_data["category"]

    template = RevenueItemTemplate.objects.create(
        category=category,
        harmonised_code="TPL-001",
        item_name="Template Liquor License",
        unit_of_charge="annual",
    )

    item = activate_template_item(
        council=council,
        template=template,
        rate_amount=decimal.Decimal("15000.00"),
        actor=admin,
    )

    assert item.template == template
    assert item.harmonised_code == "TPL-001"
    assert item.item_name == "Template Liquor License"
    assert item.current_rate.rate_amount == decimal.Decimal("15000.00")
    assert AuditLog.objects.filter(council_id=council.id, action="REVENUE_ITEM_ACTIVATED", entity_id=item.id).exists()
