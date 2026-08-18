-- Creates the non-superuser role the app connects as at runtime.
--
-- The official postgres image makes POSTGRES_USER (here: "acrev360") the
-- cluster's bootstrap superuser, which unconditionally bypasses Row-Level
-- Security -- including FORCE ROW LEVEL SECURITY (see apps/common/db.py).
-- "acrev360" stays as the table owner for migrations; "appuser" is a plain
-- role the RLS policies actually apply to, and is what DATABASE_URL points
-- web/celery-worker/celery-beat at.
--
-- NOTE: docker-entrypoint-initdb.d scripts only run against a *fresh* data
-- directory (first cluster init). They will not retroactively apply to an
-- existing postgres_data volume -- run this by hand against one of those
-- (docker compose exec postgres psql -U acrev360 -d acrev360 -f ...).
-- CREATEDB is needed because pytest-django creates/drops a throwaway
-- "test_acrev360" database per run using this same role -- it doesn't grant
-- superuser or BYPASSRLS, so it doesn't weaken the RLS fix this role exists
-- for.
CREATE ROLE appuser WITH LOGIN PASSWORD 'appuser' NOSUPERUSER CREATEDB NOCREATEROLE;
GRANT ALL PRIVILEGES ON DATABASE acrev360 TO appuser;
-- Postgres 15+ no longer grants CREATE on the public schema to non-owners
-- by default -- without this, `manage.py migrate` (which runs as appuser,
-- via web's docker-entrypoint.sh) can't create django_migrations or any
-- app table.
GRANT ALL PRIVILEGES ON SCHEMA public TO appuser;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO appuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO appuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO appuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO appuser;
