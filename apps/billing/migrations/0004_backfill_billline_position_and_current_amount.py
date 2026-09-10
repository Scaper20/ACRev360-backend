"""
Backfills existing BillLine rows for the two new fields added in 0003:
- position: insertion order was always implicit (pk order) before this field
  existed — this makes it explicit, ordered per bill by id.
- current_amount: the old system never itemized arrears onto lines (arrears
  lived only as Bill.arrears_amount), so every pre-existing line is entirely
  a current-cycle charge — current_amount = line_amount, arrears_amount stays 0.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Bill = apps.get_model("billing", "Bill")
    BillLine = apps.get_model("billing", "BillLine")
    for bill_id in Bill.objects.order_by("id").values_list("id", flat=True):
        lines = list(BillLine.objects.filter(bill_id=bill_id).order_by("id"))
        for position, line in enumerate(lines):
            line.position = position
            line.current_amount = line.line_amount
        BillLine.objects.bulk_update(lines, ["position", "current_amount"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_fifo_allocation_and_itemized_arrears"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
