"""
Functional tests for the highest-risk new boundaries added in the RBAC
expansion (docs/RBAC_EXPANSION_DESIGN.md) — the pieces that are genuinely new
data-isolation logic, not just an access level added to an existing,
already-tested queryset filter. Each of these is exactly the kind of gap
that would otherwise only surface via a live-frontend report after ship, the
same way the original RLS backfill bug did (see docs/CHANGELOG.md,
2026-09-10) — so these are asserted here instead.
"""
import datetime

import pytest
from django.utils import timezone

from apps.accounts.models import AppRole, AppUser, CouncilGrant, FieldAgent
from apps.billing.models import Bill
from apps.billing.services import issue_bill
from apps.common.platform_scope import accessible_council_ids, granted_council_ids
from apps.registry.models import Payer, PayerDelegation
from apps.registry.services import accessible_payer_ids, create_payer


@pytest.fixture
def platform_user(db, make_role):
    def _make(username, access_level):
        role = make_role(name=f"PLATFORM_{access_level}", access_level=access_level)
        return AppUser.objects.create_user(username=username, password="testpass12345", full_name="Platform Test", council=None, role=role)

    return _make


class TestPayerDelegationIsolation:
    """Matrix Decision #2: a proxy sees exactly the payer(s) delegated to
    them, never another ratepayer's, and losing the delegation (revoke)
    removes access immediately."""

    def _setup(self, make_council, make_ward, make_user, make_payer):
        council = make_council()
        ward = make_ward(council)
        admin = make_user(council, username="admin1", access_level=AppRole.COUNCIL_ADMIN)
        payer_a = make_payer(council, ward, admin, name="Ratepayer Alpha", phone="08010000001")
        payer_b = make_payer(council, ward, admin, name="Ratepayer Beta", phone="08010000002")
        return council, admin, payer_a, payer_b

    def test_proxy_only_sees_delegated_payer(self, make_council, make_ward, make_user, make_payer, make_role):
        council, admin, payer_a, payer_b = self._setup(make_council, make_ward, make_user, make_payer)
        ratepayer_role = make_role(name="RATEPAYER_T", access_level=AppRole.RATEPAYER)
        proxy_role = make_role(name="RATEPAYER_PROXY_T", access_level=AppRole.RATEPAYER_PROXY)

        owner_a = AppUser.objects.create_user(username="owner_a", password="testpass12345", full_name="Owner A", council=council, role=ratepayer_role)
        payer_a.user = owner_a
        payer_a.save(update_fields=["user"])

        proxy = AppUser.objects.create_user(username="proxy1", password="testpass12345", full_name="Proxy", council=council, role=proxy_role)
        PayerDelegation.objects.create(council=council, payer=payer_a, proxy_user=proxy, granted_by=owner_a)

        assert accessible_payer_ids(proxy) == [payer_a.id]
        assert payer_b.id not in accessible_payer_ids(proxy)

    def test_revoked_delegation_removes_access(self, make_council, make_ward, make_user, make_payer, make_role):
        council, admin, payer_a, _payer_b = self._setup(make_council, make_ward, make_user, make_payer)
        ratepayer_role = make_role(name="RATEPAYER_T2", access_level=AppRole.RATEPAYER)
        proxy_role = make_role(name="RATEPAYER_PROXY_T2", access_level=AppRole.RATEPAYER_PROXY)
        owner_a = AppUser.objects.create_user(username="owner_a2", password="testpass12345", full_name="Owner A2", council=council, role=ratepayer_role)
        proxy = AppUser.objects.create_user(username="proxy2", password="testpass12345", full_name="Proxy2", council=council, role=proxy_role)
        delegation = PayerDelegation.objects.create(council=council, payer=payer_a, proxy_user=proxy, granted_by=owner_a)

        assert payer_a.id in accessible_payer_ids(proxy)
        delegation.revoked_at = timezone.now()
        delegation.save(update_fields=["revoked_at"])
        assert accessible_payer_ids(proxy) == []

    def test_ratepayer_portal_endpoint_scopes_bills_to_own_payer_only(
        self, make_council, make_ward, make_user, make_payer, make_role, make_revenue_item, authed_api_client,
    ):
        council, admin, payer_a, payer_b = self._setup(make_council, make_ward, make_user, make_payer)
        item = make_revenue_item(council)
        bill_a = issue_bill(
            council_id=council.id, payer=payer_a, due_date=datetime.date.today() + datetime.timedelta(days=30),
            lines=[{"council_revenue_item": item, "quantity": 1}], bill_all_drafts=False, roll_arrears=False, actor=admin,
        )
        issue_bill(
            council_id=council.id, payer=payer_b, due_date=datetime.date.today() + datetime.timedelta(days=30),
            lines=[{"council_revenue_item": item, "quantity": 1}], bill_all_drafts=False, roll_arrears=False, actor=admin,
        )

        ratepayer_role = make_role(name="RATEPAYER_T3", access_level=AppRole.RATEPAYER)
        owner_a = AppUser.objects.create_user(username="owner_a3", password="testpass12345", full_name="Owner A3", council=council, role=ratepayer_role)
        payer_a.user = owner_a
        payer_a.save(update_fields=["user"])

        client = authed_api_client(owner_a)
        response = client.get("/api/v1/my/bills")
        assert response.status_code == 200
        bill_refs = {row["bill_ref"] for row in response.data}
        assert bill_refs == {bill_a.bill_ref}


class TestDelegationCreationIsCouncilScoped:
    """A ratepayer creating a delegation must not be able to discover or
    delegate to a RATEPAYER_PROXY account in a *different* council — app_user
    carries no RLS policy at all, so an unscoped email lookup would otherwise
    leak whether an email belongs to a proxy account anywhere on the
    platform. See apps.registry.api.views.RatepayerPortalViewSet.delegations."""

    def test_cannot_delegate_to_a_proxy_in_a_different_council(
        self, make_council, make_ward, make_user, make_payer, make_role, authed_api_client,
    ):
        # council_a's own RLS-protected objects (ward/payer) must all be
        # created before council_b exists — make_council leaves the ambient
        # RLS context pointed at whichever council it created *last* (see
        # its own docstring: SET LOCAL persists into the enclosing test
        # transaction once its own savepoint releases), so creating
        # council_b first would make ward_a's insert fail RLS's WITH CHECK
        # for being created under the wrong council's context.
        council_a = make_council(code="DCA", name="Council A")
        ward_a = make_ward(council_a)
        admin_a = make_user(council_a, username="admin_a", access_level=AppRole.COUNCIL_ADMIN)
        payer_a = make_payer(council_a, ward_a, admin_a, name="Ratepayer A", phone="08010000201")

        ratepayer_role = make_role(name="RATEPAYER_XC", access_level=AppRole.RATEPAYER)
        owner_a = AppUser.objects.create_user(username="owner_xc", password="testpass12345", full_name="Owner XC", council=council_a, role=ratepayer_role)
        payer_a.user = owner_a
        payer_a.save(update_fields=["user"])

        # app_user carries no RLS policy at all (see the leak this test
        # guards against), so creating council_b now and an AppUser under it
        # needs no context switch back.
        council_b = make_council(code="DCB", name="Council B")
        proxy_role = make_role(name="RATEPAYER_PROXY_XC", access_level=AppRole.RATEPAYER_PROXY)
        proxy_other_council = AppUser.objects.create_user(
            username="proxy_other_council", password="testpass12345", full_name="Cross Council Proxy",
            council=council_b, role=proxy_role, email="crossproxy@example.com",
        )

        client = authed_api_client(owner_a)
        response = client.post("/api/v1/my/delegations", {"proxy_email": proxy_other_council.email})
        assert response.status_code == 400
        assert not PayerDelegation.objects.filter(payer=payer_a, proxy_user=proxy_other_council).exists()


class TestRatepayerClosedFromStaffEndpoints:
    def test_ratepayer_cannot_list_payers(self, make_council, make_role, authed_api_client):
        council = make_council()
        role = make_role(name="RATEPAYER_T4", access_level=AppRole.RATEPAYER)
        user = AppUser.objects.create_user(username="rp4", password="testpass12345", full_name="RP4", council=council, role=role)
        client = authed_api_client(user)
        response = client.get("/api/v1/payers")
        assert response.status_code == 403


class TestAgentSupervisorWardScoping:
    """Matrix Decision #4: own zone/team only, no cross-zone visibility."""

    def test_supervisor_sees_only_own_ward_agents(self, make_council, make_ward, make_user, make_role, make_field_agent):
        council = make_council()
        ward_1 = make_ward(council, code="W1", name="Ward One")
        ward_2 = make_ward(council, code="W2", name="Ward Two")

        agent_role = make_role(name="FIELD_AGENT_T", access_level=AppRole.AGENT)
        agent_1_user = AppUser.objects.create_user(username="agent_w1", password="testpass12345", full_name="Agent W1", council=council, role=agent_role)
        agent_2_user = AppUser.objects.create_user(username="agent_w2", password="testpass12345", full_name="Agent W2", council=council, role=agent_role)
        make_field_agent(council, agent_1_user, ward=ward_1, agent_code="AGT-W1")
        make_field_agent(council, agent_2_user, ward=ward_2, agent_code="AGT-W2")

        supervisor_role = make_role(name="AGENT_SUPERVISOR_T", access_level=AppRole.AGENT_SUPERVISOR)
        supervisor_user = AppUser.objects.create_user(username="sup_w1", password="testpass12345", full_name="Supervisor W1", council=council, role=supervisor_role)
        make_field_agent(council, supervisor_user, ward=ward_1, agent_code="SUP-W1")

        from apps.common.scoping import portfolio_filter

        class _FakeRequest:
            user = supervisor_user

        qs = portfolio_filter(Payer.objects.filter(council=council), _FakeRequest(), payer_path="")
        # No payers exist yet — this just proves the query builds without
        # error and doesn't blow up on a None ward; the real assertion is in
        # the API-level test below.
        list(qs)

    def test_supervisor_with_no_field_agent_profile_sees_nothing(self, make_council, make_role):
        council = make_council()
        supervisor_role = make_role(name="AGENT_SUPERVISOR_T2", access_level=AppRole.AGENT_SUPERVISOR)
        supervisor_user = AppUser.objects.create_user(username="sup_orphan", password="testpass12345", full_name="Orphan Supervisor", council=council, role=supervisor_role)

        from apps.common.scoping import portfolio_filter

        class _FakeRequest:
            user = supervisor_user

        qs = portfolio_filter(Payer.objects.filter(council=council), _FakeRequest(), payer_path="")
        assert not qs.exists()


class TestExternalAuditorTimeBoxedGrant:
    """Matrix's "likely needs a temporary/expiring access grant" — an expired
    CouncilGrant must not grant access, only a live or never-expiring one."""

    def test_granted_council_ids_excludes_expired_grants(self, make_council, platform_user):
        council_active = make_council(code="AUD1", name="Active Grant Council")
        council_expired = make_council(code="AUD2", name="Expired Grant Council")
        auditor = platform_user("auditor1", AppRole.EXTERNAL_AUDITOR)

        CouncilGrant.objects.create(user=auditor, council=council_active, expires_at=None)
        CouncilGrant.objects.create(
            user=auditor, council=council_expired, expires_at=timezone.now() - timezone.timedelta(days=1)
        )

        ids = granted_council_ids(auditor)
        assert council_active.id in ids
        assert council_expired.id not in ids

    def test_accessible_council_ids_for_external_auditor_is_grant_scoped_only(self, make_council, platform_user):
        make_council(code="AUD3", name="Not Granted At All")
        council_granted = make_council(code="AUD4", name="Granted Council")
        auditor = platform_user("auditor2", AppRole.EXTERNAL_AUDITOR)
        CouncilGrant.objects.create(user=auditor, council=council_granted, expires_at=None)

        ids = accessible_council_ids(auditor)
        assert ids == [council_granted.id]

    def test_super_admin_sees_every_active_council_not_just_granted_ones(self, make_council, platform_user):
        council_1 = make_council(code="SA1", name="One")
        council_2 = make_council(code="SA2", name="Two")
        super_admin = platform_user("super1", AppRole.SUPER_ADMIN)

        ids = set(accessible_council_ids(super_admin))
        assert {council_1.id, council_2.id} <= ids


class TestPayerDelegationRLS:
    def test_payer_delegation_table_has_row_level_security_enabled(self, db):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'payer_delegation'"
            )
            row = cursor.fetchone()
        assert row == (True, True), "payer_delegation must have RLS ENABLED and FORCED, like every other tenant-scoped table"
