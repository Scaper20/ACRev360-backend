"""
Tenancy isolation, exercised directly against RLS (not just through the API) —
V2_ARCHITECTURE.md §10: "council A's user can never read or write council B's
rows... exercised against RLS directly."
"""
import pytest
from django.db import transaction

from apps.tenancy.context import council_context, set_council_context


@pytest.mark.django_db(transaction=True)
def test_ward_isolated_by_council(make_council, make_ward):
    c1 = make_council(code="AAA", name="Council A")
    c2 = make_council(code="BBB", name="Council B")

    with council_context(c1.id):
        make_ward(c1, code="W1", name="Council A Ward")
    with council_context(c2.id):
        make_ward(c2, code="W1", name="Council B Ward")

    from apps.tenancy.models import WardZone

    with council_context(c1.id):
        assert list(WardZone.objects.values_list("ward_name", flat=True)) == ["Council A Ward"]
    with council_context(c2.id):
        assert list(WardZone.objects.values_list("ward_name", flat=True)) == ["Council B Ward"]


@pytest.mark.django_db(transaction=True)
def test_naive_unscoped_query_still_isolated(make_council, make_ward):
    """A query with no council filter at all (the exact class of bug RLS exists to
    catch — see V2_ARCHITECTURE.md §3) must still only see the active council's rows."""
    c1 = make_council(code="CCC")
    c2 = make_council(code="DDD")

    with council_context(c1.id):
        make_ward(c1, code="X1", name="A")
        make_ward(c1, code="X2", name="B")
    with council_context(c2.id):
        make_ward(c2, code="X1", name="C")

    from apps.tenancy.models import WardZone

    with council_context(c1.id):
        assert WardZone.objects.count() == 2  # naive .all(), no council filter


@pytest.mark.django_db(transaction=True)
def test_no_context_sees_nothing(make_council, make_ward):
    c1 = make_council(code="EEE")
    with council_context(c1.id):
        make_ward(c1)

    from apps.tenancy.models import WardZone

    with transaction.atomic():
        set_council_context(None)
        assert WardZone.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_cross_council_write_blocked_by_rls(make_council, make_ward):
    """Inserting a row for council B while scoped to council A's context must fail
    the RLS WITH CHECK clause, not silently succeed. Postgres reports this as a
    ProgrammingError (InsufficientPrivilege), not an IntegrityError."""
    from django.db import ProgrammingError

    from apps.tenancy.models import WardZone

    c1 = make_council(code="FFF")
    c2 = make_council(code="GGG")

    with pytest.raises(ProgrammingError):
        with council_context(c1.id):
            WardZone.objects.create(council=c2, ward_code="X", ward_name="Should be rejected")
