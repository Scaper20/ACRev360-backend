"""
Account-creation endpoints for consultant-manager and stakeholder (GLOBAL_VIEW)
logins, and the GLOBAL_VIEW permission tightening that goes with them — a
stakeholder must be read-only and see only aggregate figures, never a payer,
bill, payment or sub-consultant's name. See StakeholderViewSet's docstring.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole, AppUser
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user):
    council = make_council(code="ACC")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="acc-admin")
        yield {"council": council, "ward": ward, "admin": admin}


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_without_manager_fields_creates_no_login(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {"consultant_name": "No Login Co", "contract_ref": "CR-NL", "commission_rate": "30.00"},
        format="json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["has_login"] is False
    assert not AppUser.objects.filter(consultant_id=r.json()["id"]).exists()


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_with_manager_fields_creates_linked_login(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Manager Co", "contract_ref": "CR-M", "commission_rate": "25.00",
            "manager_username": "managerco1", "manager_full_name": "Manager of Manager Co",
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["has_login"] is True

    user = AppUser.objects.get(username="managerco1")
    assert user.access_level == AppRole.CONSULTANT
    assert user.consultant_id == r.json()["id"]
    assert user.check_password("acrev360-2026")  # default fallback, matching FieldAgent's convention


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_manager_username_without_full_name_rejected(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {"consultant_name": "Bad Co", "contract_ref": "CR-B", "commission_rate": "30.00", "manager_username": "badco1"},
        format="json",
    )
    assert r.status_code == 400, r.content
    assert not AppUser.objects.filter(username="badco1").exists()


@pytest.mark.django_db(transaction=True)
def test_consultant_list_is_council_admin_only(scoped, authed_api_client, make_user, make_consultant):
    make_consultant(scoped["council"], name="Some Co")
    consultant_manager = make_user(scoped["council"], username="acc-mgr", access_level=AppRole.CONSULTANT)
    stakeholder = make_user(scoped["council"], username="acc-stake", access_level=AppRole.GLOBAL_VIEW)

    assert authed_api_client(consultant_manager).get("/api/v1/consultants").status_code == 403
    assert authed_api_client(stakeholder).get("/api/v1/consultants").status_code == 403
    assert authed_api_client(scoped["admin"]).get("/api/v1/consultants").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_consultant_can_view_own_portfolio_not_anothers(scoped, authed_api_client, make_user, make_consultant):
    own = make_consultant(scoped["council"], name="Own Co", contract_ref="CR-OWN")
    other = make_consultant(scoped["council"], name="Other Co", contract_ref="CR-OTHER")
    own_manager = make_user(scoped["council"], username="acc-own-mgr", access_level=AppRole.CONSULTANT, consultant=own)

    r_own = authed_api_client(own_manager).get(f"/api/v1/consultants/{own.id}/portfolio")
    assert r_own.status_code == 200, r_own.content

    r_other = authed_api_client(own_manager).get(f"/api/v1/consultants/{other.id}/portfolio")
    assert r_other.status_code == 403, r_other.content


@pytest.mark.django_db(transaction=True)
def test_consultant_cannot_assign_own_portfolio(scoped, authed_api_client, make_user, make_consultant, make_revenue_item):
    own = make_consultant(scoped["council"], name="Own Co 2", contract_ref="CR-OWN2")
    own_manager = make_user(scoped["council"], username="acc-own-mgr-2", access_level=AppRole.CONSULTANT, consultant=own)
    item = make_revenue_item(scoped["council"], code="ACCITEM", rate=5000)

    r = authed_api_client(own_manager).post(
        f"/api/v1/consultants/{own.id}/portfolio", {"consultant": own.id, "council_revenue_item": item.id}, format="json",
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db(transaction=True)
def test_me_exposes_consultant_identity_for_consultant_role_only(scoped, authed_api_client, make_user, make_consultant):
    consultant = make_consultant(scoped["council"], name="Identity Co", rate=33)
    manager = make_user(scoped["council"], username="acc-identity-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)

    r = authed_api_client(manager).get("/api/v1/auth/me")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["consultant_name"] == "Identity Co"
    assert body["consultant_commission_rate"] == "33.00"
    assert body["consultant_status"] == "ACTIVE"

    r_admin = authed_api_client(scoped["admin"]).get("/api/v1/auth/me")
    admin_body = r_admin.json()
    assert admin_body["consultant_name"] is None
    assert admin_body["consultant_commission_rate"] is None


@pytest.mark.django_db(transaction=True)
def test_stakeholder_creation_is_council_admin_only(scoped, authed_api_client, make_user):
    non_admin = make_user(scoped["council"], username="acc-nonadmin", access_level=AppRole.CONSULTANT)
    r = authed_api_client(non_admin).post(
        "/api/v1/stakeholders", {"username": "newstake", "full_name": "New Stakeholder"}, format="json",
    )
    assert r.status_code == 403, r.content
    assert not AppUser.objects.filter(username="newstake").exists()


@pytest.mark.django_db(transaction=True)
def test_stakeholder_creation_and_login(scoped, authed_api_client, api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/stakeholders", {"username": "newstake2", "full_name": "New Stakeholder 2"}, format="json",
    )
    assert r.status_code == 201, r.content
    assert "password" not in r.json()

    user = AppUser.objects.get(username="newstake2")
    assert user.access_level == AppRole.GLOBAL_VIEW
    assert user.check_password("acrev360-2026")

    login = api_client.post("/api/v1/auth/login", {"username": "newstake2", "password": "acrev360-2026"}, format="json")
    assert login.status_code == 200, login.content


@pytest.mark.django_db(transaction=True)
def test_stakeholder_list_scoped_to_council_and_role(scoped, authed_api_client, make_council, make_user):
    make_user(scoped["council"], username="acc-stake-a", access_level=AppRole.GLOBAL_VIEW)
    make_user(scoped["council"], username="acc-stake-b", access_level=AppRole.CONSULTANT)  # different role, must not appear

    other_council = make_council(code="ACC2")
    make_user(other_council, username="acc-stake-other-council", access_level=AppRole.GLOBAL_VIEW)

    r = authed_api_client(scoped["admin"]).get("/api/v1/stakeholders")
    assert r.status_code == 200, r.content
    usernames = {row["username"] for row in r.json()["results"]}
    assert usernames == {"acc-stake-a"}
