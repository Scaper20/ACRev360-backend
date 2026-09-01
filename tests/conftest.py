import datetime

import pytest
from django.db import transaction
from rest_framework.test import APIClient

from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.payments.models import POSTerminal
from apps.registry.models import Payer
from apps.revenue.models import CouncilRevenueItem, RateBand, RateSchedule, RevenueCategory, RevenueItemTemplate
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, CouncilConfig, WardZone


@pytest.fixture
def make_council(db):
    def _make(code="TST", name="Test Council"):
        # CouncilConfig is itself RLS-protected — a brand new council has no
        # context yet, so bootstrap it the same way apps.tenancy.services.
        # onboard_council does: set the context to the row's own id as soon as
        # it exists, in the same transaction.
        with transaction.atomic():
            council = Council.objects.create(council_code=code, council_name=name)
            set_council_context(council.id)
            CouncilConfig.objects.create(council=council, bill_ref_prefix=code, bill_due_days=30)
        return council

    return _make


@pytest.fixture
def make_ward(db):
    def _make(council, code="W1", name="Ward One"):
        return WardZone.objects.create(council=council, ward_code=code, ward_name=name)

    return _make


@pytest.fixture
def make_revenue_item(db):
    def _make(council, code="ITEM1", name="Test Item", rate=10000):
        category, _ = RevenueCategory.objects.get_or_create(name="Fees and Charges")
        template, _ = RevenueItemTemplate.objects.get_or_create(
            harmonised_code=code, defaults={"item_name": name, "unit_of_charge": "Per Annum", "category": category}
        )
        item = CouncilRevenueItem.objects.create(
            council=council, template=template, harmonised_code=code, item_name=name,
            category=category, unit_of_charge="Per Annum",
        )
        RateSchedule.objects.create(council_revenue_item=item, rate_amount=rate, effective_from=datetime.date.today())
        return item

    return _make


@pytest.fixture
def make_role(db):
    def _make(name="COUNCIL_ADMIN", access_level=AppRole.COUNCIL_ADMIN):
        role, _ = AppRole.objects.get_or_create(name=name, defaults={"access_level": access_level})
        return role

    return _make


@pytest.fixture
def make_user(db, make_role):
    def _make(council, username="admin1", access_level=AppRole.COUNCIL_ADMIN, password="testpass12345", consultant=None):
        role = make_role(name=f"{access_level}_{council.council_code}", access_level=access_level)
        return AppUser.objects.create_user(
            username=username, password=password, full_name="Test User", council=council, role=role, consultant=consultant,
        )

    return _make


@pytest.fixture
def make_payer(db):
    def _make(council, ward, actor, name="Test Payer", phone="08010000000"):
        return Payer.objects.create(
            council=council, payer_ref=f"C-{Payer.objects.filter(council=council).count() + 1:07d}",
            payer_type=Payer.BUSINESS, full_name=name, phone=phone, ward=ward, enumerated_by=actor,
        )

    return _make


@pytest.fixture
def make_registration_item(db, make_revenue_item):
    """Seeds the 'Contractors — Consultancy' item/band SubConsultantViewSet.
    perform_create bills consultant registration against — see
    CONSULTANT_REGISTRATION_ITEM_CODE/_BAND_LABEL in apps.accounts.api.views."""

    def _make(council, rate=120000):
        item = make_revenue_item(council, code="30010048", name="Contractors", rate=rate)
        RateBand.objects.create(
            council_revenue_item=item, label="Consultancy", rate_mode=RateBand.FLAT,
            flat_amount=rate, effective_from=datetime.date.today(),
        )
        return item

    return _make


@pytest.fixture
def make_consultant(db):
    def _make(council, name="Test Consultant", contract_ref="CR-1", rate=30, status=SubConsultant.ACTIVE):
        return SubConsultant.objects.create(
            council=council, consultant_name=name, contract_ref=contract_ref,
            commission_rate=rate, status=status,
        )

    return _make


@pytest.fixture
def make_field_agent(db):
    def _make(council, user, ward=None, agent_code="AGT-1", status=FieldAgent.ACTIVE):
        return FieldAgent.objects.create(
            council=council, user=user, agent_code=agent_code, assigned_ward=ward, status=status,
        )

    return _make


@pytest.fixture
def make_terminal(db):
    def _make(council, agent, ward, terminal_id="TERM-1", bank_terminal_id="", status=POSTerminal.ACTIVE):
        return POSTerminal.objects.create(
            council=council, terminal_id=terminal_id, bank_terminal_id=bank_terminal_id,
            agent=agent, ward=ward, status=status,
        )

    return _make


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def authed_api_client(api_client):
    def _make(user):
        from apps.accounts.tokens import AppTokenObtainPairSerializer

        token = AppTokenObtainPairSerializer.get_token(user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        return api_client

    return _make
