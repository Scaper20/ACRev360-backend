"""
Best-effort split of existing Payer.full_name on whitespace: first token ->
first_name, last token -> last_name, everything in between -> middle_name.
A single-token name (common for registered businesses, and most GOVERNMENT/
NGO payers) goes entirely into first_name, with middle/last left blank —
Payer.full_name (now a property) reconstructs it correctly either way.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Payer = apps.get_model("registry", "Payer")
    payers = list(Payer.objects.all())
    for payer in payers:
        tokens = payer.full_name.split()
        if not tokens:
            continue
        elif len(tokens) == 1:
            payer.first_name = tokens[0]
        else:
            payer.first_name = tokens[0]
            payer.last_name = tokens[-1]
            payer.middle_name = " ".join(tokens[1:-1])
    Payer.objects.bulk_update(payers, ["first_name", "middle_name", "last_name"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0006_payer_name_fields"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
