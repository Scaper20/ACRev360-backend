import datetime
import os

import pytest
from django.db import transaction
from rest_framework.test import APIClient

from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.payments.models import POSTerminal
from apps.registry.models import Payer
from apps.registry.services import split_full_name
from apps.revenue.models import CouncilRevenueItem, RateBand, RateSchedule, RevenueCategory, RevenueItemTemplate
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, CouncilConfig, WardZone


_LOCAL_DB_HOSTS = {"", "localhost", "127.0.0.1", "::1", "db", "postgres"}


def pytest_sessionstart(session):
    """Refuse to run against a remote database. `.env` points DATABASE_URL at
    production Neon, and Django's test runner will happily create, migrate and
    fill a `test_<name>` database on whatever server that is (this happened on
    2026-09-24: a full run started against production, and killing it left a
    scratch database behind). Override DATABASE_URL to the local Postgres, or set
    ALLOW_REMOTE_TEST_DB=1 if you truly mean it."""
    from django.conf import settings

    host = (settings.DATABASES["default"].get("HOST") or "").lower()
    if host not in _LOCAL_DB_HOSTS and not os.environ.get("ALLOW_REMOTE_TEST_DB"):
        pytest.exit(
            f"Refusing to run tests against remote database host {host!r}. "
            "Set DATABASE_URL=postgresql://acrev360:acrev360@localhost:5432/acrev360 (or ALLOW_REMOTE_TEST_DB=1).",
            returncode=3,
        )


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The default cache is per-process and outlives a test's rolled-back
    transaction: a cached bill_ref->council map, dashboard payload or throttle
    counter from one test would otherwise leak into the next."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


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
        first_name, middle_name, last_name = split_full_name(name)
        return Payer.objects.create(
            council=council, payer_ref=f"C-{Payer.objects.filter(council=council).count() + 1:07d}",
            payer_type=Payer.BUSINESS, first_name=first_name, middle_name=middle_name, last_name=last_name,
            phone=phone, ward=ward, enumerated_by=actor,
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
