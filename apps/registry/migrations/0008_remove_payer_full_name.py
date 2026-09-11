from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0007_backfill_payer_names"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="payer",
            name="payer_council_c13bae_idx",
        ),
        migrations.RemoveField(
            model_name="payer",
            name="full_name",
        ),
        migrations.AddIndex(
            model_name="payer",
            index=models.Index(fields=["council", "last_name", "first_name"], name="payer_council_ed0f1d_idx"),
        ),
    ]
