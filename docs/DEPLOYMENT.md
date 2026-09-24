# Deploying — backend to Render, frontend to Vercel

Both apps deploy from their own git repo (as already separated). Order matters:
deploy the backend first (you need its URL for the frontend's env var), then the
frontend (you need *its* URL for the backend's CORS setting), then go back and
finish the backend's CORS value.

This guide targets Render's **free tier** and default `*.onrender.com` /
`*.vercel.app` subdomains — no custom domain or paid plan required to get live.
Both are easy to upgrade later without re-architecting anything below.

---

## 0. What's already in the repo for this

- **`render.yaml`** — a Render "Blueprint": one command deploys the Docker web
  service. Postgres itself is **not** a Render resource — it's hosted on
  [Neon](https://neon.tech) (free tier) instead, because Render's own free
  Postgres plan auto-expires after 30 days (this bit us once already — see
  `docs/CHANGELOG.md`). Neon's free tier doesn't expire; it just suspends
  compute after ~5 minutes idle and wakes on the next query (a second or two
  of extra latency on the first request after a quiet spell, same idea as
  Render's own free-tier spin-down below).
- **`Dockerfile`** — already the one verified working locally (§ GETTING_STARTED.md);
  now also binds to Render's `$PORT` instead of a hardcoded 8000.
- **No Redis / Celery worker / Render Cron Job in the free deploy.** The only
  scheduled job today is the daily debt-ageing refresh
  (`apps/enforcement/tasks.py::refresh_all_councils_debt`), and nothing in the
  codebase calls `.delay()`/`.apply_async()` yet — so a standing Celery worker +
  beat + Redis broker isn't earning its cost yet. Render Cron Jobs aren't free
  either way (minimum $1/mo per job, no free instance type), so instead
  **`.github/workflows/debt-ageing-refresh.yml`** calls the same
  `POST /api/v1/debt/refresh` endpoint the frontend's "Refresh Ageing" button
  uses, on a daily GitHub Actions schedule — genuinely free, no extra Render
  service. When real async work shows up (e.g. webhook post-processing), add a
  Render Key Value instance + a `worker` service running
  `celery -A config worker --beat`, matching `docker-compose.yml`'s
  `celery-worker`/`celery-beat` services, and retire the workflow.
- **Free web services have no Shell/SSH access** (Render restriction, not a
  bug) — step 1.5 below uses the database's external connection string from
  your own machine instead.

---

## 1. Database → Neon

1. Sign up / log in at [neon.tech](https://neon.tech) (GitHub login is
   fastest). Free tier: 0.5GB storage, autosuspends after idle — no 30-day
   expiry like Render's free Postgres.
2. **New Project** → name it (e.g. `acrev360`), pick a region close to your
   Render web service's region (US or EU — match whatever you pick for the
   web service below), Postgres version 17 to match what `render.yaml`
   previously pinned. Neon creates a default database and role for you.
3. Project dashboard → **Connection Details** → copy the **pooled**
   connection string (the one with `-pooler` in the hostname — use this one,
   not the direct/unpooled string, since Render's web service holds
   persistent connections across requests). It looks like:
   ```
   postgres://<user>:<password>@ep-xxxx-pooler.<region>.aws.neon.tech/<dbname>?sslmode=require
   ```
   Keep this tab open — you'll paste it into Render in step 2 below and use
   it directly from your own machine in step 4.

## 2. Backend → Render

1. Push this repo to GitHub/GitLab if it isn't already remote (it's currently
   only a local git repo — `git remote add origin <url>` then `git push -u origin master`).
2. In the Render dashboard: **New → Blueprint**, pick this repo. Render reads
   `render.yaml` and shows you the `acrev360-backend` web service it's about
   to create (no database — that's Neon now).
3. Render will prompt for the env vars marked `sync: false` before the first
   deploy — you need three values ready:
   - **`DATABASE_URL`** — the Neon pooled connection string from step 1.3.
   - **`WEBHOOK_ENCRYPTION_KEY`** — generate one:
     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
     Losing/rotating this key makes existing encrypted webhook secrets
     unreadable, so store it somewhere durable (password manager), not just in
     Render's dashboard.
   - **`CORS_ALLOWED_ORIGINS`** — you don't have the Vercel URL yet. Put in a
     placeholder for now (e.g. `https://placeholder.vercel.app`); you'll fix
     this in step 3 below once the frontend is deployed.
4. Click **Apply**. First deploy takes a few minutes (Docker build + `migrate`
   + `collectstatic` run automatically via `docker-entrypoint.sh`) and runs
   migrations directly against the Neon database you just wired up.
5. **Seed the database.** Free web services don't get Shell/SSH access on
   Render, so run the seed command from your own machine instead, pointed at
   Neon directly — no need to go through Render for this at all:
   - From `E:\ACRev360-backend`, with your local venv active, run `seed_kuje`
     against the Neon URL without touching your local `.env` (PowerShell shown;
     bash is the same with `export` instead of `$env:`):
     ```powershell
     $env:DATABASE_URL   = "<the Neon pooled connection string from step 1>"
     $env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
     $env:DJANGO_ALLOWED_HOSTS   = "acrev360-backend.onrender.com"
     $env:CORS_ALLOWED_ORIGINS   = "https://placeholder.vercel.app"
     $env:DJANGO_SECRET_KEY      = "not-used-by-this-command-any-value-works"
     python manage.py seed_kuje --admin-password <pick-a-real-password-this-time>
     ```
     (`seed_kuje` is guarded — it checks whether Kuje is already seeded and
     exits harmlessly if so, so re-running this by accident is safe.)
   - Close that shell/unset those vars afterward so you don't accidentally run
     something else against the production database.
6. Confirm it's actually up (the deployed hostname may not be the plain
   `acrev360-backend.onrender.com` — the plain subdomain can already be
   claimed by an older service in your Render workspace, in which case
   Render assigns a random suffix instead, e.g. `acrev360-backend-wxu8.onrender.com`;
   check the service's page in the Render dashboard for its actual URL):
   - `<your-service-url>/api/v1/health` → `{"status": "ok"}`
   - `<your-service-url>/api/docs/` loads Swagger UI
   - **Free-tier note:** the web service spins down after 15 minutes idle: the
     first request after a quiet spell takes ~30–60s while it wakes up.
7. **Wire up the daily debt-ageing refresh** (GitHub Actions, since Render
   Cron Jobs cost $1/mo minimum even on the "free" plan — see §0): the
   workflow (`.github/workflows/debt-ageing-refresh.yml`) runs
   `manage.py refresh_debt_ageing` directly against the database — not
   through the deployed API — specifically so this job doesn't depend on the
   Render web service being awake or even up at all (that dependency chain
   broke more than once: free Postgres expiry, idle spin-down, an
   unexplained manual suspension — see `docs/CHANGELOG.md`). In the backend
   repo's GitHub settings → **Settings → Secrets and variables → Actions**,
   add one repository secret:
   ```
   ACREV_DATABASE_URL = <the same Neon pooled connection string used for DATABASE_URL>
   ```
   It then runs daily automatically; you can also trigger it manually from
   the repo's **Actions** tab (**Run workflow**) to confirm it works right
   away instead of waiting a day.

## 3. Frontend → Vercel

1. Push `E:\ACRev360-frontend` to its own GitHub/GitLab repo, separate from the
   backend's (already separate directories/git histories — keep that).
2. In Vercel: **Add New → Project**, import the frontend repo. Vercel
   auto-detects Vite (`vercel.json` in the repo pins `npm run build` /
   `dist` / SPA rewrites explicitly, so this isn't left to guesswork).
3. Before the first deploy, set one environment variable in Vercel's project
   settings (**Settings → Environment Variables**, apply to Production +
   Preview + Development):
   ```
   VITE_API_BASE_URL = https://acrev360-backend.onrender.com
   ```
   (Vite inlines env vars **at build time**, not runtime — if you change this
   later you must trigger a new deploy, not just restart something.)
4. Deploy. Vercel gives you a URL like `https://acrev360-frontend.vercel.app`.

## 4. Close the loop: point the backend's CORS at the real frontend URL

Now that you have the real Vercel URL, go back to Render → `acrev360-backend` →
**Environment**, and set:
```
CORS_ALLOWED_ORIGINS = https://acrev360-frontend.vercel.app
```
(comma-separated if you also want to allow a custom domain later). Saving
triggers an automatic redeploy of the web service.

If you also want Vercel **Preview deployments** (one URL per PR/branch) to be
able to call the API, either add each preview URL to
`CORS_ALLOWED_ORIGINS` as it's created, or switch
`config/settings/prod.py` to `django-cors-headers`'
[`CORS_ALLOWED_ORIGIN_REGEXES`](https://github.com/adamchainz/django-cors-headers#cors_allowed_origin_regexes)
with a pattern matching `https://acrev360-frontend-.*\.vercel\.app` — not done
by default here since it widens the allowed origin set beyond what's explicitly
approved.

## 5. Verify the live, end-to-end deploy

Same checklist as `GETTING_STARTED.md`, against the real URLs:
- Open the Vercel URL, log in with the admin credentials seeded in step 2.5.
- Confirm the dashboard shows live data (proves the frontend → Render round trip
  and CORS are both correct).
- Walk one real flow: enumerate a payer → issue a bill → collect a payment →
  see it in receipts and global performance.
- Check the Render **Logs** tab for the web service — should show real request
  traffic, no 500s, no CORS-rejection lines.

## 6. Enforce row-level security (do this before a second council goes live)

This app's tenant isolation is two layers: every viewset filters by the caller's
council, **and** Postgres RLS (`FORCE ROW LEVEL SECURITY` on 24 tables) makes the
database itself refuse cross-council rows if a filter is ever missed. The second
layer only exists if the app connects as a role that RLS applies to.

**Neon's default project owner role does not qualify** — it is a member of
`neon_superuser`, which carries `BYPASSRLS`. Connecting as it, RLS is silently
off: acting as a council with no data still returns every council's rows
(verified 2026-09-24: 115 payers visible as an empty council). The local Docker
setup already does this right (`docker/postgres-init/01-appuser.sql`); production
needs the equivalent, with two connection strings:

| Env var | Role | Used for |
|---|---|---|
| `DATABASE_URL` | `acrev360_app` (NOBYPASSRLS, DML only) | the running web service, the debt-refresh cron |
| `MIGRATE_DATABASE_URL` | the Neon owner role | `manage.py migrate` in `docker-entrypoint.sh` only |

One-time setup, run as the owner role (`psql` or Neon's SQL editor; pick a fresh
40+ char password):

```sql
CREATE ROLE acrev360_app WITH LOGIN PASSWORD '<strong-random>'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT CONNECT ON DATABASE "<dbname>" TO acrev360_app;
GRANT USAGE ON SCHEMA public TO acrev360_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acrev360_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO acrev360_app;
-- Append-only audit trail: nothing at runtime edits or deletes audit rows, so
-- make that a database guarantee rather than a convention.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM acrev360_app;
-- Tables/sequences the owner's future migrations create:
ALTER DEFAULT PRIVILEGES FOR ROLE "<owner-role>" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acrev360_app;
ALTER DEFAULT PRIVILEGES FOR ROLE "<owner-role>" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO acrev360_app;
```

Then: set `MIGRATE_DATABASE_URL` on Render to the **current** owner connection
string, change `DATABASE_URL` to the `acrev360_app` one (same host/db, pooled,
`?sslmode=require&channel_binding=require`), redeploy, and point the GitHub
Actions `ACREV_DATABASE_URL` secret at the app-role string too. Ad-hoc ops from
your own machine (`seed_*`, `reset_council_data`, `migrate`) keep using the owner
string.

Verify it took (should print `False`, then `0`, then a non-zero count):

```sql
SELECT rolbypassrls FROM pg_roles WHERE rolname = 'acrev360_app';
BEGIN; SELECT set_config('app.council_id', '<a council with no payers>', true);
SELECT count(*) FROM payer; ROLLBACK;                -- 0: RLS is filtering
BEGIN; SELECT set_config('app.council_id', '1', true);
SELECT count(*) FROM payer; ROLLBACK;                -- KAC's rows only
```

A new migration that needs DDL always runs as the owner (via
`MIGRATE_DATABASE_URL`), so the runtime role never needs schema privileges.

## Upgrading off the free tier later

- **Web service**: change `plan: free` to `starter` (or another paid plan) in
  `render.yaml`, or just change it in the dashboard — no code changes. This
  also gets you Shell/SSH access, so step 2.5's local-`DATABASE_URL` workaround
  stops being necessary (though it still works fine either way).
- **Database**: Neon's free tier has no hard expiry, but it does autosuspend
  idle compute and cap storage at 0.5GB — upgrade to a paid Neon plan in
  their dashboard when either becomes a problem; the connection string stays
  the same (or update `DATABASE_URL` in Render if you rotate credentials),
  no migration needed either way.
- **Celery/Redis**: see the note in §0 — add a `keyvalue` service and a
  `worker` service to `render.yaml` running
  `celery -A config worker --beat --loglevel=info`, set `CELERY_BROKER_URL`/
  `CELERY_RESULT_BACKEND` from the new Key Value instance, switch
  `refresh_all_councils_debt` back to being called via Celery beat
  (`CELERY_BEAT_SCHEDULE` in `config/settings/base.py` already has the entry),
  and delete `.github/workflows/debt-ageing-refresh.yml`. This also fixes the
  single-council limitation noted in that workflow once a second council is
  onboarded.
- **Custom domain**: attach in both Render's and Vercel's dashboards, then
  update `DJANGO_ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS` (backend) and
  `VITE_API_BASE_URL` (frontend, if the backend's domain also changes) to
  match, redeploy both.
