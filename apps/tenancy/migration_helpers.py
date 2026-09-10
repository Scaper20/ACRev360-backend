"""
Helper for writing data migrations (RunPython) that touch an RLS-protected
model — i.e. any CouncilScopedModel subclass. See apps/tenancy/context.py for
what the RLS policy actually checks.

**Read this before writing a RunPython operation that queries or updates any
table listed under "ALTER TABLE ... ENABLE ROW LEVEL SECURITY" in this repo's
migrations (grep for it to get the current list — it's most of the app's
tables).** A bare `SomeModel.objects.all()` (or .filter(), .update(),
bulk_update(), etc.) inside a migration sees ZERO rows across the whole
table, silently, if `app.council_id` isn't set first — the policy's USING
clause is `council_id = NULLIF(current_setting('app.council_id', true), '')
::integer`, and an unset setting means NULL, and `council_id = NULL` is never
true in SQL. Migrations run as the same non-superuser role RLS applies to, so
this isn't hypothetical — it silently no-ops. Worse, the migration reports
success (0 rows matched isn't an error), so nothing fails loudly; the bug
only surfaces later, when someone notices the data was never actually
touched. This exact failure mode shipped once already (2026-09-10, PR4/PR9's
backfill migrations) and cost the original data on the registry side, since
the very next migration in the same deploy dropped the column the backfill
was supposed to have already read from.

Use `for_each_council` for any RunPython that needs to read or write
RLS-protected rows across (potentially) multiple councils — it's the
correct, easy-to-get-right replacement for a bare queryset.
"""
from django.db import connection


def for_each_council(apps, fn) -> None:
    """Calls `fn(council)` once per Council row, with `app.council_id` set to
    that council's id for the duration of the call — `council` is the
    *historical* Council model instance (from `apps.get_model`), matching
    every other historical model a migration works with. Safe to call
    `Council.objects.all()` itself without this wrapper first: `council`
    (the tenant root) has no RLS policy of its own.

    Must run inside the migration's own transaction (Migration.atomic=True,
    Django's default) — SET LOCAL is transaction-scoped, and this relies on
    that transaction staying open for the whole loop, not committing between
    councils.
    """
    Council = apps.get_model("tenancy", "Council")
    with connection.cursor() as cursor:
        for council in Council.objects.order_by("id"):
            cursor.execute("SET LOCAL app.council_id = %s;", [str(council.id)])
            fn(council)
