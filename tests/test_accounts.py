"""
Account-creation endpoints for consultant-manager and stakeholder (GLOBAL_VIEW)
logins, and the GLOBAL_VIEW permission tightening that goes with them — a
stakeholder must be read-only and see only aggregate figures, never a payer,
bill, payment or sub-consultant's name. See StakeholderViewSet's docstring.

Also covers FieldAgentViewSet.portfolio — a consultant assigning a subset of
their own ConsultantPortfolio to a specific agent they onboarded.
"""
import pytest
from django.db import transaction

from apps.accounts.models import AppRole, AppUser
from apps.revenue.models import ConsultantPortfolio
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
def test_admin_can_assign_consultant_portfolio(scoped, authed_api_client, make_consultant, make_revenue_item):
    consultant = make_consultant(scoped["council"], name="Assignable Co", contract_ref="CR-ASSIGNABLE")
    item = make_revenue_item(scoped["council"], code="ACCASSIGNABLE", rate=5000)

    r = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/portfolio", {"council_revenue_item": item.id}, format="json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["consultant"] == consultant.id


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


@pytest.mark.django_db(transaction=True)
def test_consultant_assigns_item_from_own_portfolio_to_own_agent(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent, make_revenue_item,
):
    council = scoped["council"]
    consultant = make_consultant(council, name="Portfolio-Assign Co")
    manager = make_user(council, username="acc-pa-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    agent_user = make_user(council, username="acc-pa-agent", access_level=AppRole.AGENT, consultant=consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-PA-1")
    item = make_revenue_item(council, code="ACCPAITEM", rate=5000)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item)

    r = authed_api_client(manager).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert r.status_code == 201, r.content

    r_get = authed_api_client(manager).get(f"/api/v1/agents/{agent.id}/portfolio")
    assert r_get.status_code == 200, r_get.content
    assert [row["council_revenue_item"] for row in r_get.json()] == [item.id]


@pytest.mark.django_db(transaction=True)
def test_consultant_cannot_assign_item_outside_own_portfolio_to_agent(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent, make_revenue_item,
):
    council = scoped["council"]
    consultant = make_consultant(council, name="Narrow Portfolio Co")
    manager = make_user(council, username="acc-narrow-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    agent_user = make_user(council, username="acc-narrow-agent", access_level=AppRole.AGENT, consultant=consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-NARROW-1")
    # Deliberately never added to `consultant`'s own ConsultantPortfolio.
    outside_item = make_revenue_item(council, code="ACCOUTSIDE", rate=9000)

    r = authed_api_client(manager).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": outside_item.id}, format="json")
    assert r.status_code == 400, r.content
    assert not agent.portfolio.exists()


@pytest.mark.django_db(transaction=True)
def test_consultant_cannot_assign_item_to_another_consultants_agent(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent, make_revenue_item,
):
    council = scoped["council"]
    owning_consultant = make_consultant(council, name="Owner Co", contract_ref="CR-OWNER")
    other_consultant = make_consultant(council, name="Outsider Co", contract_ref="CR-OUTSIDER")
    other_manager = make_user(council, username="acc-outsider-mgr", access_level=AppRole.CONSULTANT, consultant=other_consultant)
    agent_user = make_user(council, username="acc-owned-agent", access_level=AppRole.AGENT, consultant=owning_consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-OWNED-1")
    item = make_revenue_item(council, code="ACCCROSS", rate=4000)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=owning_consultant, council_revenue_item=item)

    r = authed_api_client(other_manager).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert r.status_code == 404, r.content  # FieldAgentViewSet.get_queryset() already scopes CONSULTANT to their own agents


@pytest.mark.django_db(transaction=True)
def test_admin_can_assign_item_to_any_consultants_agent(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent, make_revenue_item,
):
    council = scoped["council"]
    consultant = make_consultant(council, name="Admin-Assign Co")
    agent_user = make_user(council, username="acc-adminassign-agent", access_level=AppRole.AGENT, consultant=consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-ADMINASSIGN-1")
    item = make_revenue_item(council, code="ACCADMINASSIGN", rate=6000)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item)

    r = authed_api_client(scoped["admin"]).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert r.status_code == 201, r.content


@pytest.mark.django_db(transaction=True)
def test_cannot_assign_item_to_council_direct_agent(scoped, authed_api_client, make_field_agent, make_revenue_item):
    council = scoped["council"]
    agent = make_field_agent(council, scoped["admin"], agent_code="AGT-DIRECT-1")  # no consultant
    item = make_revenue_item(council, code="ACCDIRECT", rate=3000)

    r = authed_api_client(scoped["admin"]).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_end_agent_portfolio_revokes_assignment(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent, make_revenue_item,
):
    council = scoped["council"]
    consultant = make_consultant(council, name="Revoke-Test Co")
    manager = make_user(council, username="acc-revoke-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    agent_user = make_user(council, username="acc-revoke-agent", access_level=AppRole.AGENT, consultant=consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-REVOKE-1")
    item = make_revenue_item(council, code="ACCREVOKE", rate=2000)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item)

    created = authed_api_client(manager).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    entry_id = created.json()["id"]

    r = authed_api_client(manager).post(f"/api/v1/agents/{agent.id}/portfolio/{entry_id}/end")
    assert r.status_code == 200, r.content
    assert r.json()["effective_to"] is not None

    r_get = authed_api_client(manager).get(f"/api/v1/agents/{agent.id}/portfolio")
    assert r_get.json() == []
