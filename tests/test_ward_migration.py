"""
PR12 — seeding the real 80 Kuje Area Council areas
(apps/tenancy/migrations/0004_seed_real_kac_wards.py). Same testing
convention as tests/test_migration_helpers.py: a source-inspection guard
that the migration routes through for_each_council (not a bare queryset —
see that module's docstring for why this matters), plus functional tests
against the migration's own real function, invoked directly with the
current app registry (django.apps.apps) the same way
test_for_each_council_correctly_reaches_every_councils_rows does.
"""
import importlib
import inspect

import django.apps
import pytest
from django.db import transaction

from apps.tenancy.context import set_council_context
from apps.tenancy.models import WardZone

_migration = importlib.import_module("apps.tenancy.migrations.0004_seed_real_kac_wards")


def test_migration_uses_for_each_council():
    assert "for_each_council" in inspect.getsource(_migration.seed_real_kac_wards)


def _seed_old_placeholder_wards(council):
    """The exact 9-row set this migration is replacing — see the PR."""
    old_names = ["Chibiri", "Gaube", "Gudun-Karya", "Ivo", "Kabi", "Kuje", "Kwaku", "Rubochi", "Yenche"]
    for name in old_names:
        WardZone.objects.create(
            council=council, ward_code=_migration._ward_code(name), ward_name=name, zone_type="WARD",
        )


@pytest.mark.django_db(transaction=True)
class TestSeedRealKacWards:
    def test_kac_ends_up_with_all_80_real_areas(self, make_council):
        council = make_council(code="KAC", name="Kuje Area Council")
        with transaction.atomic():
            set_council_context(council.id)
            _seed_old_placeholder_wards(council)

        with transaction.atomic():
            set_council_context(council.id)
            _migration._migrate_kac_wards(django.apps.apps, council)

        with transaction.atomic():
            set_council_context(council.id)
            codes = set(WardZone.objects.filter(council=council).values_list("ward_code", flat=True))
        assert len(codes) == 80
        assert codes == {_migration._ward_code(name) for name in _migration.REAL_KAC_AREAS}

    def test_already_correct_rows_keep_their_existing_id(self, make_council):
        council = make_council(code="KAC", name="Kuje Area Council")
        with transaction.atomic():
            set_council_context(council.id)
            _seed_old_placeholder_wards(council)
            kuje_id_before = WardZone.objects.get(council=council, ward_code="KUJE").id

        with transaction.atomic():
            set_council_context(council.id)
            _migration._migrate_kac_wards(django.apps.apps, council)

        with transaction.atomic():
            set_council_context(council.id)
            kuje = WardZone.objects.get(council=council, ward_code="KUJE")
        assert kuje.id == kuje_id_before

    def test_gudun_karya_hyphen_normalized_to_space_same_id(self, make_council):
        council = make_council(code="KAC", name="Kuje Area Council")
        with transaction.atomic():
            set_council_context(council.id)
            _seed_old_placeholder_wards(council)
            hyphen_id = WardZone.objects.get(council=council, ward_code="GUDUN_KARYA").id

        with transaction.atomic():
            set_council_context(council.id)
            _migration._migrate_kac_wards(django.apps.apps, council)

        with transaction.atomic():
            set_council_context(council.id)
            renamed = WardZone.objects.get(council=council, ward_code="GUDUN_KARYA")
        assert renamed.id == hyphen_id
        assert renamed.ward_name == "Gudun Karya"

    def test_ivo_is_removed_when_nothing_references_it(self, make_council):
        council = make_council(code="KAC", name="Kuje Area Council")
        with transaction.atomic():
            set_council_context(council.id)
            _seed_old_placeholder_wards(council)

        with transaction.atomic():
            set_council_context(council.id)
            _migration._migrate_kac_wards(django.apps.apps, council)

        with transaction.atomic():
            set_council_context(council.id)
            assert not WardZone.objects.filter(council=council, ward_code="IVO").exists()

    def test_ivo_referenced_by_a_payer_fails_loudly_instead_of_deleting(self, make_council, make_user, make_payer):
        """The general-case safety net the PR explicitly asks for: this being
        clean on production today doesn't mean every environment is."""
        council = make_council(code="KAC", name="Kuje Area Council")
        with transaction.atomic():
            set_council_context(council.id)
            _seed_old_placeholder_wards(council)
            admin = make_user(council, username="kac-admin")
            ivo = WardZone.objects.get(council=council, ward_code="IVO")
            make_payer(council, ivo, admin, name="Still On Ivo")

        with pytest.raises(RuntimeError, match="IVO"):
            with transaction.atomic():
                set_council_context(council.id)
                _migration._migrate_kac_wards(django.apps.apps, council)

        # Nothing was lost: Ivo is still there, still referenced.
        with transaction.atomic():
            set_council_context(council.id)
            assert WardZone.objects.filter(council=council, ward_code="IVO").exists()

    def test_non_kac_council_is_left_untouched(self, make_council, make_ward):
        council = make_council(code="OTH", name="Some Other Council")
        with transaction.atomic():
            set_council_context(council.id)
            make_ward(council, code="ONLYWARD", name="Only Ward")

        with transaction.atomic():
            set_council_context(council.id)
            _migration._migrate_kac_wards(django.apps.apps, council)

        with transaction.atomic():
            set_council_context(council.id)
            codes = set(WardZone.objects.filter(council=council).values_list("ward_code", flat=True))
        assert codes == {"ONLYWARD"}
