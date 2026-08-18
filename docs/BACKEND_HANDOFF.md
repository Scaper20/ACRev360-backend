# Backend handoff — gaps found rebuilding the frontend

From: frontend rebuild (`ACRev360-frontend`)
To: Scaper20 (`ACRev360-backend`)
Date: 2026-08-18

## Context

I rebuilt the council-portal frontend against `ACRev360-backend`'s live OpenAPI
schema and cross-checked every screen against the old RevAc prototype
(`revac.onrender.com`) it's replacing. Two gaps turned out to be closeable purely
in the frontend and are already fixed there (Debt Case inline payment, POS
Terminal Fleet's Agent column). The seven items below are not — each is
either a field that doesn't exist anywhere in the API response, or an
endpoint that doesn't exist at all. I've dug into the actual models/
serializers (not just the OpenAPI schema) for each one so this can go
straight into implementation rather than needing a second investigation
pass. File paths are relative to the `ACRev360-backend` repo root.

Also flagging one item that isn't a frontend gap at all but a real local-dev
correctness bug I found and fixed while setting up Docker — see **Item 0**.

---

## Item 0 — Local dev Postgres setup silently bypasses RLS (already fixed locally)

**Not a frontend gap — a setup bug in `docker-compose.yml`.** Flagging because
anyone else following the repo's own `docker compose up` + `GETTING_STARTED.md`
instructions as written will hit the same thing.

**What's wrong:** `docker-compose.yml` sets `POSTGRES_USER: acrev360` (line 6)
and then points `DATABASE_URL` at that same `acrev360` role for `web`,
`celery-worker`, and `celery-beat`. The official Postgres image makes
whatever `POSTGRES_USER` is at container init the **cluster's bootstrap
superuser**. Postgres superusers bypass Row-Level Security unconditionally —
`FORCE ROW LEVEL SECURITY` (which `apps/common/db.py`'s `enable_tenant_rls_sql`
correctly sets) has no effect on a superuser connection. So every request
served by the Django app was running with tenant isolation silently
disabled — one council's queries could read another's rows if the app-level
`council_id` filter were ever missing or buggy, and RLS wasn't there as a
backstop like the migrations imply it is.

**How I confirmed it:** all 4 tenancy/RLS pytest tests failed on a clean
`docker compose up`. I tried `ALTER ROLE acrev360 NOSUPERUSER` — Postgres
refused it outright ("The bootstrap superuser must have the SUPERUSER
attribute"), which confirms this isn't fixable by adjusting the existing
role; a cluster's bootstrap superuser can never be stripped of SUPERUSER
under any connecting role.

**Fix (works, applied locally, not yet upstreamed):** create a second,
non-superuser role and connect the app as that role instead of the
bootstrap superuser.

```sql
CREATE ROLE appuser WITH LOGIN PASSWORD 'acrev360' CREATEDB;
GRANT ALL PRIVILEGES ON DATABASE acrev360 TO appuser;
GRANT USAGE, CREATE ON SCHEMA public TO appuser;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO appuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO appuser;
ALTER DEFAULT PRIVILEGES FOR ROLE acrev360 IN SCHEMA public GRANT ALL ON TABLES TO appuser;
ALTER DEFAULT PRIVILEGES FOR ROLE acrev360 IN SCHEMA public GRANT ALL ON SEQUENCES TO appuser;
```

then `DATABASE_URL: postgres://appuser:acrev360@postgres:5432/acrev360` in
`docker-compose.yml` for `web`/`celery-worker`/`celery-beat` (`acrev360`
stays as-is for `postgres`'s own `POSTGRES_USER`, since migrations still run
fine as the table owner — `FORCE ROW LEVEL SECURITY` only needs to bind the
*app's runtime connection*, not the migration connection). Confirmed all 10
backend tests pass after this change.

**What I'd actually recommend upstreaming**, since the manual `psql` step
above doesn't survive a fresh `postgres_data` volume and isn't documented
anywhere a new dev would find it: add a Postgres init script at
`docker/postgres-init/01-appuser.sql` with the block above (no directory
like this exists yet — `docker-compose.yml`'s `postgres` service has no
`docker-entrypoint-initdb.d` volume mount today). The official Postgres
image runs every `.sql`/`.sh` file in that directory automatically on first
init, so a fresh `docker compose up` would create `appuser` and grant it
correctly with zero manual steps, and `DATABASE_URL` for the three app
services can just be updated to use it. I did not open a PR for this since I
don't have push access to the repo — happy to if that'd help.

---

## Item 1 — Dashboard summary is missing most of what the old app showed

**Endpoint:** `GET /api/v1/dashboard/summary`
**File:** `apps/common/api/dashboard.py`, `DashboardSummaryView` (lines 43–70)

**Old app** (`renderDashboard`, `frontend/app.js:248`) rendered from
`/api/dashboard/summary`:
```
{
  billed, collected, outstanding,        // present in new API
  bills, assessments, payers,            // MISSING — counts
  active_agents,                         // MISSING — count
  by_channel: [{channel_code, channel_name, amount}],   // MISSING
  by_item: [{item_name, billed}],                       // MISSING — top revenue items
  trend: [{d, amount}]                   // MISSING — 14-day daily collections
}
```

**New API today** only returns `billed`, `collected`, `outstanding`,
`bills_by_status` (a status→count breakdown, which the old app didn't have
but is a fine addition). Everything else above is absent, so four of the
five dashboard sections (agent/payer counts, the channel-mix flow chart, the
top-revenue-items bar chart, and the 14-day trend chart) render as empty on
the new frontend — not a UI bug, the data simply isn't in the response.

**What's needed**, added into `DashboardSummaryView.get()`:
- `bills = bills.count()`, `assessments = <count of Assessment rows in the same filter>`, `payers = Payer.objects.filter(council_id=...).count()`, `active_agents = FieldAgent.objects.filter(council_id=..., status=FieldAgent.ACTIVE).count()`
- `by_channel`: `payments.values("channel__code", "channel__provider").annotate(amount=Sum("amount"))` (or whatever field holds a human channel name — `channel_name` in the old response, check `PaymentChannel` model for the right source)
- `by_item`: aggregate `BillLine` (or `Assessment`) amounts grouped by revenue item, same `bills`/`portfolio_filter` scoping already used for `billed` above
- `trend`: daily `Sum("amount")` on `payments` grouped by `created_at__date` for the last 14 days (`TruncDate`, `Count`/`Sum` + `annotate`)

All of this can reuse the exact `bills`/`payments` querysets already built in
the view (lines 53–61) — it's additional aggregation on data the view is
already fetching, not new joins.

---

## Item 2 — Dashboard global (council-admin) view is missing per-consultant and per-ward detail

**Endpoint:** `GET /api/v1/dashboard/global`
**File:** `apps/common/api/dashboard.py`, `DashboardGlobalView` (lines 73–105)

**Old app** (`renderGlobal`, `frontend/app.js:297`) from `/api/dashboard/global`:
```
{
  consultants: [{consultant_name, billed, collected, collection_rate, commission_accrued, status}],
  wards: [{ward_name, payers, collected}]
}
```

**New API today** returns `by_consultant: [{consultant_name, collected}]` and
`by_ward: [{ward_name, collected}]` — collected-only, missing `billed`,
`collection_rate`, `commission_accrued`, `status` per consultant, and
`payers` (count) per ward.

**What's needed:**
- Per consultant: add a `billed` aggregate mirroring the existing
  `by_consultant` query but against `Bill` instead of `Payment` (same
  `payer__enumerated_by__consultant` path already used at line 83), then
  `collection_rate = round(collected / billed * 100)` in Python once both
  are fetched (matches how the old backend likely computed it — no need to
  do this in SQL). `commission_accrued` — pull from `CommissionSettlement`
  (`apps/settlements/models.py`) for the consultant's open period, or 0 if
  none computed yet. `status` — join `Consultant.status` directly, it's
  already on the model.
- Per ward: add `payers=Count("bill__payer", distinct=True)` (or
  equivalent) to the existing `by_ward` aggregation.

---

## Item 3 — Bills list has no Consultant column

**Endpoint:** `GET /api/v1/bills`
**File:** `apps/billing/api/serializers.py`, `BillSerializer` (lines 6–17)

Old app's bill list showed which consultant a bill's payer belongs to. The
new `BillSerializer` has no such field — `fields` (line 13) only exposes
`payer`/`payer_ref`/`full_name`, nothing consultant-related.

**What's needed** — add a method field, not a plain `source=` chain, since
`payer.enumerated_by` can be null for council-direct payers (this is the
exact same relation `dashboard.py:83` already walks for the global view, so
the path is confirmed correct, just needs null-safety in the serializer
context where a raw `source=` chain would throw instead of falling back):

```python
consultant_name = serializers.SerializerMethodField()

def get_consultant_name(self, obj):
    enum = getattr(obj.payer, "enumerated_by", None)
    consultant = getattr(enum, "consultant", None) if enum else None
    return consultant.consultant_name if consultant else None
```
Add `"consultant_name"` to `fields` (line 14) and `read_only_fields` (line 17).

---

## Item 4 — Payments list has no Payer name

**Endpoint:** `GET /api/v1/payments`
**File:** `apps/payments/api/serializers.py`, `PaymentSerializer` (lines 12–22)

Old app's payments table showed the payer's name per row. `Payment` has no
direct payer relation (it only carries `bill`), so this needs to come from
`bill.payer`, exactly the same two-hop pattern `BillSerializer` already uses
for `payer_ref`/`full_name` (`apps/billing/api/serializers.py:7-8`) — this
one's a straight `source=` chain since `payment.bill.payer` is never null
(a `Bill` always has a `payer` FK, unlike the optional `enumerated_by` in
Item 3):

```python
full_name = serializers.CharField(source="bill.payer.full_name", read_only=True)
payer_ref = serializers.CharField(source="bill.payer.payer_ref", read_only=True)
```
Add both to `fields` (line 19) and `read_only_fields` (line 22).

---

## Item 5 — POS Terminal Fleet is missing Bank Terminal ID and Collected columns

**Endpoint:** `GET /api/v1/terminals`
**Model:** `apps/payments/models.py`, `POSTerminal` (lines 30–46)
**Serializer:** `apps/payments/api/serializers.py`, `POSTerminalSerializer` (lines 43–47)

Old app's terminals table (`renderTerminals`, `frontend/app.js:1191`) showed
`bank_terminal_id` and a `collected` money total per terminal, alongside
serial/agent/ward/status (agent/ward/status already exist on the new model
and are now surfaced on the frontend as of this handoff).

**Two different kinds of gap here, worth knowing apart:**

1. **Bank Terminal ID is a genuinely missing field.** `POSTerminal` has no
   equivalent to the old model's separate bank-assigned terminal ID
   (distinct from `terminal_id`, which is the council's own reference). If
   this distinction still matters operationally, it needs a new field —
   `bank_terminal_id = models.CharField(max_length=32, blank=True)` — plus a
   migration and serializer field.

2. **Collected is not just a missing serializer field — `Payment` has no
   link to `POSTerminal` at all.** `Payment` (line 70 of the same file)
   references `bill` and `channel` (a `PaymentChannel`, e.g. "POS" as a
   channel *type*), but never a specific `POSTerminal` instance. There's no
   way to compute "amount collected through terminal #X" with the current
   schema — it's not an aggregation that's possible with existing columns.
   If per-terminal collection totals matter, `Payment` needs a nullable
   `terminal = models.ForeignKey(POSTerminal, null=True, blank=True, ...)`,
   populated wherever POS payments are posted (presumably
   `payments.services.post_payment()`, referenced in the `Payment` model's
   docstring), before this can be reported at all.

---

## Item 6 — Reconciliation has no global "Unmatched Bank Credits" view

**Old app** (`renderReconciliation`, `frontend/app.js:786`) showed two
independent sections: "Recent Runs" *and* a separate, cross-run "Unmatched
Bank Credits" table — `bank_txn_ref, channel_name, narration, amount,
value_date, match_status` — that showed every unmatched feed row regardless
of which run touched it.

**New API today** only exposes exceptions nested inside a specific run
(`ReconciliationRunSerializer.exceptions`, `apps/reconciliation/api/
serializers.py:18`) — there's no endpoint that lists unmatched items across
all runs. `ReconciliationRunViewSet` (`apps/reconciliation/api/views.py`) is
list + two `POST` actions (`run`, `resolve_exception`) only; resolving an
exception even requires already knowing its `exception_id`, which today is
only discoverable by opening the specific run it's nested under.

This might be a deliberate architecture choice (per-run exceptions is a
reasonable model) rather than an oversight — flagging it as a real behavior
difference either way, since "browse everything unmatched" and "drill into
one run's exceptions" are genuinely different workflows for whoever's doing
reconciliation day to day.

**If a global view is wanted, two gaps to close:**

1. **No endpoint.** `ReconciliationException` is itself council-scoped
   (`apps/reconciliation/models.py:33`) and already carries a `run` FK, so a
   cross-run query is straightforward —
   `ReconciliationException.objects.filter(council_id=..., resolved_at__isnull=True)`
   — it just isn't exposed. Would need something like a
   `GET /api/v1/reconciliation/exceptions?resolved=false` list action on
   `ReconciliationRunViewSet`, serialized with `run__channel__code` and
   `run__run_date` added to `ReconciliationExceptionSerializer` so each row
   is self-describing outside the context of its parent run.

2. **`narration` and a distinct `value_date` don't exist on the feed model
   at all.** `ChannelTransactionFeed` (`apps/payments/models.py:103`) has
   `bank_txn_ref`, `amount`, `match_status`, `received_at`, and a
   `raw_payload` JSON blob — no `narration` or `value_date` columns. If the
   underlying bank feed format actually carries these (worth checking
   against whatever real bank webhook payload this is meant to represent),
   they're sitting unextracted inside `raw_payload` today and would need
   promoting to real columns; if the feed format doesn't carry them at all,
   the old app's columns can't be matched exactly and would need to fall
   back to `received_at` for "value date" and drop narration.

---

## Summary table

| # | Area | Kind of gap | Effort |
|---|---|---|---|
| 0 | Local dev Postgres RLS bypass | Setup bug (fix ready, needs upstreaming) | Small — one init script |
| 1 | Dashboard summary | Missing aggregates, no new tables | Medium |
| 2 | Dashboard global | Missing aggregates, one relies on Settlements | Medium |
| 3 | Bills list consultant column | One serializer method field | Small |
| 4 | Payments list payer name | Two serializer fields | Small |
| 5a | Terminals bank ID | New column + migration | Small |
| 5b | Terminals collected | New FK on Payment + population logic | Medium |
| 6 | Reconciliation global unmatched | New endpoint + possibly new columns | Medium |
