"""
Account-creation endpoints for consultant-manager and stakeholder (GLOBAL_VIEW)
logins, and the GLOBAL_VIEW permission tightening that goes with them — a
stakeholder must be read-only and see only aggregate figures, never a payer,
bill, payment or sub-consultant's name. See StakeholderViewSet's docstring.

Also covers FieldAgentViewSet.portfolio — a consultant assigning a subset of
their own ConsultantPortfolio to a specific agent they onboarded.
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole, AppUser, SubConsultant
from apps.billing.models import Bill
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.revenue.models import ConsultantPortfolio
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_registration_item):
    council = make_council(code="ACC")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="acc-admin")
        # SubConsultantViewSet.perform_create bills every new consultant's
        # registration against this — see CONSULTANT_REGISTRATION_ITEM_CODE.
        make_registration_item(council)
        yield {"council": council, "ward": ward, "admin": admin}


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_without_manager_fields_creates_no_login(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {"consultant_name": "No Login Co", "contract_ref": "CR-NL", "commission_rate": "30.00", "registration_ward_id": scoped["ward"].id},
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
            "registration_ward_id": scoped["ward"].id,
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
def test_onboard_consultant_with_duplicate_contract_ref_is_rejected_not_500(scoped, authed_api_client):
    # Audit finding, live: uniq_contract_ref_per_council's IntegrityError isn't a
    # DRF APIException, so it skipped DRF's exception handler and 500'd with no
    # detail at all (no response body, nothing in server logs with DEBUG off).
    admin_client = authed_api_client(scoped["admin"])
    first = admin_client.post(
        "/api/v1/consultants",
        {"consultant_name": "First Co", "contract_ref": "CR-DUPE", "commission_rate": "30.00", "registration_ward_id": scoped["ward"].id},
        format="json",
    )
    assert first.status_code == 201, first.content

    second = admin_client.post(
        "/api/v1/consultants",
        {"consultant_name": "Second Co", "contract_ref": "CR-DUPE", "commission_rate": "30.00", "registration_ward_id": scoped["ward"].id},
        format="json",
    )
    assert second.status_code == 400, second.content
    assert "contract_ref" in second.json()
    assert not AppUser.objects.filter(consultant__consultant_name="Second Co").exists()


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_manager_username_without_full_name_rejected(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Bad Co", "contract_ref": "CR-B", "commission_rate": "30.00", "manager_username": "badco1",
            "registration_ward_id": scoped["ward"].id,
        },
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


@pytest.mark.django_db(transaction=True)
def test_cannot_assign_same_item_twice_to_agent(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent, make_revenue_item,
):
    council = scoped["council"]
    consultant = make_consultant(council, name="No-Dupe Co")
    manager = make_user(council, username="acc-nodupe-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    agent_user = make_user(council, username="acc-nodupe-agent", access_level=AppRole.AGENT, consultant=consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-NODUPE-1")
    item = make_revenue_item(council, code="ACCNODUPE", rate=1000)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item)

    first = authed_api_client(manager).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert first.status_code == 201, first.content

    second = authed_api_client(manager).post(f"/api/v1/agents/{agent.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert second.status_code == 400, second.content
    assert agent.portfolio.filter(effective_to__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_cannot_assign_same_item_twice_to_consultant(scoped, authed_api_client, make_consultant, make_revenue_item):
    consultant = make_consultant(scoped["council"], name="No-Dupe Consultant Co")
    item = make_revenue_item(scoped["council"], code="ACCNODUPECONS", rate=1000)

    first = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/portfolio", {"council_revenue_item": item.id}, format="json",
    )
    assert first.status_code == 201, first.content

    second = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/portfolio", {"council_revenue_item": item.id}, format="json",
    )
    assert second.status_code == 400, second.content
    assert consultant.portfolio.filter(effective_to__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_can_reassign_item_to_consultant_after_revoking(scoped, authed_api_client, make_consultant, make_revenue_item):
    consultant = make_consultant(scoped["council"], name="Reassign Co")
    item = make_revenue_item(scoped["council"], code="ACCREASSIGN", rate=1000)
    admin_client = authed_api_client(scoped["admin"])

    first = admin_client.post(f"/api/v1/consultants/{consultant.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    entry_id = first.json()["id"]
    ended = admin_client.post(f"/api/v1/consultants/{consultant.id}/portfolio/{entry_id}/end")
    assert ended.status_code == 200, ended.content

    reassigned = admin_client.post(f"/api/v1/consultants/{consultant.id}/portfolio", {"council_revenue_item": item.id}, format="json")
    assert reassigned.status_code == 201, reassigned.content  # the old row is now effective_to-set, so this isn't a duplicate


@pytest.mark.django_db(transaction=True)
def test_consultant_search_matches_name_and_contract_ref(scoped, authed_api_client, make_consultant):
    make_consultant(scoped["council"], name="Findable Revenue Partners", contract_ref="CR-FIND")
    make_consultant(scoped["council"], name="Other Co", contract_ref="CR-OTHER")
    client = authed_api_client(scoped["admin"])

    by_name = client.get("/api/v1/consultants?q=Findable")
    assert {r["consultant_name"] for r in by_name.json()["results"]} == {"Findable Revenue Partners"}

    by_ref = client.get("/api/v1/consultants?q=CR-FIND")
    assert {r["consultant_name"] for r in by_ref.json()["results"]} == {"Findable Revenue Partners"}


@pytest.mark.django_db(transaction=True)
def test_agent_search_matches_code_and_name(scoped, authed_api_client, make_field_agent, make_user):
    findable_user = make_user(scoped["council"], username="acc-search-findable", access_level=AppRole.AGENT)
    make_field_agent(scoped["council"], findable_user, agent_code="AGT-FINDME-1")
    other_user = make_user(scoped["council"], username="acc-search-other", access_level=AppRole.AGENT)
    make_field_agent(scoped["council"], other_user, agent_code="AGT-OTHER-1")
    # AppUser.create_user doesn't set full_name via make_user — set it directly for the name-search half.
    findable_user.full_name = "Zainab Findable"
    findable_user.save(update_fields=["full_name"])

    client = authed_api_client(scoped["admin"])
    by_code = client.get("/api/v1/agents?q=FINDME")
    assert {r["agent_code"] for r in by_code.json()["results"]} == {"AGT-FINDME-1"}

    by_name = client.get("/api/v1/agents?q=Zainab")
    assert {r["agent_code"] for r in by_name.json()["results"]} == {"AGT-FINDME-1"}
    assert by_name.json()["results"][0]["agent_full_name"] == "Zainab Findable"


@pytest.mark.django_db(transaction=True)
def test_me_includes_agent_identity_for_agent_role(scoped, authed_api_client, make_field_agent, make_user):
    agent_user = make_user(scoped["council"], username="acc-me-agent", access_level=AppRole.AGENT)
    agent = make_field_agent(scoped["council"], agent_user, ward=scoped["ward"], agent_code="AGT-ME-1")

    r = authed_api_client(agent_user).get("/api/v1/auth/me")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["agent_id"] == agent.id
    assert body["agent_code"] == "AGT-ME-1"
    assert body["assigned_ward_id"] == scoped["ward"].id
    assert body["assigned_ward_name"] == scoped["ward"].ward_name


@pytest.mark.django_db(transaction=True)
def test_me_agent_fields_are_null_for_non_agent(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/auth/me")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["agent_id"] is None
    assert body["agent_code"] is None
    assert body["assigned_ward_id"] is None


@pytest.mark.django_db(transaction=True)
def test_agent_can_view_own_activity_not_anothers(scoped, authed_api_client, make_field_agent, make_user):
    own_user = make_user(scoped["council"], username="acc-activity-own", access_level=AppRole.AGENT)
    own_agent = make_field_agent(scoped["council"], own_user, ward=scoped["ward"], agent_code="AGT-ACT-OWN")
    other_user = make_user(scoped["council"], username="acc-activity-other", access_level=AppRole.AGENT)
    other_agent = make_field_agent(scoped["council"], other_user, ward=scoped["ward"], agent_code="AGT-ACT-OTHER")

    own_client = authed_api_client(own_user)
    r_own = own_client.get(f"/api/v1/agents/{own_agent.id}/activity")
    assert r_own.status_code == 200, r_own.content

    r_other = own_client.get(f"/api/v1/agents/{other_agent.id}/activity")
    assert r_other.status_code == 404, r_other.content  # get_queryset() scopes AGENT to their own row first


# --- profile editing + password changing (any authenticated role) ---

@pytest.mark.django_db(transaction=True)
def test_user_can_update_their_own_profile(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).patch(
        "/api/v1/auth/me", {"full_name": "Updated Name", "email": "updated@example.com", "phone": "08099998888"}, format="json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["full_name"] == "Updated Name"
    assert body["email"] == "updated@example.com"
    assert body["phone"] == "08099998888"
    # full MeSerializer shape, not just the three writable fields — access_level
    # in particular is what the frontend's route guarding depends on.
    assert body["access_level"] == AppRole.COUNCIL_ADMIN

    scoped["admin"].refresh_from_db()
    assert scoped["admin"].full_name == "Updated Name"


@pytest.mark.django_db(transaction=True)
def test_profile_update_cannot_smuggle_in_admin_managed_fields(scoped, authed_api_client, make_user):
    other_admin = make_user(scoped["council"], username="acc-other-admin")
    r = authed_api_client(scoped["admin"]).patch(
        "/api/v1/auth/me",
        {"full_name": "Still Admin", "access_level": "GLOBAL_VIEW", "consultant": None, "council": other_admin.council_id},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["access_level"] == AppRole.COUNCIL_ADMIN  # unwritable field, silently ignored, not smuggled through

    scoped["admin"].refresh_from_db()
    assert scoped["admin"].role.access_level == AppRole.COUNCIL_ADMIN


@pytest.mark.django_db(transaction=True)
def test_profile_update_rejects_blank_full_name(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).patch("/api/v1/auth/me", {"full_name": "   "}, format="json")
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_user_can_change_their_own_password(scoped, authed_api_client):
    scoped["admin"].set_password("original-pw-123")
    scoped["admin"].save()

    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/auth/change-password", {"current_password": "original-pw-123", "new_password": "a-genuinely-new-pw-456"}, format="json",
    )
    assert r.status_code == 204, r.content

    scoped["admin"].refresh_from_db()
    assert scoped["admin"].check_password("a-genuinely-new-pw-456")
    assert not scoped["admin"].check_password("original-pw-123")


@pytest.mark.django_db(transaction=True)
def test_change_password_rejects_wrong_current_password(scoped, authed_api_client):
    scoped["admin"].set_password("original-pw-123")
    scoped["admin"].save()

    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/auth/change-password", {"current_password": "totally-wrong", "new_password": "a-genuinely-new-pw-456"}, format="json",
    )
    assert r.status_code == 400, r.content

    scoped["admin"].refresh_from_db()
    assert scoped["admin"].check_password("original-pw-123")  # unchanged


@pytest.mark.django_db(transaction=True)
def test_change_password_rejects_a_weak_new_password(scoped, authed_api_client):
    scoped["admin"].set_password("original-pw-123")
    scoped["admin"].save()

    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/auth/change-password", {"current_password": "original-pw-123", "new_password": "short"}, format="json",
    )
    assert r.status_code == 400, r.content

    scoped["admin"].refresh_from_db()
    assert scoped["admin"].check_password("original-pw-123")  # unchanged


@pytest.mark.django_db(transaction=True)
def test_admin_onboarding_agent_without_consultant_is_rejected(scoped, authed_api_client):
    """Council-direct agents (no consultant) are retired — admin must assign
    one explicitly now, same as a consultant onboarding their own agent
    always was assigned to themselves automatically."""
    before = AppUser.objects.count()
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/agents", {"full_name": "No Consultant Agent", "username": "no-consultant-agent"}, format="json",
    )
    assert r.status_code == 400, r.content
    assert AppUser.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_admin_onboarding_agent_with_consultant_succeeds(scoped, authed_api_client, make_consultant):
    consultant = make_consultant(scoped["council"], name="Agent Assign Co")
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/agents",
        {"full_name": "Assigned Agent", "username": "assigned-agent", "consultant_id": consultant.id},
        format="json",
    )
    assert r.status_code == 201, r.content
    assert AppUser.objects.get(username="assigned-agent").consultant_id == consultant.id


@pytest.mark.django_db(transaction=True)
def test_admin_onboarding_agent_with_another_councils_consultant_is_rejected(scoped, authed_api_client, make_consultant, make_council):
    other_council = make_council(code="ACC2")
    with transaction.atomic():
        set_council_context(other_council.id)
        foreign_consultant = make_consultant(other_council, name="Foreign Co")
    before = AppUser.objects.count()
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/agents",
        {"full_name": "Cross Tenant Agent", "username": "cross-tenant-agent", "consultant_id": foreign_consultant.id},
        format="json",
    )
    assert r.status_code == 400, r.content
    assert AppUser.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_consultant_onboarding_agent_is_still_auto_assigned_to_self(scoped, authed_api_client, make_consultant, make_user):
    """Unaffected by the admin-side requirement above — a consultant's own
    onboarding flow never took consultant_id from the request at all."""
    consultant = make_consultant(scoped["council"], name="Self Assign Co")
    manager = make_user(scoped["council"], username="self-assign-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    r = authed_api_client(manager).post(
        "/api/v1/agents", {"full_name": "Own Agent", "username": "own-agent"}, format="json",
    )
    assert r.status_code == 201, r.content
    assert AppUser.objects.get(username="own-agent").consultant_id == consultant.id


# --- gap fix: a PENDING consultant manager could previously self-onboard agents ---

@pytest.mark.django_db(transaction=True)
def test_pending_consultant_manager_cannot_onboard_agent(scoped, authed_api_client, make_user, make_consultant):
    consultant = make_consultant(scoped["council"], name="Pending Co", contract_ref="CR-PEND", status=SubConsultant.PENDING)
    manager = make_user(scoped["council"], username="acc-pend-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)

    r = authed_api_client(manager).post(
        "/api/v1/agents", {"full_name": "Blocked Agent", "username": "blocked-agent"}, format="json",
    )
    assert r.status_code == 400, r.content
    assert not AppUser.objects.filter(username="blocked-agent").exists()


@pytest.mark.django_db(transaction=True)
def test_active_consultant_manager_can_still_onboard_agent(scoped, authed_api_client, make_user, make_consultant):
    consultant = make_consultant(scoped["council"], name="Active Onboard Co", contract_ref="CR-ACTIVEOB", status=SubConsultant.ACTIVE)
    manager = make_user(scoped["council"], username="acc-active-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)

    r = authed_api_client(manager).post(
        "/api/v1/agents", {"full_name": "Allowed Agent", "username": "allowed-agent"}, format="json",
    )
    assert r.status_code == 201, r.content


# --- contract dates + expiry flag (item 1) ---

@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_with_contract_dates(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Dated Co", "contract_ref": "CR-DATED", "commission_rate": "30.00",
            "registration_ward_id": scoped["ward"].id,
            "contract_start_date": "2026-01-01", "contract_end_date": "2026-12-31",
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["contract_start_date"] == "2026-01-01"
    assert body["contract_end_date"] == "2026-12-31"
    assert body["is_contract_expired"] is False


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_rejects_contract_end_before_start(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Backwards Co", "contract_ref": "CR-BACK", "commission_rate": "30.00",
            "registration_ward_id": scoped["ward"].id,
            "contract_start_date": "2026-12-31", "contract_end_date": "2026-01-01",
        },
        format="json",
    )
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_is_contract_expired_true_once_end_date_has_passed(scoped, authed_api_client, make_consultant):
    consultant = make_consultant(scoped["council"], name="Expired Co", contract_ref="CR-EXP")
    consultant.contract_end_date = datetime.date(2020, 1, 1)
    consultant.save(update_fields=["contract_end_date"])

    r = authed_api_client(scoped["admin"]).get("/api/v1/consultants")
    row = next(row for row in r.json()["results"] if row["id"] == consultant.id)
    assert row["is_contract_expired"] is True


@pytest.mark.django_db(transaction=True)
def test_contract_dates_action_updates_dates(scoped, authed_api_client, make_consultant):
    consultant = make_consultant(scoped["council"], name="Editable Co", contract_ref="CR-EDIT")
    r = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/contract_dates",
        {"contract_start_date": "2026-02-01", "contract_end_date": "2027-02-01"},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["contract_start_date"] == "2026-02-01"
    assert r.json()["contract_end_date"] == "2027-02-01"

    consultant.refresh_from_db()
    assert consultant.contract_start_date == datetime.date(2026, 2, 1)


@pytest.mark.django_db(transaction=True)
def test_contract_dates_action_rejects_end_before_already_set_start(scoped, authed_api_client, make_consultant):
    consultant = make_consultant(scoped["council"], name="Partial Co", contract_ref="CR-PARTIAL")
    first = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/contract_dates", {"contract_start_date": "2026-06-01"}, format="json",
    )
    assert first.status_code == 200, first.content

    second = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/contract_dates", {"contract_end_date": "2026-01-01"}, format="json",
    )
    assert second.status_code == 400, second.content


@pytest.mark.django_db(transaction=True)
def test_contract_dates_action_is_council_admin_only(scoped, authed_api_client, make_user, make_consultant):
    consultant = make_consultant(scoped["council"], name="Guarded Co", contract_ref="CR-GUARD")
    manager = make_user(scoped["council"], username="acc-guard-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    r = authed_api_client(manager).post(
        f"/api/v1/consultants/{consultant.id}/contract_dates", {"contract_start_date": "2026-06-01"}, format="json",
    )
    assert r.status_code == 403, r.content


# --- consultants onboarded as payers, billed for registration (item 7) ---

@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_creates_registration_payer_and_bill(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Billable Co", "contract_ref": "CR-BILL", "commission_rate": "30.00",
            "registration_ward_id": scoped["ward"].id,
        },
        format="json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["registration_payer_ref"] is not None

    consultant = SubConsultant.objects.get(id=body["id"])
    assert consultant.registration_payer is not None
    assert consultant.registration_payer.full_name == "Billable Co"
    bill = Bill.objects.get(payer=consultant.registration_payer)
    assert bill.total_amount == 120000


@pytest.mark.django_db(transaction=True)
def test_onboard_consultant_without_registration_ward_rejected(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {"consultant_name": "No Ward Co", "contract_ref": "CR-NOWARD", "commission_rate": "30.00"},
        format="json",
    )
    assert r.status_code == 400, r.content
    assert "registration_ward_id" in r.json()


@pytest.mark.django_db(transaction=True)
def test_status_change_to_active_blocked_while_registration_bill_unpaid(scoped, authed_api_client):
    onboard = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Unpaid Co", "contract_ref": "CR-UNPAID", "commission_rate": "30.00",
            "registration_ward_id": scoped["ward"].id,
        },
        format="json",
    )
    consultant_id = onboard.json()["id"]

    activate = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant_id}/status_change", {"status": "ACTIVE"}, format="json",
    )
    assert activate.status_code == 400, activate.content
    assert SubConsultant.objects.get(id=consultant_id).status == SubConsultant.PENDING


@pytest.mark.django_db(transaction=True)
def test_status_change_to_active_allowed_once_registration_bill_paid(scoped, authed_api_client):
    onboard = authed_api_client(scoped["admin"]).post(
        "/api/v1/consultants",
        {
            "consultant_name": "Paid Co", "contract_ref": "CR-PAID", "commission_rate": "30.00",
            "registration_ward_id": scoped["ward"].id,
        },
        format="json",
    )
    consultant = SubConsultant.objects.get(id=onboard.json()["id"])
    bill = Bill.objects.get(payer=consultant.registration_payer)

    channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
    post_payment(council_id=scoped["council"].id, bill=bill, channel=channel, amount=bill.total_amount, posted_by=scoped["admin"])

    activate = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/status_change", {"status": "ACTIVE"}, format="json",
    )
    assert activate.status_code == 200, activate.content
    assert activate.json()["status"] == "ACTIVE"


@pytest.mark.django_db(transaction=True)
def test_status_change_to_active_unaffected_for_consultant_with_no_registration_payer(scoped, authed_api_client, make_consultant):
    """A consultant onboarded before registration_payer existed (or created
    directly, as fixtures do) has none set — the new balance check must not
    retroactively block it."""
    consultant = make_consultant(scoped["council"], name="Legacy Co", contract_ref="CR-LEGACY", status=SubConsultant.PENDING)
    r = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/status_change", {"status": "ACTIVE"}, format="json",
    )
    assert r.status_code == 200, r.content


# --- revenue officer role (item 3) ---

@pytest.mark.django_db(transaction=True)
def test_onboard_revenue_officer_scoped_to_consultant(scoped, authed_api_client, make_consultant):
    consultant = make_consultant(scoped["council"], name="Officer Co", contract_ref="CR-OFFICER")
    r = authed_api_client(scoped["admin"]).post(
        f"/api/v1/consultants/{consultant.id}/revenue-officers",
        {"username": "revoff1", "full_name": "Revenue Officer One"},
        format="json",
    )
    assert r.status_code == 201, r.content
    user = AppUser.objects.get(username="revoff1")
    assert user.access_level == AppRole.REVENUE_OFFICER
    assert user.consultant_id == consultant.id


@pytest.mark.django_db(transaction=True)
def test_revenue_officer_onboarding_is_council_admin_only(scoped, authed_api_client, make_user, make_consultant):
    consultant = make_consultant(scoped["council"], name="Guarded Officer Co", contract_ref="CR-GUARDOFF")
    manager = make_user(scoped["council"], username="acc-guardoff-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    r = authed_api_client(manager).post(
        f"/api/v1/consultants/{consultant.id}/revenue-officers",
        {"username": "revoff-blocked", "full_name": "Blocked"},
        format="json",
    )
    assert r.status_code == 403, r.content
    assert not AppUser.objects.filter(username="revoff-blocked").exists()


@pytest.mark.django_db(transaction=True)
def test_revenue_officer_list_scoped_to_own_consultant(scoped, authed_api_client, make_consultant):
    own = make_consultant(scoped["council"], name="Own Officer Co", contract_ref="CR-OWNOFFICER")
    other = make_consultant(scoped["council"], name="Other Officer Co", contract_ref="CR-OTHEROFFICER")
    admin_client = authed_api_client(scoped["admin"])
    admin_client.post(f"/api/v1/consultants/{own.id}/revenue-officers", {"username": "own-off", "full_name": "Own Officer"}, format="json")
    admin_client.post(f"/api/v1/consultants/{other.id}/revenue-officers", {"username": "other-off", "full_name": "Other Officer"}, format="json")

    r = admin_client.get(f"/api/v1/consultants/{own.id}/revenue-officers")
    assert r.status_code == 200, r.content
    assert {row["username"] for row in r.json()} == {"own-off"}


@pytest.mark.django_db(transaction=True)
def test_revenue_officer_sees_same_portfolio_as_consultant_but_read_only(
    scoped, authed_api_client, make_consultant, make_user, make_payer, make_revenue_item,
):
    council = scoped["council"]
    consultant = make_consultant(council, name="Portfolio Officer Co", contract_ref="CR-PORTOFFICER")
    manager = make_user(council, username="acc-portoff-mgr", access_level=AppRole.CONSULTANT, consultant=consultant)
    officer = make_user(council, username="acc-portoff-officer", access_level=AppRole.REVENUE_OFFICER, consultant=consultant)

    item = make_revenue_item(council, code="ACCOFFITEM", rate=5000)
    payer = make_payer(council, scoped["ward"], manager, name="Officer Payer")
    bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=manager)

    r = authed_api_client(officer).get("/api/v1/bills")
    assert r.status_code == 200, r.content
    assert [row["id"] for row in r.json()["results"]] == [bill.id]

    r_post = authed_api_client(officer).post("/api/v1/bills", {"payer_id": payer.id, "bill_all_drafts": True}, format="json")
    assert r_post.status_code == 403, r_post.content

    r_payers = authed_api_client(officer).get("/api/v1/payers")
    assert r_payers.status_code == 200, r_payers.content
    assert [row["id"] for row in r_payers.json()["results"]] == [payer.id]
