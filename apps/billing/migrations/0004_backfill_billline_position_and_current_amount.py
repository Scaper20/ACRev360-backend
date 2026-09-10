"""
Backfills existing BillLine rows for the two new fields added in 0003:
- position: insertion order was always implicit (pk order) before this field
  existed — this makes it explicit, ordered per bill by id.
- current_amount: the old system never itemized arrears onto lines (arrears
  lived only as Bill.arrears_amount), so every pre-existing line is entirely
  a current-cycle charge — current_amount = line_amount, arrears_amount stays 0.

Bill is RLS-protected (BillLine itself isn't, but is only ever reached here
via Bill) — see apps.tenancy.migration_helpers.for_each_council's docstring
for why this can't be a bare Bill.objects.order_by("id") (an earlier version
of this migration was exactly that, and silently updated zero rows in any
environment with existing data).
"""
from django.db import migrations

from apps.tenancy.migration_helpers import for_each_council


def backfill(apps, schema_editor):
    Bill = apps.get_model("billing", "Bill")
    BillLine = apps.get_model("billing", "BillLine")

    def backfill_council(council):
        for bill_id in Bill.objects.filter(council_id=council.id).order_by("id").values_list("id", flat=True):
            lines = list(BillLine.objects.filter(bill_id=bill_id).order_by("id"))
            for position, line in enumerate(lines):
                line.position = position
                line.current_amount = line.line_amount
            BillLine.objects.bulk_update(lines, ["position", "current_amount"])

    for_each_council(apps, backfill_council)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_fifo_allocation_and_itemized_arrears"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
