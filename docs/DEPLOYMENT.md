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
  service, a free Postgres database, and a Cron Job, fully wired together.
- **`Dockerfile`** — already the one verified working locally (§ GETTING_STARTED.md);
  now also binds to Render's `$PORT` instead of a hardcoded 8000.
- **No Redis / Celery worker service in the free deploy.** The only scheduled
  job today is the daily debt-ageing refresh
  (`apps/enforcement/tasks.py::refresh_all_councils_debt`), and nothing in the
  codebase calls `.delay()`/`.apply_async()` yet — so a standing Celery worker +
  beat + Redis broker (3 extra always-on pieces) isn't earning its cost yet.
  `render.yaml` instead runs `python manage.py refresh_debt_ageing` (a new thin
  wrapper that calls the same task function synchronously) once a day via
  Render's native Cron Jobs. When real async work shows up (e.g. webhook
  post-processing), add a Render Key Value instance + a `worker` service running
  `celery -A config worker --beat`, matching `docker-compose.yml`'s
  `celery-worker`/`celery-beat` services — nothing else changes.

---

## 1. Backend → Render

1. Push this repo to GitHub/GitLab if it isn't already remote (it's currently
   only a local git repo — `git remote add origin <url>` then `git push -u origin master`).
2. In the Render dashboard: **New → Blueprint**, pick this repo. Render reads
   `render.yaml` and shows you the `acrev360-backend` web service, the
   `acrev360-debt-ageing` cron job, and the `acrev360-db` Postgres database it's
   about to create.
3. Render will prompt for the env vars marked `sync: false` before the first
   deploy — you need two values ready:
   - **`WEBHOOK_ENCRYPTION_KEY`** — generate one:
     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
     Use the **same value** for both the web service and the cron job (paste it
     in twice — Render doesn't let a Blueprint share one `sync: false` value
     across services). Losing/rotating this key makes existing encrypted webhook
     secrets unreadable, so store it somewhere durable (password manager), not
     just in Render's dashboard.
   - **`CORS_ALLOWED_ORIGINS`** — you don't have the Vercel URL yet. Put in a
     placeholder for now (e.g. `https://placeholder.vercel.app`); you'll fix
     this in step 3 below once the frontend is deployed. Same value for both
     services (the cron job doesn't serve HTTP, but `config/settings/prod.py`
     requires this var to be set for *any* process, since Django settings load
     on every `manage.py` invocation).
4. Click **Apply**. First deploy takes a few minutes (Docker build + `migrate`
   + `collectstatic` run automatically via `docker-entrypoint.sh`).
5. Once the web service is live, seed the database — Render dashboard → the
   `acrev360-backend` service → **Shell** tab:
   ```bash
   python manage.py seed_kuje --admin-password <pick-a-real-password-this-time>
   ```
   (Don't reuse the local dev password `acrev360-dev-2026` for anything public.)
6. Confirm it's actually up:
   - `https://acrev360-backend.onrender.com/api/v1/health` → `{"status": "ok"}`
   - `https://acrev360-backend.onrender.com/api/docs/` loads Swagger UI
   - **Free-tier note:** the web service spins down after 15 minutes idle: the
     first request after a quiet spell takes ~30–60s while it wakes up. Same
     applies to the cron job's container startup each run — expected, not a bug.

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
(comma-separated if you also want to allow a custom domain later). Update the
cron job's copy too, for consistency, though it doesn't strictly need to be
correct there. Saving triggers an automatic redeploy of the web service.

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

- **Web service / cron job**: change `plan: free` to `starter` (or another paid
  plan) in `render.yaml`, or just change it in the dashboard — no code changes.
- **Database**: Render's free Postgres is time-limited and has no backups;
  upgrading a plan in the dashboard keeps the same database, no migration
  needed.
- **Celery/Redis**: see the note in §0 — add a `keyvalue` service and a
  `worker` service to `render.yaml` running
  `celery -A config worker --beat --loglevel=info`, set `CELERY_BROKER_URL`/
  `CELERY_RESULT_BACKEND` from the new Key Value instance, and switch
  `refresh_all_councils_debt` back to being called via Celery beat
  (`CELERY_BEAT_SCHEDULE` in `config/settings/base.py` already has the entry —
  it's just unused while the cron job calls the task function directly).
- **Custom domain**: attach in both Render's and Vercel's dashboards, then
  update `DJANGO_ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS` (backend) and
  `VITE_API_BASE_URL` (frontend, if the backend's domain also changes) to
  match, redeploy both.
