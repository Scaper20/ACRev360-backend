"""
Best-effort split of existing Payer.full_name via apps.registry.services.
split_full_name — the one place this tokenization rule lives, also used by
every runtime call site that turns a full-name string into these fields
(consultant-as-payer registration, demo/seed data). Payer.full_name (now a
property) reconstructs the original string correctly either way.

Payer is RLS-protected — see apps.tenancy.migration_helpers.for_each_council's
docstring for why this can't be a bare Payer.objects.all() (an earlier version
of this migration was exactly that, and silently updated zero rows in any
environment with existing data).
"""
from django.db import migrations

from apps.registry.services import split_full_name
from apps.tenancy.migration_helpers import for_each_council


def backfill(apps, schema_editor):
    Payer = apps.get_model("registry", "Payer")

    def backfill_council(council):
        payers = list(Payer.objects.filter(council_id=council.id))
        for payer in payers:
            if not payer.full_name:
                continue
            payer.first_name, payer.middle_name, payer.last_name = split_full_name(payer.full_name)
        Payer.objects.bulk_update(payers, ["first_name", "middle_name", "last_name"])

    for_each_council(apps, backfill_council)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0006_payer_name_fields"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
