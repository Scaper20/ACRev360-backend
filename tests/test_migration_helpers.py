"""
Regression coverage for the RLS-blocked-data-migration incident (2026-09-10):
apps.registry.migrations.0007_backfill_payer_names and apps.billing.migrations.
0004_backfill_billline_position_and_current_amount both originally ran a bare
queryset against an RLS-protected table with no council context set, silently
matched zero rows, and reported success. Production data was affected before
anyone noticed — see docs/CHANGELOG.md's 2026-09-10 entry.

Why pytest never caught this: the test database is migrated (schema + these
RunPython operations) while completely empty, then test fixtures create data
*afterward*, always via set_council_context()/council_context() themselves.
A migration's data-transform step never runs against real pre-existing data
in that flow, so a broken migration and a correct one look identical to the
existing test suite. These tests close that gap by calling the helper (and,
via it, the exact code path a migration uses) directly against real
pre-existing data with no ambient council context set — the actual condition
under which the original bug shipped.
"""
import django.apps
import pytest
from django.db import transaction

from apps.registry.models import Payer
from apps.tenancy.context import set_council_context
from apps.tenancy.migration_helpers import for_each_council


@pytest.fixture
def two_councils_with_a_payer_each(make_council, make_ward, make_user):
    council_a = make_council(code="MHA")
    with transaction.atomic():
        set_council_context(council_a.id)
        ward_a = make_ward(council_a)
        user_a = make_user(council_a, username="mh-admin-a")
        Payer.objects.create(
            council=council_a, payer_ref="C-MHA1", payer_type=Payer.BUSINESS,
            first_name="Alpha", ward=ward_a, enumerated_by=user_a,
        )

    council_b = make_council(code="MHB")
    with transaction.atomic():
        set_council_context(council_b.id)
        ward_b = make_ward(council_b)
        user_b = make_user(council_b, username="mh-admin-b")
        Payer.objects.create(
            council=council_b, payer_ref="C-MHB1", payer_type=Payer.BUSINESS,
            first_name="Bravo", ward=ward_b, enumerated_by=user_b,
        )

    return council_a, council_b


@pytest.mark.django_db(transaction=True)
def test_bare_query_with_no_council_context_sees_nothing_the_original_bug(two_councils_with_a_payer_each):
    """Proves the trap is real, not hypothetical: two real Payer rows exist,
    and a plain query run the way a RunPython migration runs it — with no
    app.council_id ever set — sees zero of them. This is exactly what let
    the original backfill migrations report success while touching nothing."""
    assert Payer.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_for_each_council_correctly_reaches_every_councils_rows(two_councils_with_a_payer_each):
    council_a, council_b = two_councils_with_a_payer_each
    seen_counts = {}

    def visit(council):
        # A bare, unfiltered query — the exact shape the broken migrations
        # used — must now correctly see only this council's own row, proving
        # for_each_council's SET LOCAL actually took effect for the query
        # inside the callback, not just for a query made outside it.
        seen_counts[council.id] = Payer.objects.count()

    with transaction.atomic():
        for_each_council(django.apps.apps, visit)

    assert seen_counts == {council_a.id: 1, council_b.id: 1}


@pytest.mark.django_db(transaction=True)
def test_for_each_council_never_leaks_one_councils_rows_into_another(two_councils_with_a_payer_each):
    council_a, council_b = two_councils_with_a_payer_each
    seen_refs = {}

    def visit(council):
        seen_refs[council.id] = set(Payer.objects.values_list("payer_ref", flat=True))

    with transaction.atomic():
        for_each_council(django.apps.apps, visit)

    assert seen_refs[council_a.id] == {"C-MHA1"}
    assert seen_refs[council_b.id] == {"C-MHB1"}


def test_registry_backfill_migration_uses_for_each_council():
    """A cheaper guard against the exact regression than re-deriving the full
    migration-executor machinery: the fixed migration must route through the
    helper, not a bare queryset, so this fails loudly if someone "simplifies"
    it back to the broken shape later."""
    import importlib
    import inspect

    module = importlib.import_module("apps.registry.migrations.0007_backfill_payer_names")
    assert "for_each_council" in inspect.getsource(module.backfill)


def test_billing_backfill_migration_uses_for_each_council():
    import importlib
    import inspect

    module = importlib.import_module("apps.billing.migrations.0004_backfill_billline_position_and_current_amount")
    assert "for_each_council" in inspect.getsource(module.backfill)
