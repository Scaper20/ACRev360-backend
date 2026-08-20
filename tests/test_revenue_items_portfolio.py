"""
CouncilRevenueItemViewSet must scope to a consultant's own ConsultantPortfolio
— unlike payers/bills/payments (scoped via common.scoping.portfolio_filter,
which walks a payer's enumerated_by__consultant_id), a revenue item has no
payer to walk through, so it was never covered by that helper and leaked the
full council chart of revenue to every consultant. Reported live: a
consultant could see and select revenue items nobody had assigned them.
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole
from apps.revenue.models import AgentPortfolio, ConsultantPortfolio
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_revenue_item):
    council = make_council(code="RVI")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="rvi-admin")
        item_a = make_revenue_item(council, code="RVIITEMA", name="Item A", rate=5000)
        item_b = make_revenue_item(council, code="RVIITEMB", name="Item B", rate=7000)
        yield {"council": council, "ward": ward, "admin": admin, "item_a": item_a, "item_b": item_b}


@pytest.mark.django_db(transaction=True)
def test_consultant_only_sees_assigned_revenue_items(scoped, authed_api_client, make_consultant, make_user):
    council, item_a, item_b = scoped["council"], scoped["item_a"], scoped["item_b"]
    consultant = make_consultant(council, name="Portfolio Co")
    consultant_user = make_user(council, username="rvi-consultant", access_level=AppRole.CONSULTANT, consultant=consultant)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item_a)

    r = authed_api_client(consultant_user).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    codes = {row["harmonised_code"] for row in r.json()["results"]}
    assert codes == {item_a.harmonised_code}
    assert item_b.harmonised_code not in codes


@pytest.mark.django_db(transaction=True)
def test_consultant_with_no_portfolio_sees_no_revenue_items(scoped, authed_api_client, make_consultant, make_user):
    consultant = make_consultant(scoped["council"], name="Empty Portfolio Co")
    consultant_user = make_user(scoped["council"], username="rvi-empty-consultant", access_level=AppRole.CONSULTANT, consultant=consultant)

    r = authed_api_client(consultant_user).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    assert r.json()["results"] == []


@pytest.mark.django_db(transaction=True)
def test_consultant_does_not_see_revoked_portfolio_item(scoped, authed_api_client, make_consultant, make_user):
    council, item_a = scoped["council"], scoped["item_a"]
    consultant = make_consultant(council, name="Revoked Co")
    consultant_user = make_user(council, username="rvi-revoked-consultant", access_level=AppRole.CONSULTANT, consultant=consultant)
    ConsultantPortfolio.objects.create(
        council_id=council.id, consultant=consultant, council_revenue_item=item_a, effective_to=datetime.date.today(),
    )

    r = authed_api_client(consultant_user).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    assert r.json()["results"] == []


@pytest.mark.django_db(transaction=True)
def test_council_admin_sees_all_revenue_items_regardless_of_portfolio(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    codes = {row["harmonised_code"] for row in r.json()["results"]}
    assert codes == {scoped["item_a"].harmonised_code, scoped["item_b"].harmonised_code}


@pytest.mark.django_db(transaction=True)
def test_consultant_cannot_retrieve_unassigned_item_by_id(scoped, authed_api_client, make_consultant, make_user):
    council, item_a, item_b = scoped["council"], scoped["item_a"], scoped["item_b"]
    consultant = make_consultant(council, name="Retrieve Co")
    consultant_user = make_user(council, username="rvi-retrieve-consultant", access_level=AppRole.CONSULTANT, consultant=consultant)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item_a)

    assert authed_api_client(consultant_user).get(f"/api/v1/revenue-items/{item_a.id}").status_code == 200
    assert authed_api_client(consultant_user).get(f"/api/v1/revenue-items/{item_b.id}").status_code == 404


@pytest.mark.django_db(transaction=True)
def test_agent_with_no_agent_portfolio_inherits_full_consultant_portfolio(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent,
):
    """AgentPortfolio is an optional further narrowing, not a mandatory
    allow-list — an agent nobody has specifically restricted yet must not be
    locked out of everything their consultant can already do."""
    council, item_a, item_b = scoped["council"], scoped["item_a"], scoped["item_b"]
    consultant = make_consultant(council, name="Inherit Co")
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item_a)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item_b)
    agent_user = make_user(council, username="rvi-inherit-agent", access_level=AppRole.AGENT, consultant=consultant)
    make_field_agent(council, agent_user, agent_code="AGT-INHERIT-1")

    r = authed_api_client(agent_user).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    codes = {row["harmonised_code"] for row in r.json()["results"]}
    assert codes == {item_a.harmonised_code, item_b.harmonised_code}


@pytest.mark.django_db(transaction=True)
def test_agent_with_own_agent_portfolio_is_restricted_to_it(
    scoped, authed_api_client, make_consultant, make_user, make_field_agent,
):
    council, item_a, item_b = scoped["council"], scoped["item_a"], scoped["item_b"]
    consultant = make_consultant(council, name="Restrict Co")
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item_a)
    ConsultantPortfolio.objects.create(council_id=council.id, consultant=consultant, council_revenue_item=item_b)
    agent_user = make_user(council, username="rvi-restrict-agent", access_level=AppRole.AGENT, consultant=consultant)
    agent = make_field_agent(council, agent_user, agent_code="AGT-RESTRICT-1")
    AgentPortfolio.objects.create(council_id=council.id, agent=agent, council_revenue_item=item_a)

    r = authed_api_client(agent_user).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    codes = {row["harmonised_code"] for row in r.json()["results"]}
    assert codes == {item_a.harmonised_code}  # not item_b, even though it's in the consultant's own portfolio too


@pytest.mark.django_db(transaction=True)
def test_council_direct_agent_with_no_agent_portfolio_sees_everything(scoped, authed_api_client, make_user, make_field_agent):
    agent_user = make_user(scoped["council"], username="rvi-direct-agent", access_level=AppRole.AGENT)  # no consultant
    make_field_agent(scoped["council"], agent_user, agent_code="AGT-COUNCILDIRECT-1")

    r = authed_api_client(agent_user).get("/api/v1/revenue-items")
    assert r.status_code == 200, r.content
    codes = {row["harmonised_code"] for row in r.json()["results"]}
    assert codes == {scoped["item_a"].harmonised_code, scoped["item_b"].harmonised_code}
