# ACRev360 Backend

Revenue administration & collection platform for FCT Area Councils — Django + DRF
on PostgreSQL, per [docs/V2_ARCHITECTURE.md](docs/V2_ARCHITECTURE.md). Kuje Area
Council (KAC) is council #1.

**New here?** See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) — both ways
to run the stack (local venv or Docker), where the API docs/database/tests
actually live, and a checklist for confirming it's genuinely working.

## Stack

Django 6.1, Django REST Framework, PostgreSQL (with row-level security for
multi-tenancy), Celery + Redis, JWT auth (`djangorestframework-simplejwt`),
`drf-spectacular` for the OpenAPI schema/docs. See [docs/TDD.md](docs/TDD.md) and
[docs/API_REFERENCE.md](docs/API_REFERENCE.md).

## Local development

```bash
python -m venv .venv
./.venv/Scripts/activate        # Windows; `source .venv/bin/activate` elsewhere
pip install -r requirements/dev.txt

cp .env.example .env            # then fill in a real DATABASE_URL, SECRET_KEY, etc.

python manage.py migrate
python manage.py seed_kuje      # seeds Kuje (KAC) as council #1 — prints the admin password once
python manage.py runserver
```

Requires a local PostgreSQL instance (RLS policies are Postgres-specific — this
does not run on SQLite) and, for Celery, Redis.

- API docs: `http://127.0.0.1:8000/api/docs/` (Swagger) or `/api/redoc/`
- Health check: `GET /api/v1/health`

## Tests

```bash
pytest
```

The suite in `tests/` covers the three permanent invariant classes this product
cannot regress on (V2_ARCHITECTURE.md §10): tenancy isolation (exercised directly
against Postgres RLS), money-path invariants (one payment path, terminal-state
refusal, arrears-consolidation conservation, idempotent webhook replay), and
end-to-end council onboarding.

## Docker

```bash
docker compose up -d --build
docker compose exec web python manage.py seed_kuje
```

Brings up Postgres, Redis, the Django app (gunicorn), and Celery worker/beat —
verified end-to-end (full smoke walk, plus a real Celery task round trip via the
worker/broker/result-backend). Postgres/Redis are on host ports **5433**/**6380**,
not the defaults — this machine already runs native services on 5432/6379. See
[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for the full picture.

## Repository layout

```
config/            settings (base/dev/prod), root URLconf, Celery app
apps/
  common/           shared abstractions: base models, permissions, RLS SQL helper,
                     exception handler, reference generation, dashboard/health views
  tenancy/          Council, WardZone, CouncilConfig, RLS context + middleware,
                     council onboarding service
  accounts/         AppUser (custom user model), AppRole, SubConsultant, FieldAgent
  registry/         Payer, EnumeratedAsset
  revenue/          RevenueCategory, RevenueItemTemplate, CouncilRevenueItem,
                     RateSchedule, ConsultantPortfolio
  billing/          Assessment, Bill, BillLine — the recompute/arrears-consolidation
                     service layer
  payments/         PaymentChannel, Payment, Receipt, APIClient — the single
                     post_payment() money-in path
  channels/         per-channel webhook adapters (validate/normalise/verify_signature)
  reconciliation/   ReconciliationRun, ReconciliationException
  settlements/      CommissionSettlement
  enforcement/      DebtCase, the ageing/escalation ladder, scheduled re-ageing task
  audit/            append-only AuditLog
tests/              pytest suite (tenancy / money / onboarding)
docs/               product & architecture documentation (see below)
```

## Documentation

- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) — how to run and verify everything, both setups
- [docs/PRD.md](docs/PRD.md) — what the product is and why
- [docs/V2_ARCHITECTURE.md](docs/V2_ARCHITECTURE.md) — the target architecture this build implements
- [docs/SCHEMA.md](docs/SCHEMA.md) / [docs/TDD.md](docs/TDD.md) — data model and technical design (prototype reference + what changes for v2)
- [docs/APP_FLOW.md](docs/APP_FLOW.md) / [docs/DESIGN_BRIEF.md](docs/DESIGN_BRIEF.md) — UX flow and visual design system, for the frontend
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md) — pointer to the generated, always-current API docs

## Scope of this build pass

Foundation, core revenue cycle, and channels/reconciliation
(V2_ARCHITECTURE.md §11, phases 1–3): tenancy/RLS, auth, payer registry, chart of
revenue, billing with arrears consolidation, payments/receipts across all five
channels, reconciliation, commission settlements, and debt enforcement. **Not**
in this pass: field ops (mobile PWA, offline sync/worklist) and the full custom
report builder / cross-council oversight views — phase 4.
