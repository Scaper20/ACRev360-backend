"""
PR8 (email login): backfills every AppUser with a blank email before the
next migration adds a unique constraint. Same placeholder scheme as
AppUserManager._create_user going forward — username is already globally
unique, so username@placeholder.acrev360.local always is too. Real emails
get collected as a follow-up; this only needs to make the migration to a
unique, required email column possible without locking anyone out.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    AppUser = apps.get_model("accounts", "AppUser")
    users = list(AppUser.objects.filter(email=""))
    for user in users:
        user.email = f"{user.username}@placeholder.acrev360.local"
    AppUser.objects.bulk_update(users, ["email"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_fieldagent_id_hash_fieldagent_id_type_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
