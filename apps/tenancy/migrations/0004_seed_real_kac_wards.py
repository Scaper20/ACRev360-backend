# PR12 — seed the real 80 Kuje Area Council areas (WardZone), replacing the
# 9-row placeholder set (see docs/CHANGELOG.md).
#
# RunPython against WardZone (RLS-protected — CouncilScopedModel) must go
# through apps.tenancy.migration_helpers.for_each_council, never a bare
# queryset — see that module's own docstring for why (this shipped as a real
# production incident once already, 2026-09-10).
#
# Only KAC gets the real-area seed here — every other council keeps whatever
# wards it already has. This is Kuje-specific reference data, not a uniform
# transform every tenant should receive.
from django.db import migrations
from django.db.models.deletion import ProtectedError

from apps.tenancy.migration_helpers import for_each_council

# The 80 real Kuje Area Council areas (alphabetical). Kept in sync by hand
# with apps/tenancy/management/commands/seed_kuje.py's own WARDS list — a
# migration must stay a frozen, self-contained snapshot (it can't safely
# import a list that might change under it later), so this is a deliberate
# duplication, not an oversight. "Gudun Karya" (space) is the canonical
# spelling from here on; the one pre-existing row using a hyphen
# ("Gudun-Karya") gets its ward_name normalized to match below.
REAL_KAC_AREAS = [
    "Achimbi", "Adegba Tashara", "Affa", "Agwai", "Ahinza", "Anguwar Madaki",
    "Attako", "Bamishe", "Bida", "Buga", "Bugako", "Buzunkure", "Chibiri",
    "Chida", "Chukuku", "Dafara", "Darka", "Dibe Padama", "Dnago", "Duda",
    "Gadoro", "Gafere", "Gashe", "Gaube", "Gawu", "Gawu Kurmi", "Gidan Bawa",
    "Gidigwai", "Gombe", "Gudun Karya", "Gumayi", "Gurufufu",
    "Gwagwada Kpana", "Gwargwada", "Gwari-Yamma", "Gwaupe", "Huni Gade",
    "Huni Gwari", "Jeida", "Kabi", "Kabi Kassa", "Kahoda Hannu", "Kanzo",
    "Kasada", "Kashimoro", "Kayarda", "Kiyi", "Kuje", "Kujekwa", "Kulo",
    "Kusaki", "Kutada", "Kutumbwa", "Kwaku", "Lanto", "Munu", "Paggi",
    "Passali", "Rubochi", "Rubokya", "Sabe", "Sauka", "Shaji", "Shetuko",
    "Sungba", "Takwa", "Tika", "Toto Gabiya", "Tude", "Tukpeki", "Tunbwa",
    "Ukya", "Wumi", "Yaba", "Yanga", "Yenche", "Yewu", "Zagabutu",
    "Zango-Kara", "Zokutu",
]


def _ward_code(name):
    return name.upper().replace(" ", "_").replace("-", "_")


def _migrate_kac_wards(apps, council):
    if council.council_code != "KAC":
        return

    WardZone = apps.get_model("tenancy", "WardZone")
    real_codes = {_ward_code(name) for name in REAL_KAC_AREAS}
    existing_by_code = {w.ward_code: w for w in WardZone.objects.filter(council=council)}

    # Normalize any pre-existing row whose stored ward_name doesn't match the
    # real list's spelling exactly (currently just "Gudun-Karya" ->
    # "Gudun Karya") — same id, same ward_code either way (hyphens and
    # spaces both normalize to underscore), so nothing already pointing at
    # this row via FK needs remapping, only the display name changes.
    for ward in existing_by_code.values():
        canonical = next((name for name in REAL_KAC_AREAS if _ward_code(name) == ward.ward_code), None)
        if canonical and ward.ward_name != canonical:
            ward.ward_name = canonical
            ward.save(update_fields=["ward_name"])

    # Add every real area not already present, preserving the id of any row
    # that already matches (acceptance: the 8 already-correct rows keep
    # their existing ids).
    for name in REAL_KAC_AREAS:
        code = _ward_code(name)
        if code not in existing_by_code:
            WardZone.objects.create(council=council, ward_code=code, ward_name=name, zone_type="WARD")

    # Retire any ward that ISN'T a real area (currently just "Ivo", with zero
    # payers/agents/terminals/etc. assigned to it on production — but this
    # must handle the general case, not assume that's true everywhere).
    # Every FK to WardZone uses on_delete=PROTECT, so Django itself refuses
    # the delete and raises ProtectedError if anything still references this
    # row; the try/except below only turns that into a clearer, actionable
    # error message instead of a bare traceback — it doesn't weaken the
    # protection.
    for ward in list(existing_by_code.values()):
        if ward.ward_code in real_codes:
            continue
        try:
            ward.delete()
        except ProtectedError as exc:
            referencing = ", ".join(sorted({obj._meta.label for obj in exc.protected_objects}))
            raise RuntimeError(
                f"WardZone {ward.ward_code!r} ({ward.ward_name!r}) on {council.council_code} isn't a real "
                f"Kuje area but is still referenced by: {referencing}. Reassign or clear those references "
                "before this migration can retire it — see PR12 in docs/CHANGELOG.md."
            ) from exc


def seed_real_kac_wards(apps, schema_editor):
    for_each_council(apps, lambda council: _migrate_kac_wards(apps, council))


def noop_reverse(apps, schema_editor):
    """Deliberately irreversible in any meaningful sense — undoing this would
    mean re-creating the retired placeholder ward and reverting the name
    normalization, neither of which anyone actually wants. A no-op keeps
    `migrate tenancy <previous>` usable without pretending there's a real
    rollback path for reference-data content like this."""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0003_department_legal_basis"),
    ]

    operations = [
        migrations.RunPython(seed_real_kac_wards, noop_reverse),
    ]
