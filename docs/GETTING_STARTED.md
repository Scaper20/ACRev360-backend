# Getting Started — running and verifying ACRev360 backend

Two ways to run the stack: the local Python virtualenv (fastest for day-to-day
development) or Docker Compose (closer to how it actually deploys). Both are
verified working end-to-end as of this writing. Everything below assumes you're
in `E:\ACRev360-backend`.

---

## Option A — local virtualenv

```bash
./.venv/Scripts/activate            # Windows
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py seed_kuje --admin-password acrev360-dev-2026
python manage.py runserver 8000
```

Requires the local PostgreSQL 18 service already running on this machine
(`localhost:5432`, database/role `acrev360`/`acrev360` — see `.env`). Celery
isn't required for the API to work day-to-day: the one scheduled task (debt
re-ageing) also has a synchronous path used by the `POST /api/v1/debt/refresh`
endpoint, so nothing breaks if no worker is running locally.

Reachable at **`http://127.0.0.1:8000`**.

## Option B — Docker Compose

```bash
docker compose up -d --build
docker compose exec web python manage.py seed_kuje --admin-password acrev360-dev-2026
```

Brings up five containers: `postgres`, `redis`, `web` (gunicorn), `celery-worker`,
`celery-beat`. The entrypoint (`docker-entrypoint.sh`) runs migrations and
`collectstatic` automatically on every start — you don't run `migrate` by hand.

Also reachable at **`http://127.0.0.1:8000`** (same host port as Option A — don't
run both at once, they'll fight over port 8000).

**Postgres/Redis are on non-default host ports.** This machine already runs a
native PostgreSQL 18 service on `5432` and something on `6379`; mapping Docker's
containers to the same host ports would create two listeners silently racing for
the same port (confirmed via `netstat` — not hypothetical). Docker's Postgres is
on host port **5433**, Redis on **6380**. Containers still talk to each other
over the normal internal ports (5432/6379) — this only affects connecting *from
the host* (e.g. `psql -p 5433`).

Useful commands:
```bash
docker compose ps                        # status of all 5 services
docker compose logs -f web                # tail the app server
docker compose exec web python manage.py shell
docker compose down                       # stop everything
docker compose down -v                    # stop and wipe the Postgres volume
```

---

## Where everything is

| What | Local venv | Docker |
|---|---|---|
| API | `http://127.0.0.1:8000/api/v1/...` | same |
| Swagger UI | `http://127.0.0.1:8000/api/docs/` | same |
| Redoc | `http://127.0.0.1:8000/api/redoc/` | same |
| Raw OpenAPI schema | `http://127.0.0.1:8000/api/schema/` | same |
| Health check | `http://127.0.0.1:8000/api/v1/health` | same |
| Postgres | `localhost:5432` (native service) | `localhost:5433` |
| Redis | n/a unless you run one | `localhost:6380` |

Seeded admin login (either setup, right after `seed_kuje`):
**username `admin`, password `acrev360-dev-2026`** — this is a *local dev-only*
password you chose at seed time (`--admin-password`); it's not a shared demo
password baked into the code (see TDD.md's "blocking gaps" list — that was
explicitly called out as v1's mistake, not repeated here).

## Trying the API interactively (Swagger)

1. Open `http://127.0.0.1:8000/api/docs/`.
2. Expand `POST /api/v1/auth/login`, "Try it out", body
   `{"username": "admin", "password": "acrev360-dev-2026"}`, Execute. Copy the
   `access` token from the response.
3. Click the **Authorize** button (top right), paste `Bearer <access token>`
   into the value field (include the word `Bearer`), Authorize.
4. Every other endpoint now runs as that logged-in council admin — try
   `GET /api/v1/revenue-items`, `GET /api/v1/payers`, etc. "Try it out" on any
   of them.

## Looking at the database directly

`psql` ships with the PostgreSQL 18 install already on this machine:

```bash
# local venv's database
psql -U acrev360 -h localhost -p 5432 -d acrev360

# Docker's database
psql -U acrev360 -h localhost -p 5433 -d acrev360
```
(password `acrev360` for the app role in both cases — see `.env`.) Once
connected: `\dt` lists tables, `SELECT * FROM council;`, `SELECT * FROM bill;`,
etc. A GUI tool (pgAdmin, DBeaver, TablePlus — none are currently installed on
this machine, any would work) is a more comfortable option if you'd rather
browse than type SQL; use the same host/port/user/password/database above.

**One genuinely confusing thing worth knowing up front:** row-level security
(the mechanism that keeps council A from ever seeing council B's rows —
V2_ARCHITECTURE.md §3) only applies to the app's own `acrev360` database role.
Postgres superusers (`postgres`, which is what you'd use for admin tasks like
creating the database in the first place) **bypass RLS entirely** — that's
Postgres's own design, not a bug in this policy. So if you connect as
`postgres` and run `SELECT * FROM payer;`, you'll see every council's rows at
once, even though the exact same query through the API (or through `psql -U
acrev360`) is correctly scoped. If that ever looks like a security hole, it
isn't — it's which role you're connected as.

## Running the tests

```bash
pytest
```

10 tests, all against a real Postgres instance (`test_acrev360`, created and
destroyed automatically by pytest-django — never touches your seeded dev data).
They cover the three invariant classes this product cannot regress on
(V2_ARCHITECTURE.md §10):

- **`tests/test_tenancy_rls.py`** — council isolation exercised directly against
  RLS: a naive query with no council filter at all, a write attempted for the
  wrong council (rejected by the database itself), a request with no council
  context (sees nothing).
- **`tests/test_money_invariants.py`** — the single payment path, terminal-bill
  refusal (`CANCELLED`/`SUPERSEDED` can't take a payment), arrears consolidation
  never double-counts what's owed, webhook replays are idempotent (same
  `bank_txn_ref` twice → one payment, not two).
- **`tests/test_onboarding.py`** — a brand-new council can be created,
  configured, and billed end-to-end.

## "Is it actually working?" — checklist

Run through this after any setup (local or Docker):

1. `curl http://127.0.0.1:8000/api/v1/health` → `{"status": "ok", ...}`
2. `/api/docs/` and `/api/redoc/` both load in a browser
3. Logging in via Swagger (above) returns an `access` token, and Authorize
   unlocks the other endpoints
4. `pytest` → `10 passed`
5. (Docker only) `docker compose ps` shows all 5 services `Up`/`healthy`

If all five hold, the backend is genuinely working end to end, not just
"the server started."
