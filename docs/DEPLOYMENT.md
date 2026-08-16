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

- **`render.yaml`** — a Render "Blueprint": one command deploys a Docker web
  service and a free Postgres database, fully wired together.
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

## 1. Backend → Render

1. Push this repo to GitHub/GitLab if it isn't already remote (it's currently
   only a local git repo — `git remote add origin <url>` then `git push -u origin master`).
2. In the Render dashboard: **New → Blueprint**, pick this repo. Render reads
   `render.yaml` and shows you the `acrev360-backend` web service and the
   `acrev360-db` Postgres database it's about to create.
3. Render will prompt for the env vars marked `sync: false` before the first
   deploy — you need two values ready:
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
   + `collectstatic` run automatically via `docker-entrypoint.sh`).
5. **Seed the database.** Free web services don't get Shell/SSH access on
   Render, so run the seed command from your own machine instead, pointed at
   the database directly:
   - Render dashboard → the `acrev360-db` database → **Connect** → copy the
     **External Database URL** (something like
     `postgres://acrev360:...@dpg-xxxx.oregon-postgres.render.com/acrev360`).
     Free Postgres instances still allow external connections — this isn't a
     paid-only feature, only Shell/SSH access is.
   - From `E:\ACRev360-backend`, with your local venv active, run `seed_kuje`
     against that URL without touching your local `.env` (PowerShell shown;
     bash is the same with `export` instead of `$env:`):
     ```powershell
     $env:DATABASE_URL   = "<the External Database URL you copied>"
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
6. Confirm it's actually up:
   - `https://acrev360-backend.onrender.com/api/v1/health` → `{"status": "ok"}`
   - `https://acrev360-backend.onrender.com/api/docs/` loads Swagger UI
   - **Free-tier note:** the web service spins down after 15 minutes idle: the
     first request after a quiet spell takes ~30–60s while it wakes up.
7. **Wire up the daily debt-ageing refresh** (GitHub Actions, since Render
   Cron Jobs cost $1/mo minimum even on the "free" plan — see §0): in the
   backend repo's GitHub settings → **Settings → Secrets and variables →
   Actions**, add three repository secrets:
   ```
   ACREV_API_BASE_URL   = https://acrev360-backend.onrender.com
   ACREV_ADMIN_USERNAME = <the --admin-username you seeded with, default "admin">
   ACREV_ADMIN_PASSWORD = <the password you just set in step 5>
   ```
   The workflow (`.github/workflows/debt-ageing-refresh.yml`) then runs daily
   automatically; you can also trigger it manually from the repo's **Actions**
   tab (**Run workflow**) to confirm it works right away instead of waiting a day.

## 2. Frontend → Vercel

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

## 3. Close the loop: point the backend's CORS at the real frontend URL

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

## 4. Verify the live, end-to-end deploy

Same checklist as `GETTING_STARTED.md`, against the real URLs:
- Open the Vercel URL, log in with the admin credentials seeded in step 1.5.
- Confirm the dashboard shows live data (proves the frontend → Render round trip
  and CORS are both correct).
- Walk one real flow: enumerate a payer → issue a bill → collect a payment →
  see it in receipts and global performance.
- Check the Render **Logs** tab for the web service — should show real request
  traffic, no 500s, no CORS-rejection lines.

## Upgrading off the free tier later

- **Web service**: change `plan: free` to `starter` (or another paid plan) in
  `render.yaml`, or just change it in the dashboard — no code changes. This
  also gets you Shell/SSH access, so step 1.5's local-`DATABASE_URL` workaround
  stops being necessary (though it still works fine either way).
- **Database**: Render's free Postgres is time-limited and has no backups;
  upgrading a plan in the dashboard keeps the same database, no migration
  needed.
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
