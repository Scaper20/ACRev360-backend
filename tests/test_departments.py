"""
Department (item 5 of the frontend's backend requirements doc) — council-scoped
grouping for CouncilRevenueItem, with its own list/create/edit endpoints and a
dedicated action for assigning an item to one.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Department


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_revenue_item):
    council = make_council(code="DEP")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="dep-admin")
        item = make_revenue_item(council, code="DEPITEM", rate=5000)
        yield {"council": council, "ward": ward, "admin": admin, "item": item}


@pytest.mark.django_db(transaction=True)
def test_admin_can_create_department(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/departments",
        {"department_name": "Works and Housing", "department_code": "WH", "head_name": "Eng. Bala", "head_phone": "08012345678"},
        format="json",
    )
    assert r.status_code == 201, r.content
    assert Department.objects.filter(council=scoped["council"], department_name="Works and Housing").exists()


@pytest.mark.django_db(transaction=True)
def test_non_admin_cannot_create_department(scoped, authed_api_client, make_user):
    non_admin = make_user(scoped["council"], username="dep-nonadmin", access_level=AppRole.CONSULTANT)
    r = authed_api_client(non_admin).post("/api/v1/departments", {"department_name": "Blocked Dept"}, format="json")
    assert r.status_code == 403, r.content
    assert not Department.objects.filter(department_name="Blocked Dept").exists()


@pytest.mark.django_db(transaction=True)
def test_department_list_is_council_scoped(scoped, authed_api_client, make_council, make_user):
    Department.objects.create(council=scoped["council"], department_name="Local Dept")
    other_council = make_council(code="DEP2")
    with transaction.atomic():
        set_council_context(other_council.id)
        Department.objects.create(council=other_council, department_name="Other Council Dept")

    r = authed_api_client(scoped["admin"]).get("/api/v1/departments")
    assert r.status_code == 200, r.content
    names = {row["department_name"] for row in r.json()["results"]}
    assert names == {"Local Dept"}


@pytest.mark.django_db(transaction=True)
def test_admin_can_edit_department(scoped, authed_api_client):
    department = Department.objects.create(council=scoped["council"], department_name="Old Name")
    r = authed_api_client(scoped["admin"]).patch(
        f"/api/v1/departments/{department.id}", {"department_name": "New Name", "head_name": "New Head"}, format="json",
    )
    assert r.status_code == 200, r.content
    department.refresh_from_db()
    assert department.department_name == "New Name"
    assert department.head_name == "New Head"


@pytest.mark.django_db(transaction=True)
def test_non_admin_cannot_edit_department(scoped, authed_api_client, make_user):
    department = Department.objects.create(council=scoped["council"], department_name="Guarded Dept")
    non_admin = make_user(scoped["council"], username="dep-edit-nonadmin", access_level=AppRole.CONSULTANT)
    r = authed_api_client(non_admin).patch(f"/api/v1/departments/{department.id}", {"department_name": "Hacked"}, format="json")
    assert r.status_code == 403, r.content
    department.refresh_from_db()
    assert department.department_name == "Guarded Dept"


@pytest.mark.django_db(transaction=True)
def test_revenue_officer_can_read_but_not_write_departments(scoped, authed_api_client, make_user, make_consultant):
    Department.objects.create(council=scoped["council"], department_name="Read Only Dept")
    consultant = make_consultant(scoped["council"], name="Dept Officer Co", contract_ref="CR-DEPTOFF")
    officer = make_user(scoped["council"], username="dep-officer", access_level=AppRole.REVENUE_OFFICER, consultant=consultant)

    r = authed_api_client(officer).get("/api/v1/departments")
    assert r.status_code == 200, r.content

    r_post = authed_api_client(officer).post("/api/v1/departments", {"department_name": "Blocked"}, format="json")
    assert r_post.status_code == 403, r_post.content


# --- assigning a revenue item to a department ---

@pytest.mark.django_db(transaction=True)
def test_admin_can_assign_item_to_department(scoped, authed_api_client):
    department = Department.objects.create(council=scoped["council"], department_name="Assignable Dept")
    item = scoped["item"]

    r = authed_api_client(scoped["admin"]).post(
        f"/api/v1/revenue-items/{item.id}/department", {"department_id": department.id}, format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["department"] == department.id
    assert r.json()["department_name"] == "Assignable Dept"

    item.refresh_from_db()
    assert item.department_id == department.id


@pytest.mark.django_db(transaction=True)
def test_admin_can_clear_item_department(scoped, authed_api_client):
    department = Department.objects.create(council=scoped["council"], department_name="Clearable Dept")
    item = scoped["item"]
    item.department = department
    item.save(update_fields=["department"])

    r = authed_api_client(scoped["admin"]).post(
        f"/api/v1/revenue-items/{item.id}/department", {"department_id": None}, format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["department"] is None

    item.refresh_from_db()
    assert item.department_id is None


@pytest.mark.django_db(transaction=True)
def test_non_admin_cannot_assign_item_department(scoped, authed_api_client, make_user):
    department = Department.objects.create(council=scoped["council"], department_name="Guarded Assign Dept")
    non_admin = make_user(scoped["council"], username="dep-assign-nonadmin", access_level=AppRole.CONSULTANT)

    r = authed_api_client(non_admin).post(
        f"/api/v1/revenue-items/{scoped['item'].id}/department", {"department_id": department.id}, format="json",
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db(transaction=True)
def test_revenue_items_can_be_filtered_by_department(scoped, authed_api_client, make_revenue_item):
    department = Department.objects.create(council=scoped["council"], department_name="Filter Dept")
    scoped["item"].department = department
    scoped["item"].save(update_fields=["department"])
    other_item = make_revenue_item(scoped["council"], code="DEPOTHERITEM", rate=3000)

    r = authed_api_client(scoped["admin"]).get(f"/api/v1/revenue-items?department={department.id}")
    assert r.status_code == 200, r.content
    codes = {row["harmonised_code"] for row in r.json()["results"]}
    assert codes == {"DEPITEM"}
    assert other_item.harmonised_code not in codes
