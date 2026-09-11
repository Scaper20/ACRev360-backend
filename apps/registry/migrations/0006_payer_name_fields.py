from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0005_payer_assigned_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="payer",
            name="first_name",
            field=models.CharField(max_length=100, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="payer",
            name="middle_name",
            field=models.CharField(max_length=100, blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="payer",
            name="last_name",
            field=models.CharField(max_length=100, blank=True, default=""),
            preserve_default=False,
        ),
    ]
