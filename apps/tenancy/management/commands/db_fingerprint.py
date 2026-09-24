"""
Snapshot a database's contents so two copies of it can be proven identical —
the "did the move lose anything?" check for a pg_dump/pg_restore between
Neon projects or regions (docs/DEPLOYMENT.md, "Moving to another region").

    python manage.py db_fingerprint --output before.json     # against the OLD database
    python manage.py db_fingerprint --compare before.json    # against the NEW one; exit 1 on any difference

What is compared: exact row count of every table, the highest id per table,
every sequence's position (a restore that misses one makes the next INSERT fail
with a duplicate key), which tables have row-level security enabled/forced and
how many policies each carries, the installed extensions, and the applied
migrations. ``--checksums`` also hashes every row's content — slower on big
tables, but it is the proof that no value changed rather than merely that no row
went missing.

Run it as the database *owner* (the role in DATABASE_URL). A role that RLS applies to sees zero rows in
every tenant table when no council context is set, which would make an empty
copy look identical to a full one — the command refuses to run as such a role
unless ``--allow-rls`` is passed.
"""
import json
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _fingerprint(*, checksums):
    q = connection.ops.quote_name
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        bypasses_rls = bool(cur.fetchone()[0])

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        tables = [row[0] for row in cur.fetchall()]

        cur.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'id'"
        )
        has_id = {row[0] for row in cur.fetchall()}

        table_info = {}
        for table in tables:
            cur.execute(f"SELECT count(*) FROM {q(table)}")
            info = {"rows": cur.fetchone()[0]}
            if table in has_id:
                cur.execute(f"SELECT max(id) FROM {q(table)}")
                info["max_id"] = cur.fetchone()[0]
            if checksums:
                cur.execute(
                    f"SELECT coalesce(md5(string_agg(h, '' ORDER BY h)), '') "
                    f"FROM (SELECT md5(t::text) AS h FROM {q(table)} t) x"
                )
                info["checksum"] = cur.fetchone()[0]
            table_info[table] = info

        cur.execute("SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1")
        sequences = {name: value for name, value in cur.fetchall()}

        cur.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND (c.relrowsecurity OR c.relforcerowsecurity) ORDER BY 1"
        )
        rls = {name: {"enabled": en, "forced": forced, "policies": n} for name, en, forced, n in cur.fetchall()}

        cur.execute("SELECT extname FROM pg_extension ORDER BY 1")
        extensions = [row[0] for row in cur.fetchall()]

        cur.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        migrations = [f"{app}.{name}" for app, name in cur.fetchall()]

    return {
        "bypasses_rls": bypasses_rls,
        "tables": table_info,
        "sequences": sequences,
        "rls": rls,
        "extensions": extensions,
        "migrations": migrations,
    }


def _diff(before, after):
    problems = []
    for table in sorted(set(before["tables"]) | set(after["tables"])):
        a, b = before["tables"].get(table), after["tables"].get(table)
        if a is None or b is None:
            problems.append(f"table {table}: present in {'old' if a else 'new'} only")
        elif a != b:
            problems.append(f"table {table}: old {a} != new {b}")
    for seq in sorted(set(before["sequences"]) | set(after["sequences"])):
        if before["sequences"].get(seq) != after["sequences"].get(seq):
            problems.append(f"sequence {seq}: old {before['sequences'].get(seq)} != new {after['sequences'].get(seq)}")
    if before["rls"] != after["rls"]:
        for table in sorted(set(before["rls"]) | set(after["rls"])):
            if before["rls"].get(table) != after["rls"].get(table):
                problems.append(f"row-level security on {table}: old {before['rls'].get(table)} != new {after['rls'].get(table)}")
    # pg_trgm etc. may legitimately differ by hosting; plpgsql is always there.
    missing_ext = sorted(set(before["extensions"]) - set(after["extensions"]))
    if missing_ext:
        problems.append(f"extensions missing in new database: {missing_ext}")
    if before["migrations"] != after["migrations"]:
        problems.append(
            "applied migrations differ: "
            f"only in old {sorted(set(before['migrations']) - set(after['migrations']))}, "
            f"only in new {sorted(set(after['migrations']) - set(before['migrations']))}"
        )
    return problems


class Command(BaseCommand):
    help = "Fingerprint this database (row counts, sequences, RLS, migrations) and optionally compare with a saved one."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Write the fingerprint to this JSON file.")
        parser.add_argument("--compare", help="Compare this database against a fingerprint saved earlier; exit 1 on any difference.")
        parser.add_argument("--checksums", action="store_true", help="Also hash every row's content (slower).")
        parser.add_argument("--allow-rls", action="store_true", help="Run even though this role is subject to row-level security.")

    def handle(self, *args, **opts):
        snapshot = _fingerprint(checksums=opts["checksums"])
        if not snapshot["bypasses_rls"] and not opts["allow_rls"]:
            raise CommandError(
                "This database role is subject to row-level security, so tenant tables would read as empty. "
                "Run as the owner role (DATABASE_URL / MIGRATE_DATABASE_URL), or pass --allow-rls if you know why."
            )

        total_rows = sum(t["rows"] for t in snapshot["tables"].values())
        self.stdout.write(f"{len(snapshot['tables'])} tables, {total_rows} rows, {len(snapshot['sequences'])} sequences, "
                          f"{len(snapshot['rls'])} row-level-secured tables, {len(snapshot['migrations'])} migrations applied")
        for table, info in snapshot["tables"].items():
            if info["rows"]:
                self.stdout.write(f"  {table:<40} {info['rows']:>9}")

        if opts["output"]:
            with open(opts["output"], "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, indent=2, sort_keys=True, default=str)
            self.stdout.write(self.style.SUCCESS(f"Saved fingerprint to {opts['output']}"))

        if opts["compare"]:
            with open(opts["compare"], encoding="utf-8") as fh:
                before = json.load(fh)
            if ("checksum" in next(iter(before["tables"].values()), {})) != opts["checksums"]:
                raise CommandError("The saved fingerprint and this run must both use --checksums, or neither.")
            problems = _diff(before, snapshot)
            if problems:
                for problem in problems:
                    self.stderr.write(self.style.ERROR(problem))
                self.stderr.write(self.style.ERROR(f"{len(problems)} difference(s) - do NOT cut over."))
                sys.exit(1)
            self.stdout.write(self.style.SUCCESS("Identical: every table, sequence, RLS setting and migration matches."))
