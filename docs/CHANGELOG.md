# ACRev360 — Cross-Repo Changelog

**What this file is:** a single, chronological log of every change made across *both*
`ACRev360-backend-latest` and `ACRev360-frontend` (siblings under
`C:\Users\Star2knb\Documents\`), written so a fresh session — or the same session, later
— knows exactly what changed, why, and what to watch out for before touching related
code. Neither repo's own git log gives you the other repo's side of a change, and a
backend fix and its frontend consumption are usually really *one* change split across
two commits — that pairing is the main thing this file adds over `git log` alone.

**Protocol — read this before starting any new request.** Skim newest-to-oldest for
anything touching the area you're about to change, especially the **Gotchas** line in
each entry. The goal is to not re-break something that was already fixed once, not
re-litigate a deliberate decision, and not reintroduce a bug pattern that's already bitten
this codebase (several have — see the recurring themes below).

**How to add an entry:** newest entry at the top, right under this header block. Every
entry needs: what changed, *why* (the actual problem or ask, not just the mechanism),
which repo(s)/files, and a **Gotchas** line — the thing a future change could easily get
wrong or accidentally undo. If there's nothing non-obvious to warn about, say so
explicitly ("Gotchas: none") rather than omitting the line, so it's clear it wasn't
forgotten.

**Recurring themes worth knowing before you read the entries below:**
- **`common.scoping.portfolio_filter` only covers payer-shaped querysets** (bills,
  payments, payers, receipts, debt) via `enumerated_by__consultant_id`. Anything else
  that needs consultant/agent scoping — like revenue items — needs its own hand-written
  join. Two real bugs shipped from assuming otherwise.
- **A serializer field the view sets from the URL (not client input) must be
  `read_only`**, or DRF treats it as required input the client was never asked to send,
  and the endpoint 400s on every call. Bit both `ConsultantPortfolioSerializer` and (at
  first) `AgentPortfolioSerializer`.
- **`GLOBAL_VIEW` (stakeholder) access is a deliberate allow-list, not a default.** It
  currently has permission on exactly: `dashboard/summary`, `dashboard/global`
  (anonymized), `wards`, revenue-item read endpoints. It is *deliberately absent* from
  `PayerViewSet`, `BillViewSet`, `PaymentViewSet`, `ReceiptViewSet`, and
  `SubConsultantViewSet`'s list — all carry payer or consultant names. Don't add
  `GLOBAL_VIEW` to a new endpoint without checking whether it exposes a name.
- **`set_council_context()` needs an open transaction** (`SET LOCAL`, transaction-scoped).
  A one-off `manage.py shell -c "..."` without wrapping in `apps.tenancy.context.council_context(...)`
  or `transaction.atomic()` makes every RLS-protected query silently return empty, not
  error — this has produced a false "the data is gone" scare at least once. If a
  diagnostic query looks suspiciously empty, verify via `docker compose exec postgres
  psql -U acrev360 -d acrev360` (the Postgres superuser bypasses RLS entirely) before
  concluding data is actually missing.
- **This Docker setup has no bind mount** on `web`/`celery-*` — every backend source
  edit needs `docker compose build web && docker compose up -d web` before it's visible,
  and `pip install -r requirements/dev.txt` again inside the fresh container before
  `pytest` will even run.
- **`packages/api/package.json`'s `codegen` script targets the live Render deployment**,
  not local. To regenerate types against local backend changes, run
  `npx openapi-typescript http://localhost:8000/api/schema/ -o ./src/generated/schema.ts`
  from `packages/api` directly.

---

## 2026-08-20 — Search bars on list pages that get tedious with a lot of records

**Ask:** add search to list pages that could get tedious to search once a council has "a
lot of people" in them.

**Backend (`37dff0c`):** added a `q` search param (icontains, OR'd across the relevant
fields) to `get_queryset()` on:
- `SubConsultantViewSet` — matches `consultant_name`, `contract_ref`.
- `FieldAgentViewSet` — matches `agent_code`, `user__full_name`.
- `DebtCaseViewSet` (`apps/enforcement/api/views.py`) — matches `bill__bill_ref`,
  `bill__payer__full_name`.
- `ReceiptViewSet` — matches `receipt_ref`, `payment__bill__bill_ref`,
  `payment__bill__payer__full_name`.
- `AuditLogViewSet` — matches `actor__username`, `action`, `entity_type`. **Gotcha:**
  this queryset is hard-sliced to `[:300]` (last 300 events) — the `q` filter had to be
  applied *before* that slice, since Django raises if you try to filter a queryset
  that's already been sliced.
- `CommissionSettlementViewSet` (`apps/settlements/api/views.py`) — matches
  `consultant__consultant_name`.

Also (same pass): `FieldAgentSerializer` gained read-only `agent_full_name`
(`source="user.full_name"`) and `agent_phone` (`source="user.phone"`) — the existing
`full_name`/`phone` fields are write-only (used only at creation to seed the linked
`AppUser`), so there was no way to *read* an agent's name at all before this, making
"search by name" meaningless without it. `ReceiptSerializer` gained read-only
`full_name` (`source="payment.bill.payer.full_name"`) for the same reason — receipts
had no payer-identifying field to search or display.

`PayerViewSet`, `BillViewSet`, `PaymentViewSet` already had `q` support from earlier
passes — not touched here.

**Frontend (`e553f2e`):** search box added to `BillListPage.tsx` (backend `q` already
existed, this page just never had a UI for it), `ConsultantsPage.tsx`, `AgentsPage.tsx`
(also added a "Name" column using the new `agent_full_name` field, and the detail modal
title now shows the agent's name), `DebtPage.tsx`, `ReceiptsPage.tsx` (also added a
"Payer" column + KV row using the new `full_name` field), `AuditPage.tsx`,
`SettlementsPage.tsx`. `DebtPage`/`SettlementsPage`'s search box is visible to both admin
and consultant (the underlying list already is to both); the admin-only action button
(`Refresh Ageing` / `Compute Settlements`) stays in the same toolbar rather than gating
the whole thing.
- Deliberately skipped: `StakeholdersPage.tsx`, `TerminalsPage.tsx`,
  `RevenueItemsPage.tsx`, `ChannelsPage.tsx` — all low-cardinality (a handful to a few
  dozen rows realistically), doesn't fit "a lot of people."

**Pattern used everywhere:** `const [q, setQ] = useState('')`, `q` folded into the
query's `queryKey` and `params.query.q: q || undefined`, an `onSearchChange` handler
that also resets `page` to 1, a search `<input className="grow">` in the page's
`.toolbar`. Matches the existing convention from `PayerListPage.tsx`/`PaymentsPage.tsx`.

Verified live as admin: all seven search boxes narrow results correctly (spot-checked
each with a real query against the seeded demo data).

**Gotchas:** mid-pass, `ReceiptSerializer.full_name` was added to the backend *after*
the Docker image had already been rebuilt for the other five endpoints' changes — the
regenerated frontend schema silently didn't have the field until a second rebuild caught
up, surfacing as a TypeScript error (caught immediately by `tsc -b`, not by manual
testing, since nothing runtime-visible broke). If a serializer field is added in a
follow-up edit after you've already rebuilt once this session, rebuild again before
regenerating the schema — "I rebuilt earlier this session" doesn't cover a change made
after that rebuild.

---

## 2026-08-20 — Prevent duplicate active portfolio assignments (backend only)

**Bug reported live:** the same revenue item could be assigned to a consultant or agent
more than once — clicking "Assign" twice (or two racing requests) just created two open
`ConsultantPortfolio`/`AgentPortfolio` rows for the identical grant.

**Fix (`apps/revenue/models.py`, `apps/accounts/api/views.py`,
`apps/revenue/migrations/0004_...py`):**
- Partial `UniqueConstraint` on `(consultant/agent, council_revenue_item, ward)` where
  `effective_to IS NULL`, with `nulls_distinct=False`. Without that flag, two open
  grants both left at `ward=NULL` ("all wards") would *not* collide — Postgres treats
  every `NULL` as distinct from every other `NULL` in a unique constraint by default.
  This was the actual gap, not just "no constraint at all."
- A view-level pre-check in both `portfolio()` POST actions, so a duplicate gets a clean
  `400 {"error": "This item is already assigned to this X"}` instead of a raw
  `IntegrityError` 500 — there's no generic DRF exception handler in this codebase that
  turns a DB constraint violation into a nice response (see
  `apps/common/exceptions.py` — it only normalizes responses DRF's own handler already
  produced, `IntegrityError` isn't one of them).

Revoking (`effective_to` set) and reassigning the same item afterward still works —
confirmed by test — since the constraint only applies to open rows.

**Files:** `apps/revenue/models.py`, `apps/accounts/api/views.py`,
`apps/revenue/migrations/0004_agentportfolio_uniq_open_agent_portfolio_item_ward_and_more.py`,
`tests/test_accounts.py` (+3 tests). 101/101 passing.

**Gotchas:** if you add another "X may handle a subset of Y" assignment model later
(there will likely be more of these), give it the same partial-unique-with-
`nulls_distinct=False` treatment from the start rather than waiting for the same bug to
get reported again.

---

## 2026-08-20 — Revenue items weren't portfolio-scoped; consultants can now sub-assign to their agents

**Bug reported live:** a consultant could see and select revenue items nobody had
assigned them — `CouncilRevenueItemViewSet` returned the council's whole chart of
revenue to every role, with zero `ConsultantPortfolio` scoping. Silently missed by the
earlier stakeholder-permissions pass because `portfolio_filter` (the shared scoping
helper) only works for payer-shaped querysets, and nobody wrote item-specific scoping
either.

**Backend:**
- `CouncilRevenueItemViewSet.get_queryset()` (`apps/revenue/api/views.py`) now joins
  through `portfolio_entries` for `CONSULTANT` callers. `COUNCIL_ADMIN`/`GLOBAL_VIEW`
  stay unscoped — an item's existence/rate isn't identifying the way a payer or
  consultant *name* is, consistent with why `GLOBAL_VIEW` already has this endpoint.
- New `AgentPortfolio` model (`apps/revenue/models.py`, migration `0003_agentportfolio`)
  — mirrors `ConsultantPortfolio`. **Deliberately an optional further narrowing, not a
  mandatory allow-list**: an agent with zero rows here still inherits their whole
  consultant's portfolio (or the full catalog if council-direct) — this is what stopped
  the fix from instantly locking out every already-onboarded agent the moment it shipped.
  `CouncilRevenueItemViewSet` also scopes `AGENT`: their own `AgentPortfolio` if they
  have one, else their consultant's, else (council-direct) unrestricted.
- `FieldAgentViewSet` gained `portfolio`/`end_portfolio` actions (mirrors
  `SubConsultantViewSet`'s). `COUNCIL_ADMIN` manages any agent; `CONSULTANT` manages
  only their own — already enforced by `FieldAgentViewSet.get_queryset()`, so no extra
  ownership check was needed in the action itself (reaching another firm's agent 404s
  before the method body runs). Assigning validates the item is already in the agent's
  *own consultant's* active portfolio.
- Found and fixed a pre-existing, unrelated bug while building this: both
  `ConsultantPortfolioSerializer` and (initially) the new `AgentPortfolioSerializer` had
  their `consultant`/`agent` FK as required client input instead of `read_only`, even
  though the view always supplies it from the URL — every portfolio assignment through
  `SubConsultantViewSet.portfolio`'s POST branch had been silently 400ing since before
  this session, never caught because no test exercised the *success* path.

**Frontend (`AgentsPage.tsx`):** added portfolio management to the agent detail modal
(assigned items list + revoke + an "assign from consultant's portfolio" picker), mirroring
the existing Sub-Consultants portfolio UI. The picker's option list needed no filtering
of its own — `useRevenueItems()` already returns exactly the calling consultant's own
portfolio, for free, once the backend fix landed. Also fixed a regression the *previous*
entry's `GLOBAL_VIEW` permission tightening had introduced here: the "Consultant" name
column looked up names via `GET /consultants`, which is now `COUNCIL_ADMIN`-only — a
consultant viewing their own agents would have silently lost that label. Falls back to
`/auth/me`'s own `consultant_name` when the caller isn't an admin.

**Files:** `apps/revenue/models.py`, `apps/revenue/api/views.py`,
`apps/revenue/migrations/0003_agentportfolio.py`, `apps/accounts/api/serializers.py`,
`apps/accounts/api/views.py`, `tests/test_accounts.py`,
`tests/test_revenue_items_portfolio.py` (new). Frontend: `AgentsPage.tsx`. 98/98
backend tests passing.

**Gotchas:** see "recurring themes" above (both the `portfolio_filter` scope and the
read-only-FK lessons came from this entry). Also: `SubConsultantViewSet` has a plain
class-attribute `permission_classes`, *not* a `get_permissions()` method, on purpose —
a custom `get_permissions()` override here previously silently shadowed the `portfolio`/
`status_change` actions' own per-action `permission_classes`, since DRF only respects
per-action overrides through the *default* `get_permissions()` implementation. Don't
reintroduce a custom `get_permissions()` on this viewset without re-checking that.

---

## 2026-08-20 — Consultant-manager and stakeholder accounts, GLOBAL_VIEW locked to aggregates

**Ask:** "do something like [revac.onrender.com's] demo accounts" + "add a dashboard for
subconsultants and stakeholders (stakeholder should be read only, mostly general
information, no names of payers or subconsultants)".

**Backend:**
- `SubConsultantViewSet.perform_create()` can now optionally create a linked manager
  login alongside the firm record (`manager_username`/`manager_password`/
  `manager_full_name` on `POST /consultants`) — mirrors `FieldAgentViewSet`'s existing
  agent-login-creation pattern. Gave a consultant read access to their own portfolio via
  the `portfolio` action (see the `get_permissions()`-shadowing gotcha above — this is
  where that got fixed).
- New `StakeholderViewSet` (`COUNCIL_ADMIN`-only both ways) creates read-only
  `GLOBAL_VIEW` accounts. `MeSerializer` gained `consultant_name`/
  `consultant_commission_rate`/`consultant_status` (denormalized, since a consultant
  can't list `SubConsultantViewSet` to look their own firm up).
- **`GLOBAL_VIEW` removed from `PayerViewSet`, `BillViewSet`, `PaymentViewSet`,
  `ReceiptViewSet`, `SubConsultantViewSet`'s list** — all five carry payer or consultant
  names. `DashboardGlobalView`'s `by_consultant` breakdown collapses into an anonymous
  `Council Direct` / `Via Sub-Consultants` split for this role instead of named
  per-consultant rows.
- `seed_demo_data` creates `consultant1` (linked to the first seeded firm, "Zenith
  Revenue Partners") and `stakeholder` demo logins, password `acrev360-2026` for both
  — printed in the command's own sign-in summary at the end of a run.

**Frontend:**
- Login page: a "Demonstration accounts" quick-login section (admin/consultant1/
  stakeholder), matching the old prototype's UX. `login()` (`packages/api/src/auth.ts`)
  now rejects `AGENT`-role accounts with a message pointing at the not-yet-built mobile
  app, instead of silently dropping them into a portal with an empty nav.
  Dashboard shows a consultant's own identity (name/rate/status) above their
  portfolio-scoped figures. `nav.ts`'s `GLOBAL_VIEW` case drops the "Sub-Consultants"
  link (that list is now admin-only). New `StakeholdersPage.tsx` (list + create,
  admin-gated) + nav entry.
- **Real bug found and fixed while verifying this live:** switching accounts in the same
  browser tab without a reload could leave a *previous, more-permissive* user's cached
  list data visible on screen even though the new role's refetch correctly 403'd —
  TanStack Query keeps the last-good data on a failed background refetch by default.
  `AuthContext.tsx`'s `login()` and `logout()` both now call `queryClient.clear()`.

**Files:** backend — `apps/accounts/api/serializers.py`, `apps/accounts/api/views.py`,
`apps/accounts/api/urls.py`, `apps/common/api/dashboard.py`, `apps/billing/api/views.py`,
`apps/payments/api/views.py`, `apps/registry/api/views.py`,
`apps/tenancy/management/commands/seed_demo_data.py`, `tests/test_dashboard.py`,
`tests/test_accounts.py` (new). Frontend — `LoginPage.tsx`, `packages/api/src/auth.ts`,
`packages/ui/src/components/LoginLayout.css`, `DashboardPage.tsx`, `nav.ts`,
`ConsultantsPage.tsx`, `StakeholdersPage.tsx` (new), `App.tsx`, `ProtectedLayout.tsx`,
`AuthContext.tsx`. 83/83 backend tests passing.

**Gotchas:** see "recurring themes" — the `GLOBAL_VIEW` allow-list and the
`queryClient.clear()`-on-identity-switch pattern both came from this entry. Any new page
or query that caches list data should assume it needs to survive an account switch
without a reload; it will, as long as nothing bypasses the shared `queryClient`.

---

## Prior work (condensed — see each repo's own commit history for full detail)

This session's earlier work, before this file existed. Full detail lives in
`git log` in each repo (commit messages were already written in this same
what/why/gotchas style) — condensed here just enough to orient a new reader.

- **Original backend build** (`52cc1c1`, 2026-08-15): Django/DRF/Postgres rewrite of the
  old Flask/SQLite prototype (the "Test prod" repo — a *third*, unrelated-history sibling
  at `C:\Users\Star2knb\Documents\Test prod`, source of the old design system and demo
  data patterns). Full core revenue cycle, RLS multi-tenancy, JWT auth. Built by a
  separate collaborator (git author "Scaper20") against an architecture doc drafted in
  the "Test prod" session. Docker/deploy fixes and Render blueprint followed
  (`b088281`, `c14bf16`, `27894cd`).
- **Frontend scaffold** (`4fd5172`, 2026-08-17): full portal, design system ported from
  the old prototype's `frontend/styles.css`, typed API client generated from the live
  backend schema with 6 hand-verified schema-vs-runtime corrections
  (`packages/api/src/overrides.ts`). RLS-bypass bug found and fixed while setting up
  local Docker (`docker-compose`'s `POSTGRES_USER` becomes cluster superuser, which
  bypasses RLS regardless of `FORCE ROW LEVEL SECURITY` — see `b38d760`).
- **Gazette rate bands** (`a44ce51`, `1545570` backend; `ff9806e` frontend,
  2026-08-19): `RateBand`/`RateTier` models for revenue items priced by a
  sub-classified schedule (min/max ranges, small/medium/large-style tiers) instead of
  one flat rate, sourced from `docs/reference/KAC Gazette.xlsx` and a cleaner per-bye-law
  split. **Deliberately not seeded** (ambiguous or self-contradictory source data, not
  guessed at): Tenement Rate Collection, Regulated Premises, Foodstuff Regulation — don't
  attempt these without the council's actual bye-law text in hand.
- **Backend handoff PR + demo data** (`6936e18`/`5642abc` backend, `e795cf0` frontend;
  `7f8ed0d` backend, all 2026-08-19): richer dashboard aggregates, new list columns,
  reconciliation exceptions view, and `seed_demo_data` — ~100 payers/bills/payments/
  agents/consultants/terminals through the real service functions (not raw ORM writes),
  populating every previously-empty screen for realistic manual testing.
- **Payer/bill/payment audit fix** (`2f39c88` backend, `a8f1f6a` frontend, 2026-08-19):
  a full read-only audit of payer registry/billing/payments followed by fixes for all of
  it — banded-item registration 500, negative amount/quantity validation, payment
  reversal, KYC status transitions, guarded payer/bill delete (409 when financial history
  exists), pagination on every list page that could exceed 50 rows, stale flat-price
  labels on banded items, band/tier labels on print documents. The delete-modal
  close-before-invalidate ordering pattern (see recurring themes) originates here.

---
