# ACRev360 v2 — Target Architecture (Multi-Council Redesign)

**Status: design document — nothing here is built yet.** This is the agreed architecture
for a fresh backend rewrite. The current codebase (`backend/`, documented in
[ARCHITECTURE.md](ARCHITECTURE.md) / [TDD.md](TDD.md)) becomes the _reference
implementation_: its domain logic, invariants, and lessons port forward; its code does not.

**Goal:** one platform serving multiple FCT Area Councils — onboarded incrementally, not
all at once — with per-council customization of the chart of revenue, rates, bill
identity, and print formats. End state: all six Abuja area councils (AMAC, Bwari,
Gwagwalada, Kuje, Kwali, Abaji). Kuje (the current single tenant) is the first.

---

## 1. Scale calibration — read this before adding anything

Six councils is a few hundred thousand payers, single-digit millions of transactions a
year, and low hundreds of concurrent staff and field agents. That is real production
scale — and it is comfortably served by **one Postgres cluster and a few stateless app
replicas**. It is not microservices, Kubernetes, or event-sourcing territory, and this
document deliberately chooses none of those. The genuinely hard requirements are
**tenancy correctness** (council A must never see council B's data), **per-council
customization without per-council deploys**, and **money-path integrity** — so that's
where the engineering goes. If a future bottleneck appears, the modular structure (§6)
is the escape hatch; distributed architecture on day one would be paying that tax with
no load to justify it.

## 2. Stack

| Layer       | Choice                                                              | Why                                                                                                                                                                                                                                                            |
| ----------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language    | Python                                                              | The prototype is Python — its domain logic ports by translation, not reinterpretation.                                                                                                                                                                         |
| Framework   | **Django + Django REST Framework**                                  | Money, roles, audit, and back-office workflows are what Django's batteries are for: mature auth/permissions, ORM with real migrations, `DecimalField` currency, and a free admin panel for council back-office ops that would otherwise be hand-built screens. |
| Database    | **PostgreSQL** (managed, with PITR backups)                         | Concurrent writers (SQLite's blocking limit), row-level security for tenancy (§4), `NUMERIC` money. The v1 schema was written Postgres-portable on purpose — this is the port it anticipated.                                                                  |
| Queue/cache | **Celery + Redis**                                                  | Reconciliation runs, settlement computation, notifications, webhook post-processing off the request path; Redis doubles as cache and rate-limit store.                                                                                                         |
| API         | REST, versioned `/api/v1/…`, OpenAPI generated from DRF serializers | The hand-written API_REFERENCE.md becomes generated output, not maintained prose.                                                                                                                                                                              |
| Deploy      | Docker, gunicorn behind nginx, ≥2 stateless replicas                | App servers hold no session state, so horizontal scaling is a replica count, not a redesign.                                                                                                                                                                   |

**Considered and rejected:** FastAPI + SQLAlchemy + Alembic — leaner and async-native,
but you hand-assemble auth, admin, and migration glue, and the bottleneck in this system
is the database, not request throughput. If async webhook ingestion ever genuinely
matters, it can be a small sidecar service; it is not a reason to build everything on it.

## 3. Multi-tenancy model

**Shared schema, `council_id` on every tenant-scoped table, enforced by Postgres
row-level security.**

- **Why shared schema:** FCT-level oversight (cross-council reporting) is clearly
  coming; per-tenant databases or schemas make "collections across all councils this
  quarter" a federation problem for the sake of isolation that RLS provides anyway.
  At six tenants, the operational simplicity wins outright.
- **Why RLS and not `WHERE` clauses:** v1 scopes consultants with per-query filters
  (`portfolio_filter()`), which works but means every new query is a chance to forget
  one. In v2 the request sets the council context (`SET LOCAL app.council_id = …`) and
  the database refuses cross-tenant rows even when application code has a bug. App-level
  scoping remains as a second layer, not the only layer.
- Council-agnostic tables (the revenue item _template_, FCT-level users) sit outside
  RLS; everything payer-, bill-, or money-shaped is policy-protected.
- FCT/oversight roles get an explicit multi-council context, not a policy bypass.

## 4. Per-council customization

The requirement is that bills and revenue item prices differ by council, and new
councils onboard without code changes. Three mechanisms:

**4.1 Template-and-override chart of revenue.**
A single global template holds harmonised item definitions (code, name, category, unit).
Each council _activates_ items from the template and attaches its own `rate_schedule`
rows — so Kuje's Liquor Licensing at ₦50,000 and AMAC's at a different figure are two
rate histories against one shared definition, and cross-council comparison ("Liquor
Licensing yield across the FCT") stays a simple query. Councils may also define
council-local items that exist outside the template. Rate history semantics carry over
from v1 unchanged: close the old row, open a new one, never overwrite.

**4.2 Council configuration as data, not branches.**
Everything that was hardcoded for KAC becomes a per-council config record: bill
reference prefix (`KAC/2026/…` → per-council), revenue bank accounts, treasurer contact
lines, demand-notice/demand-bill print templates and signatures, wards. No code path may
test "which council is this" — it reads config.

**4.3 Council onboarding as a first-class admin flow.**
Create council → activate template items → set rates → configure bank
accounts/prefix/print identity → create council admin users → onboard consultants and
agents. This flow _is_ the multi-council feature; it gets tests and an admin UI, not a
runbook of SQL.

## 5. Application structure

A **modular monolith**: one deployable, with enforced internal boundaries by domain —

```
tenancy/         councils, config, onboarding, RLS context
accounts/        users, roles, auth, sessions
registry/        payers, assets, enumeration, dedup
revenue/         item template, council activation, rate schedules
billing/         assessments, bills, lines, arrears consolidation
payments/        the single money-in path, receipts, verification
channels/        per-channel webhook adapters, settlement files, USSD
reconciliation/  bank-feed matching, exception queue
settlements/     consultant commission computation
enforcement/     debt cases, ageing, escalation ladder
fieldops/        agent worklists, offline sync replay
audit/           append-only audit trail
reporting/       dashboards, report builder, cross-council views
```

Modules communicate through service functions (as `post_payment()` does today), not by
reaching into each other's tables. This preserves the option to split a module into a
service later — an option the six-council scale almost certainly never exercises.

## 6. Data model — what changes from v1

Ports **as-is in concept** (see [SCHEMA.md](SCHEMA.md)): payer registry (`business_size`
included), assessments → bills → lines, payments → receipts, channel transaction feed,
reconciliation, settlements (with rate snapshots), debt cases, portfolio assignments,
daily returns, sync queue, audit log.

Changes:

- **All money becomes `NUMERIC(14,2)`** — v1 uses floats (`REAL`), a documented gap.
- **`revenue_item` splits** into `revenue_item_template` (global) and
  `council_revenue_item` (activation + council scoping); `rate_schedule` hangs off the
  council-level row.
- **New `council_config`** (or typed columns on `council`) for §4.2.
- **Reference generation** keeps the v1 two-phase pattern (insert with placeholder,
  derive from the row's own PK) — already race-safe — but sequences/prefixes become
  per-council.
- **`app_user` council scoping** becomes explicit, with FCT-level roles that hold
  multi-council grants.
- **Postgres RLS policies** on every tenant-scoped table, written alongside the tables
  in migrations, not bolted on after.

## 7. Invariants carried forward — not up for re-litigation

Lessons the prototype already paid for:

1. **One money-in path.** Every channel — POS, teller, transfer, USSD, agent banking,
   portal entry, offline sync replay — funnels through a single payment service. The
   SUPERSEDED-bill bug was fixable in one place precisely because of this; keep it so.
2. **Terminal bill states refuse payment.** `CANCELLED` and `SUPERSEDED` bills reject
   payment at the service layer, never only in UI gating.
3. **Arrears consolidation accounting.** Rolling prior balances into a new bill flips
   the old bills to `SUPERSEDED`, closes their debt cases, and _billed_ figures subtract
   `arrears_amount` so consolidation never double-counts. `billed − collected =
outstanding` must survive any consolidation — this gets a permanent regression test.
4. **Webhook idempotency** by `UNIQUE(channel_id, bank_txn_ref)` — replays return the
   original result.
5. **Offline sync replays through the same service functions** as online requests
   (v1 initially logged PAYER records without creating them — that class of bug is
   prevented by construction: sync handlers may not contain their own business logic).
6. **Rate changes never mutate history**; assessments cite the rate row they were
   priced at.
7. **Print and view are separate actions**; demand documents are per-council templates.

## 8. Authentication & access control

- Password hashing: **argon2** (v1: unsalted SHA-256 — blocking gap, fixed by default
  here). Per-user passwords; the shared demo password does not survive the rewrite.
- Sessions: DB/Redis-backed with expiry (v1's in-memory dict dies on restart and can't
  span replicas). Short-lived JWTs are acceptable if mobile offline flows prefer them;
  pick one, not both.
- Role model carries over (`COUNCIL_ADMIN`, `CONSULTANT`, `AGENT`, `GLOBAL_VIEW`) with
  one addition: FCT-level oversight roles spanning councils.
- Webhook signature verification (HMAC) **on by default** — v1 shipped it off by default
  behind an env flag; v2 inverts that.

## 9. Async work

Request path stays synchronous and fast; Celery takes: reconciliation runs, settlement
computation, debt re-ageing (scheduled, replacing v1's manual "Refresh Ageing" button),
SMS/notification dispatch, report exports, and webhook post-processing beyond the
idempotent write. Nothing money-critical happens _only_ in a task without a durable
record to replay from.

## 10. Testing

The prototype has zero automated tests; every check so far has been live-browser manual.
The rewrite starts with pytest and treats these as permanent, non-negotiable suites:

- **Tenancy:** council A's user can never read or write council B's rows (exercised
  against RLS directly, not just through the API).
- **Money:** the §7 invariants — one path, terminal-state refusal, consolidation
  conservation, idempotent replays.
- **Onboarding:** a fresh council can be created, configured, and billed end-to-end.

## 11. Migration & build phases

1. **Foundation** — project scaffold, tenancy + RLS, accounts/auth, council config,
   Kuje seeded as council #1 from the template chart.
2. **Core revenue cycle** — registry, revenue, billing (with arrears consolidation),
   payments/receipts. At this point the platform can do enumerate → bill → collect.
3. **Channels & reconciliation** — webhook adapters, OTC settlement, USSD,
   reconciliation, settlements, enforcement.
4. **Field ops & reporting** — mobile worklist/sync, dashboards, report builder,
   FCT-level cross-council views.
5. **Cutover** — migrate Kuje's real data (when real data exists; the demo DB reseeds
   and carries nothing worth migrating), retire the Flask prototype, onboard council #2
   with the §4.3 flow as its acceptance test.

Frontends are out of scope for this document (the rewrite starts with the backend); the
existing portal and PWA keep working against a v1-compatible surface where practical,
and their redesign is a separate decision.

## 12. Open questions (deferred, not forgotten)

- **JWT vs server-side sessions** for the mobile PWA's offline window — decide when
  fieldops is built.
- **SMS/notification provider** — the receipt-by-SMS flow is simulated in v1.
- **Real bank integrations** — adapter contracts per actual collecting bank replace the
  simulated webhook payloads; signature schemes per bank TBD.
- **Payer identity dedup across councils** — one payer operating in two councils is two
  registry rows today; a shared-identity layer (NIN/TIN-keyed) is a future decision
  with privacy implications worth its own review.
