#!/bin/sh
set -e

# Migrations need DDL rights, which the runtime role deliberately lacks: the
# app must connect as a NOBYPASSRLS role with DML-only privileges so Postgres
# row-level security actually applies (Neon's default owner role bypasses it —
# see docs/DEPLOYMENT.md, "Enforce row-level security"). When
# MIGRATE_DATABASE_URL is set (the owner role), migrate uses it; when unset,
# migrate runs on DATABASE_URL exactly as before.
DATABASE_URL="${MIGRATE_DATABASE_URL:-$DATABASE_URL}" python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
