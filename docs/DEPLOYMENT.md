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
   Render web service's region — **Frankfurt (`eu-central-1`) for both** is the
   right pick for users in Nigeria/West Africa (see §7 for why, and how to move an
   existing Oregon deployment), Postgres version 17 to match what `render.yaml`
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

## 7. Moving to another region (Oregon → Frankfurt) without losing anything

**Why:** the app makes several database round-trips per request, and from West
Africa / Europe the round-trip to Oregon is ~400 ms against ~130–150 ms for
Frankfurt (measured TCP connect from the operator's location: Oregon 407 ms,
London 134 ms, Cape Town 147 ms). Both Render and Neon offer Frankfurt, both free.
**Render and Neon must move together** — the web service and its database must sit
in the same region, or every query pays the cross-ocean hop instead.

**Neither can be moved in place.** A Render service's region and a Neon project's
region are fixed at creation, so this is "build a second copy in Frankfurt, prove
it identical, switch, keep the old one as the rollback". Nothing is deleted until
the new stack has run cleanly for a week.

### What has to be carried over (the checklist that stops "settings" going missing)

| Item | Where it lives | How it moves |
|---|---|---|
| All table data, sequences, RLS policies and `FORCE ROW LEVEL SECURITY` flags | Neon database | `pg_dump` → `pg_restore` (below); proven with `manage.py db_fingerprint` |
| Database name + owner role (`ACRev360` / `ACRev360_owner`) | Neon project | Create the new project with the **same** database and role names, so only the hostname changes |
| Any extra roles (e.g. `acrev360_app` from §6) and their grants | Neon (roles aren't in a `pg_dump`) | Re-run the §6 SQL on the new project |
| Neon project settings: autosuspend delay, compute size, IP allow-list, retention | Neon console → Settings | Screenshot/copy them from the Oregon project first; set the same on the new one |
| Render env vars: `DJANGO_SECRET_KEY`, `WEBHOOK_ENCRYPTION_KEY`, `CORS_ALLOWED_ORIGINS`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_SECURE_SSL_REDIRECT`, `WEBHOOK_STRICT_SIGNATURES`, `DATABASE_URL`, plus any of `NUM_PROXIES`, `MIGRATE_DATABASE_URL`, rate-limit overrides | Render dashboard → the service → Environment | Copy each by hand (Render has no export). **`WEBHOOK_ENCRYPTION_KEY` must be byte-identical** or every stored API-client secret becomes unreadable. **`DJANGO_SECRET_KEY` identical** keeps everyone logged in; a new one just forces one re-login. **`DJANGO_ALLOWED_HOSTS` must include the new service's hostname** or every request is a 400 |
| Health check path `/api/v1/health`, plan, Docker build | Render service settings | Same values when creating the service |
| GitHub secret `ACREV_DATABASE_URL` (the nightly debt-ageing job) | GitHub → Settings → Secrets → Actions | Point at the Frankfurt database at cutover |
| Frontend API base URL (`VITE_API_BASE_URL`, and the fallback hard-coded in `packages/api/src/client.ts`) | Frontend deploy env | New service URL — or use a custom domain (see below) so this never has to change again |

### Phase A — build and rehearse (no downtime, nothing live is touched)

1. **Neon:** New project → region **AWS Europe (Frankfurt) `eu-central-1`**, Postgres
   version the same as the Oregon project (Settings shows it), database name
   `ACRev360`, owner role `ACRev360_owner`. Copy its **direct** (non-pooler) and
   **pooled** connection strings.
2. **Rehearsal dump/restore.** Use the **direct** endpoint (the one without `-pooler`)
   for both ends — PgBouncer's transaction mode isn't safe for `pg_dump`/`pg_restore`.
   Use a client at least as new as the server (`pg_dump --version`).
   ```bash
   pg_dump  "<OREGON direct url>"    --format=custom --no-owner --no-acl --file=acrev.dump
   pg_restore --no-owner --no-acl --dbname="<FRANKFURT direct url>" acrev.dump
   ```
   `--no-owner --no-acl` makes every object owned by whichever role restores it (the
   new project's `ACRev360_owner`) instead of failing on missing roles.
3. **Prove it.** Run the fingerprint against each database — as the *owner* role (a
   role subject to RLS reads tenant tables as empty; the command refuses to run as one):
   ```bash
   DATABASE_URL="<OREGON direct url>"    python manage.py db_fingerprint --checksums --output oregon.json
   DATABASE_URL="<FRANKFURT direct url>" python manage.py db_fingerprint --checksums --compare oregon.json
   ```
   (`DJANGO_SETTINGS_MODULE=config.settings.prod` plus the usual dummy
   `DJANGO_ALLOWED_HOSTS` / `CORS_ALLOWED_ORIGINS` / `DJANGO_SECRET_KEY` /
   `WEBHOOK_ENCRYPTION_KEY`, as in §2.5.) It compares exact row counts, content hashes,
   sequence positions, RLS flags/policy counts, extensions and applied migrations, and
   exits non-zero on any difference. At rehearsal time small differences are expected if
   the live app took writes since the dump — the point is that the mechanics work.
4. **Render:** New → **Web Service** (not Blueprint — the blueprint's `render.yaml`
   would collide with the running service) → same repo/branch `master`, **Docker**,
   **Frankfurt**, **Free**, health check `/api/v1/health`, a new name (e.g.
   `acrev360-api-fra`). Add every env var from the table; set `DATABASE_URL` to the
   Frankfurt **pooled** string and `DJANGO_ALLOWED_HOSTS` to include the new hostname.
   Deploy. Its start-up `migrate` is a no-op because the restored database already has
   every migration.
5. **Smoke-test the new stack** while the old one keeps serving: `/api/v1/health`,
   a login, a payer list, the dashboard, a revenue-items load. Compare speed:
   `curl -o /dev/null -s -w "connect %{time_connect}s  first-byte %{time_starttransfer}s\n" <url>/api/v1/health`
   against both services (run it 5 times; the first hit after idle includes a cold start).
   Then re-derive `NUM_PROXIES` for the new path with `GET /api/v1/ops/client-ip` (§8) and set it on
   the new service — the hop count belongs to the network path, not the code.

### Phase B — cutover (15–20 minutes; pick a quiet hour, not 01:00 UTC when the nightly job runs)

1. **Freeze writes.** Render dashboard → the *old* service → **Suspend**. From here the
   old database can't change, which is what makes the copy lossless.
2. **Final copy into a clean database.** Recreate the Frankfurt database empty (Neon
   console → Databases → delete and recreate `ACRev360`, or `DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;` as owner), then repeat the dump and restore from step A2.
3. **Fingerprint must be identical** (step A3, with `--checksums`). If it prints any
   difference: **stop, don't cut over** — unsuspend the old service and investigate.
4. **Re-create extra roles/grants** if §6 was already applied (roles aren't in the dump).
5. In the Frankfurt Render service confirm `DATABASE_URL` is the Frankfurt pooled string
   (if you change it now, Render redeploys on its own). Watch the logs until healthy.
6. **Switch traffic.**
   - *No custom domain:* set the frontend's `VITE_API_BASE_URL` to the new
     `https://acrev360-api-fra.onrender.com`, redeploy the frontend (Vite inlines it at
     build time), and update `CORS_ALLOWED_ORIGINS` on the new service if the
     frontend's own URL changed (it shouldn't).
   - *Custom domain (recommended for good):* give the API a domain you control
     (`api.example.com`, Render → Settings → Custom Domains — free), point the frontend
     at it once, and from then on a move like this one is a DNS/Render edit with no
     frontend change and an instant rollback.
7. Update the GitHub secret **`ACREV_DATABASE_URL`** to the Frankfurt string, then run
   the "Daily debt ageing refresh" workflow once manually (Actions → Run workflow) to
   prove the nightly job still works.
8. Re-run the smoke tests against the real frontend: log in as each role you care
   about, walk one bill → payment → receipt.

### Rollback

- **Before any new write lands on Frankfurt** (first few minutes): unsuspend the old
  service, point the frontend back. Nothing was lost; the old database never changed.
- **After real traffic has hit Frankfurt:** do not roll back — copy the other way
  (same dump/restore, reversed) or roll forward. Keeping the window short and testing
  in Phase A is what keeps this from being needed.

### Afterwards

- Keep the **old service suspended** (not deleted) and the **Oregon Neon project** for
  7 days as the fallback, then delete both. Watch Render's free allowance: 750
  instance-hours a month is shared by every free service in the workspace, so don't
  leave two awake around the clock — an idle service that has spun down doesn't burn
  hours, a busy one does.
- Update `render.yaml` (`region: frankfurt`, the new `name`, the `DJANGO_ALLOWED_HOSTS`
  value) and this file so the repo matches reality.
- Re-measure with the same `curl` line and compare with the Phase A numbers for Oregon.

## 8. Client IP behind the proxies (`NUM_PROXIES`)

Traffic reaches gunicorn through Cloudflare and Render's own proxy, so the caller's
address is a *position* in `X-Forwarded-For`, not the whole header. Unset, DRF keys
IP-based throttles on the entire header: a client rotating it gets a fresh budget on
every request (confirmed: 14 of 14 bad logins allowed), and behind Cloudflare the value
changes per request anyway, so the IP throttles never trip. The per-email login limits
and the per-user limit don't depend on this; the **anonymous** limits (public
bill/receipt lookups, the anonymous ceiling, the `actor_ip` on audit rows) do.

**Read the real chain off a live request** with the signed-in diagnostic
`GET /api/v1/ops/client-ip` (it echoes only the caller's own request headers):

```bash
TOKEN=$(curl -s -X POST https://<service>/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"<you>","password":"<pw>"}' | python -c "import sys,json; print(json.load(sys.stdin)['access'])")
curl -s https://<service>/api/v1/ops/client-ip -H "Authorization: Bearer $TOKEN" \
  -H 'X-Forwarded-For: 198.51.100.77'      # a spoofed entry, to see where it lands
```

Verified 2026-09-24 against the Oregon service (Cloudflare in front of Render):

```
x_forwarded_for:  198.51.100.77,203.0.113.5, 172.71.0.10, 10.0.0.1
                  ^ client-supplied  ^ real client   ^ Cloudflare edge  ^ Render internal
cf_connecting_ip: 203.0.113.5
```

Counting from the right, the real client is the **3rd** entry, and everything left of it
is attacker-controlled. So the value is **`NUM_PROXIES=3`** — set it in the Render
dashboard (Environment → add `NUM_PROXIES` = `3`; the service redeploys). Then confirm
`resolved_ip` in the same diagnostic equals your own address and that `num_proxies` is 3.
A value too small picks a shared proxy address (everyone throttled together); too large
picks a spoofable entry. **Re-run this after moving region or host** (§7) — the hop count
belongs to the network path, not the code — and it changes if Cloudflare is ever removed
from in front of the service.

Confirm the throttle now holds against spoofing (40 requests, each with a different
spoofed first entry — you should see 30 successes and then 429s, not 40 successes):

```bash
for i in $(seq 1 40); do
  curl -s -o /dev/null -w "%{http_code} " -H "X-Forwarded-For: 198.51.100.$i" \
    https://<service>/api/v1/bills/KAC/2026/000001
done; echo
```

Until it is set the anonymous limits are best-effort; nothing breaks.

## 9. Scaling past one worker

Caches and throttle counters use Django's default per-process cache (`LocMemCache`).
That is correct for the single gunicorn worker the free tier runs — the process that
handles a write is the one that serves the next read, so the dashboard and
revenue-item caches invalidate instantly. Running more workers (`WEB_CONCURRENCY` > 1)
or more instances gives each its own cache: a write in one no longer invalidates the
others (they catch up within 2 minutes — `apps/common/cachekeys.py`, `TOKEN_TTL`) and
throttle budgets multiply by the worker count. Before doing that, point `CACHES` at a
shared backend (Render's free Key Value / Redis instance, or Upstash) so all workers
agree.

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
