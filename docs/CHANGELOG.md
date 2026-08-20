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
- **`docker compose up -d web` can stall at "Created" (never reaches "Started"/"Up"),
  and this is a genuinely stuck host-level process, not just a slow one.** Confirmed via
  `Get-Process` (PowerShell) — a timed-out/backgrounded `docker compose up` leaves a real
  `docker`/`docker-compose` process still running on the host, and it does *not* die on
  its own; re-running the same command just piles up *another* stuck process contending
  for the same container, making it worse. The container itself shows no logs at all
  while stuck (the entrypoint never starts), even though `docker info`/`docker ps`
  (no filter) respond instantly — it's specific to that one container's start, not a
  general daemon hang. **Reliable fix, in order:** (1) kill any stuck `docker`/
  `docker-compose` processes first (`Get-Process | Where-Object ProcessName -in
  'docker','docker-compose'`, then `Stop-Process -Force` the ones matching your stuck
  attempts — leave `com.docker.*`/`Docker Desktop`/`docker-agent` alone, those are Docker
  Desktop's own infra); (2) `docker compose rm -f web` (fast — removes the stuck
  "Created" container cleanly); (3) `docker compose up -d web` OR, if that *also* hangs,
  `docker start <container-name>` directly (bypasses compose's orchestration layer
  entirely and has been the most reliable single step). None of this touches
  postgres/redis or any data — `web` is stateless, safe to kill/recreate freely.
- **A serializer field documented as one schema can actually return a different one at
  runtime** when the view's `create()` manually returns a different serializer than
  `get_serializer_class()` (used for request validation) implies — drf-spectacular infers
  the response from the latter. `POST /payments` is the known case (documented as
  `PostPayment`, actually returns a full `Payment`) — see
  `packages/api/src/overrides.ts`'s `PaymentRecord` for the fix. If a new endpoint's
  response looks like it's typed as its own request body, this is almost certainly why —
  check `overrides.ts` for an existing fix before adding a new manual cast.
- **An uncaught `IntegrityError` from a model-level `UniqueConstraint` isn't a DRF
  `APIException`**, so it skips `apps.common.exceptions.acrev360_exception_handler`
  entirely and falls through to Django's own generic 500 — no detail to the client, and
  with `DEBUG=False` and no `LOGGING` override (this project's actual Docker setup, despite
  `.env`'s `DJANGO_DEBUG=True` — `config.settings.prod` hardcodes `DEBUG = False`,
  silently overriding it), nothing in the server logs either. Has now bitten twice
  (duplicate `ConsultantPortfolio`/`AgentPortfolio` grant, duplicate `SubConsultant.
  contract_ref`) — any new `UniqueConstraint`-backed create path needs its own
  pre-check-and-400 in the view, same pattern both fixes used. Don't rely on the
  constraint itself to produce a usable error.
- **Prefer a nested serializer field over a `SerializerMethodField` whenever the value
  comes straight off a related manager or model attribute** (`superseded_bills =
  SupersededBillSerializer(source="supersedes", many=True)`, not a method that hand-builds
  dicts and returns them). A method field returning a plain dict skips two things at
  once: drf-spectacular can't infer its shape without an explicit `@extend_schema_field`
  hint (types as a bare `string`/`object` in the generated schema), and building the
  response by hand skips the nested serializer's own field formatting — a `Decimal`
  comes out as a raw float instead of DRF's usual `"10000.00"` string. The declarative
  nested-field form gets both right for free, and `many=True` over a related manager
  calls `.all()` automatically, same as `lines` already does elsewhere in this file.

---

## 2026-08-20 — Payer bill history + arrears-consolidation breakdown

**Ask:** two things the user found confusing while poking at the app after the QA run —
"Enumerated Revenue Items — not yet billed (0)" always showing 0 on the Payer Registry
detail view, and whether the arrears checkbox actually consolidates a payer's prior
bills the way it's supposed to.

**Turned out to be:** the "not yet billed" section was working exactly as designed
(draft assessments awaiting a bill, not a payer's actual bills — `issue_bill()` bills a
draft in the same call it creates it when given explicit lines, so there's never a
lingering one to show for a payer billed the normal way). The arrears logic was also
already fully correct server-side and already showed a total in the UI/print. But both
pointed at a real, precise gap each: no way to see a payer's *actual* bill history from
their own record, and no way to see *which* prior bills a consolidation total came from.

**Fixed:**
- `PayerDetailModal` gained a "Bills" section — sourced from the same `GET /bills?payer=`
  filter the Bills page itself already uses, so no new backend endpoint was needed.
- New `superseded_bills` field on `BillDetailSerializer` and `PublicBillLookupSerializer`
  (`Bill.supersedes`, the reverse of `superseded_by`, already had this data — it just
  wasn't exposed). `BillDetailModal` and both print documents (Demand Notice, Demand
  Bill) now show one line per consolidated bill instead of a single lump total.
- **Gotcha for next time:** the first pass at `superseded_bills` used a
  `SerializerMethodField` returning hand-built dicts. Two bugs from that, both worth
  remembering — see the new recurring-theme note above this entry for the general
  pattern, but concretely here: (a) drf-spectacular typed it as a bare `string` in the
  schema, since a method field's return shape isn't inferrable without an explicit hint;
  (b) the hand-built dict's `Decimal` amount serialized as a raw float (`10000.0`) instead
  of DRF's usual formatted string (`"10000.00"`), since skipping the serializer skips its
  field-level formatting too — a real correctness bug, not just a typing one, caught by a
  test asserting the exact string. Fixed by using `SupersededBillSerializer` as a direct
  nested field (`source="supersedes"`) instead, same as `lines` already does — nested
  declarative fields over a related manager get `.all()` called automatically and go
  through proper field serialization; hand-built response dicts get neither.
- Found and fixed live while building the payer Bills section: a `SUPERSEDED` bill's
  `balance` is frozen (`post_payment()` refuses any further payment against it), but the
  list was showing it as "owing" anyway — double-counting against whatever bill it got
  rolled into. Balance/"owing" now only shows for bills still in a non-terminal status.

**Files:** backend — `apps/billing/api/serializers.py`, `apps/billing/api/views.py`,
`tests/test_money_invariants.py` (+2 tests). 128/128 backend tests passing. Frontend —
`apps/portal/src/routes/payers/PayerDetailModal.tsx`,
`apps/portal/src/routes/bills/BillDetailModal.tsx`,
`apps/portal/src/routes/print/DemandNoticePrint.tsx`,
`apps/portal/src/routes/print/DemandBillPrint.tsx`. Verified live: consolidated two real
bills from the QA-run data (Danjuma Ventures, ₦200,000 + ₦100,000 → one ₦300,000 bill),
confirmed the breakdown renders correctly in the detail modal, the printed Demand
Notice, and the printed Demand Bill.

**Gotchas:** none beyond the SerializerMethodField one above.

---

## 2026-08-20 — Live QA test workflow: consultant → agents → payers → bills

**Ask:** "perform this test workflow... note down any problems, bugs, or things that
could be optimized" — a full onboarding-to-billing scenario run through the *real*
portal UI (not scripted around it): admin onboards a consultant with 5 revenue items
(2 flat, 3 multi-band, deliberately covering all three `RateBand.rate_mode`s — FLAT/
RANGE/TIERED — between them), log in as that consultant, onboard 5 agents, one revenue
item per agent, then 10 payers × 2 bills (20 bills) per agent — 50 payers / 100 bills
total, with the 3 banded agents' bills deliberately cycling through their item's
different bands/tiers rather than hammering the same one 20 times.

**What this actually exercised, and how:** consultant onboarding, status-change,
portfolio-assignment, agent onboarding, and agent-portfolio-assignment were all done
*live through the browser* against `localhost:5173` (admin, then re-logged-in as the
new consultant's manager account) — this is where the real UI/UX bugs below were found.
The 50-payer/100-bill volume was generated by a script calling `create_payer()`/
`issue_bill()` directly (`apps/fieldops`-style — real service functions, not raw ORM
writes or a bypassed shortcut) rather than 150+ individual browser interactions; verified
afterward both at the DB level (amounts, band/tier attribution) and live in the portal
(bill list + detail modal correctly show band label alone for RANGE, `band — tier` for
TIERED, matching what was priced).

**Bug found and fixed:** duplicate `contract_ref` on consultant onboarding crashed with
a raw 500 (`6a1b000` — see the new recurring-theme note above; this is the same
IntegrityError-isn't-an-APIException class as the duplicate-portfolio-assignment bug).
Diagnosing it took real effort specifically *because* of the logging gap described in
that same theme — reproducing it needed `manage.py shell` bisection (binary-searching
which of three fields was the actual collision) since neither the response nor the
server logs gave any signal at all.

**Infrastructure problems found (not app bugs, but cost real time):**
- The `web` container's host port publish (`8000:8000`) silently dropped at some point
  after enough rebuild/restart cycles in this session — container showed "Up" and fully
  healthy internally, but `docker port`/`netstat` showed nothing on the host side at all.
- `docker compose up -d web` got stuck badly enough this session to need actual process
  cleanup, not just a retry — see the rewritten recurring-theme note above for the full
  diagnosis and fix sequence (kill stuck host processes → `rm -f` → `up`/`docker start`).

**What worked well, worth noting alongside the problems:** zero pricing bugs across
100 bills spanning all three band modes — every FLAT/RANGE/TIERED amount matched its
source `RateBand`/`RateTier` exactly, including `RANGE`'s `amount_override` bounds
checking. The agent portfolio-assignment picker correctly scoped to just the calling
consultant's own 5 items (not the council's full ~30-item catalog) with no extra work
needed — confirms the `AgentPortfolio`/`ConsultantPortfolio` scoping from earlier in this
session is holding up under a fresh, independent scenario. Status changes (PENDING→
ACTIVE) apply on `<select>` `onChange` with no separate "save" step — good, low-friction
UX, not something to "fix" toward a more conventional save button.

**Optimization opportunities (product-level, not filed as bugs):** there is no bulk/CSV
import path anywhere in the app for payer registration — every payer is one modal at a
time. Fine for a field agent registering people they're actually meeting, but a real
council's *initial* onboarding (an existing paper/spreadsheet tax roll) would hit this
wall immediately. Worth a deliberate decision on whether that's in scope, not something
to build speculatively.

**Files:** `apps/accounts/api/views.py`, `tests/test_accounts.py` (+1 test). 126/126
backend tests passing. No frontend files changed this pass — every UI issue found was
either the contract_ref 500 (backend-only fix) or infrastructure, not a rendering bug.

**Gotchas:** none beyond what's already captured in the two recurring-theme rewrites
above — read those before touching consultant onboarding or fighting a stuck `web`
container again.

---

## 2026-08-20 — Agent mobile app: apps/fieldops (backend) + apps/field (PWA)

**Ask:** "agent mobile app now" — after a feature-list pass agreed the scope (ward-scoped
worklist, payment collection, payer registration, offline queue+sync, status/tally,
installable PWA), porting the old Flask prototype's `mobile/` app onto the current
multi-council backend and the current React/TS frontend stack instead of rebuilding it
as more vanilla JS.

**Backend — new `apps/fieldops` app:**
- `MobileSyncRecord` model — the idempotency ledger for offline sync. Keyed on the
  client-generated `client_id` (`UniqueConstraint(council, client_id)`); every outcome
  (ACCEPTED/CONFLICT/REJECTED) is stored once and a retried `client_id` short-circuits to
  the stored result rather than reprocessing — standard idempotency-key semantics. A
  CONFLICT/REJECTED record doesn't get retried under the same key; a correction needs a
  new `client_id`, same as a fresh record.
- `GET /api/v1/mobile/worklist` — ward-scoped `Payer` list (agent's own
  `assigned_ward`), annotated with `outstanding` (summed non-terminal bill balance),
  ordered desc. Deliberately **not** portfolio-scoped by revenue item — a payer owing on
  a mix of items still belongs on the worklist so the agent can collect against whichever
  they're assigned to; item scoping already happens correctly at the point that matters
  (`GET /revenue-items`, via `CouncilRevenueItemViewSet.get_queryset()`'s existing AGENT
  branch — this endpoint doesn't duplicate that). An agent with no `assigned_ward` gets an
  empty list, not the whole council — an unset ward reads as a setup gap, not "see
  everyone".
- `POST /api/v1/mobile/sync` — batch-replays queued `PAYMENT`/`PAYER` records through
  `payments.services.post_payment()` / `registry.services.create_payer()`, the *same*
  functions the online path uses (`post_payment()`'s own docstring already listed
  "offline sync replay" as a path it exists to cover). A `PAYER` record's `geo` key isn't
  a `CreatePayerSerializer` field (`Payer` has no geo columns — `EnumeratedAsset` does) —
  `_replay_payer` splits it out before validation and creates a `PREMISES`
  `EnumeratedAsset` afterward if present. Response shape
  (`{accepted:[], conflicts:[], rejected:[]}`, each row `{client_id, result_ref, detail}`)
  deliberately matches the old prototype's own `/api/mobile/sync` so the offline-queue
  logic's shape ports with minimal translation.
- `MeSerializer` gained `agent_id`/`agent_code`/`assigned_ward_id`/`assigned_ward_name`
  (denormalized from the reverse-OneToOne `field_agent`, same `default=None` pattern as
  the existing `consultant_*` fields) — the mobile app's login/header/status views all
  read agent identity from `/auth/me` rather than a dedicated endpoint.
- `FieldAgentViewSet.activity` (existing action — its own docstring already flagged
  "fieldops" as where this belonged) widened to `permission_classes=[COUNCIL_ADMIN,
  CONSULTANT, AGENT]` plus a body-level ownership check, so an agent can read their own
  today's-collections tally; `get_queryset()` now also scopes `AGENT` to `user_id=self`
  (defense in depth — the ownership check alone would have been sufficient, but matches
  how `CONSULTANT` is already scoped there).
- `PaymentSerializer` gained `receipt_ref`/`qr_token` (denormalized from the 1:1
  `Payment.receipt`) — the live-collection receipt screen needs the real verification
  token, and the response schema was already wrong about what `POST /payments` returns
  (see the new recurring-theme note above), so this was the natural place to also close
  that gap for this specific caller rather than adding a second round trip.
- Deliberately **not built**: a precomputed `agent_daily_return` rollup table (the old
  prototype had one, for its own SQLite's sake) — this backend already computes
  `today_total` *live* in `activity` via `Payment.objects.filter(...).aggregate(Sum(...))`,
  and nothing else in this codebase uses a precomputed rollup for the same shape of data,
  so adding one here would've been an unrequested extra abstraction with a sync-drift risk
  the live query doesn't have.

**Frontend — new `apps/field`:** login (rejects non-AGENT, inverse of the portal's own
rule), `WorklistView` (search, cached to `localStorage` for offline render),
`CollectView` (bill→channel→amount, live `POST /payments` when reachable, silently
queues on failure — a genuine server rejection, e.g. terminal-state refusal, is shown as
an error instead of queued), `RegisterView` (individual/business, GPS via
`navigator.geolocation`, revenue-item checklist filtered to flat-rate items only —
banded items need a bill screen this app doesn't have), `ReceiptView` (client-rendered
QR, ported byte-for-byte from the old prototype's hash-seeded algorithm), `StatusView`
(today's tally, queue state, manual sync). Offline queue (`lib/offlineQueue.ts`) is
`localStorage`-backed, keyed by `crypto.randomUUID()` `client_id`s, auto-syncs on the
`online` event, and keeps CONFLICT/REJECTED items visible (tagged, dismissible) rather
than silently dropping them — only ACCEPTED items are removed. PWA: hand-written
runtime-caching `sw.js` (not a fixed precache list — Vite's build output is
content-hashed, so there's no fixed filename list to precache correctly across deploys)
+ `manifest.json` with inline-SVG icons.

**Shared package changes** (both apps import `@acrev360/api`/`@acrev360/ui`):
- `auth-store.ts`'s refresh-token storage is now swappable via `configureAuthStorage()` —
  field calls it with `localStorage` before anything else runs (an installed PWA can be
  backgrounded/killed by the OS well before a same-tab reload would happen; portal's
  `sessionStorage` default is untouched, it never calls this).
- `auth.ts`'s `login()` is now role-agnostic — it used to hard-reject `AGENT` (added when
  `access_level` lockout first shipped, back when only the portal existed). That rule
  moved to the portal's own `AuthContext.tsx` call site; field's `AuthContext.tsx` has the
  literal inverse (rejects everything *except* `AGENT`) at its own call site. Caught
  *before* it shipped broken — would have made every agent login fail immediately.
- `revenue-items.ts` (new) — the banded-item-exclusion grouping logic, extracted from
  `apps/portal/lib/revenueItems.tsx` since `apps/field` needed the identical rule
  (flat-rate-only checklist) and apps can't import each other's `src/`. Pure data
  shaping only (no `@acrev360/ui` dependency); portal's own module now wraps it with
  `money()` formatting and JSX `render` nodes.

**Also fixed while verifying this pass's own build:** the root `package.json`'s `build`
script referenced a `build` script that doesn't exist in `packages/ui`/`packages/api` —
both are consumed as TS source via project references, never had a separate build step,
so this had never actually been run end-to-end before. Now just builds both apps
directly.

**Demo login:** `seed_demo_data` already seeds `agent01`..`agent08` (password
`acrev360-2026`, real wards) — this was already anticipated and printed in the seed
command's own sign-in summary, just waiting for this app to exist. `LoginScreen.tsx`
gained a "Try the demo agent account" quick-login using `agent01`, matching the portal's
own demo-account pattern.

**Files:** backend — `apps/fieldops/*` (new), `apps/accounts/api/serializers.py`,
`apps/accounts/api/views.py`, `apps/payments/api/serializers.py`, `config/api_urls.py`,
`config/settings/base.py`, `tests/test_fieldops.py` (new), `tests/test_accounts.py`.
123/123 backend tests passing. Frontend — `apps/field/*` (new),
`packages/api/src/auth-store.ts`, `packages/api/src/auth.ts`,
`packages/api/src/revenue-items.ts` (new), `packages/api/src/index.ts`,
`apps/portal/src/auth/AuthContext.tsx`, `apps/portal/src/lib/revenueItems.tsx`,
`package.json`. `tsc -b`, `vite build` and `vitest` all clean for every workspace.

Verified live end-to-end against the local backend + seeded demo data: `agent01`
login → ward-scoped worklist → collect a live payment → real receipt with working
QR/ref → go offline → register a payer (queued, no network call) → back online →
auto-sync fires → payer confirmed created server-side via a fresh RLS-scoped shell
query → Status view's tally and "last synced" both correct. Zero console errors
throughout.

**Gotchas:** see the two new recurring-theme entries above (the `docker compose up -d`
backgrounding stall, and the response-schema-vs-request-schema mismatch pattern) — both
came from this pass. Also: if you add a third app to this monorepo that needs
`@acrev360/api`'s auth, remember `configureAuthStorage()` must run before the app's first
API call (call it at the very top of `main.tsx`, before anything renders) — it's not
retroactive once `authStore` has already been read from.

---

## 2026-08-20 — Audit pass on the agent mobile app (fieldops + apps/field)

**Ask:** explicit — "have you done an audit on it? is there anything missing?" — after
the previous entry shipped the app verified only by tests plus one live happy-path
walkthrough, not a dedicated audit the way the payer/bill/payment work got one.

**Fixed (backend, `apps/fieldops/services.py`):**
- **Sync race condition.** Two sync requests racing on the same `client_id` (the flaky-
  connection retry this whole app exists for) could both pass the "does a
  `MobileSyncRecord` exist yet" check before either committed, both call
  `post_payment()`/`create_payer()`, and only collide at the unique constraint on
  `.create()` — an uncaught `IntegrityError` that would 500 the *entire batch*, including
  unrelated records. Fixed with a nested `atomic()` savepoint around just the `.create()`;
  the loser's side effects roll back to that savepoint and it returns the winner's actual
  outcome instead of crashing.
- **Ward scoping on the sync-replay path.** `_replay_payment`/`_replay_payer` checked
  bill/payer validity but not that the target was in the agent's own ward — a crafted
  sync payload (not anything the honest frontend sends, but nothing stopped a
  tampered client) could pay or register into any ward in the council. Now rejected,
  mirroring `get_worklist()`'s own ward scoping exactly.
- **Broad per-record exception handling.** `_replay_payment`/`_replay_payer` only
  anticipated specific failures (`PaymentRejected`, `DuplicatePayer`, `BillingError`) — a
  malformed record from genuinely corrupted local storage (again, the scenario this app
  is built to survive) would propagate uncaught and 500 the whole batch. Now caught
  broadly per-record, turned into `REJECTED` with the exception message.

**Fixed (frontend, `apps/field`):**
- `RegisterView`'s follow-up `POST /assets` call (attaching captured GPS to a
  newly-created payer) had its response completely unchecked — a failure there was
  indistinguishable from success, silently losing the GPS tag. `ReceiptView` gained an
  optional `warning` line for exactly this shape of problem: the primary action already
  succeeded, a secondary one didn't, and that shouldn't look like either a full failure
  or a full success.
- The revenue-item checklist failing to load (typically: offline before ever fetching it
  this session) rendered as a silently empty checklist — indistinguishable from "this
  payer owes nothing." Now shows an explicit notice instead.

**Flagged, not fixed — needs a decision, not just code:** `AGENT` role can call
`POST /api/v1/payments` and `POST /api/v1/payers` **directly** (not through fieldops at
all) against any bill/ward in the council — this permission predates fieldops (added
when agent accounts first got direct API access) and is shared with `COUNCIL_ADMIN`/
`CONSULTANT`, for whom "any bill/ward" is correct and intended. The same ward check just
added to the *sync-replay* path doesn't cover this *live* path, since fixing it means
touching `PaymentViewSet`/`PayerViewSet` behavior other roles rely on, not just new
fieldops code. Worth a deliberate decision (agent-specific branch in those shared
viewsets?) rather than a silent change during an audit of something else.

**Known, low-priority, not addressed this pass:** no automated test coverage for the
React views themselves (`offlineQueue.ts`'s logic is tested; `CollectView`/
`RegisterView`/`WorklistView`/`StatusView` are only covered by manual verification) — no
Playwright suite for `apps/field` the way `apps/portal` has one. `sw.js`'s `CACHE_NAME`
never gets bumped across deploys, so old deploys' hashed assets accumulate in the cache
indefinitely rather than being cleaned up (not a correctness bug — Vite's content-hashed
filenames mean a stale cache entry is never served for new content — just unbounded
growth over many deploys). The mobile app has no way to view past receipts/collection
history (matches the old prototype's own scope, not a regression, but a real agent would
likely want it).

**Files:** backend — `apps/fieldops/services.py`, `tests/test_fieldops.py` (+2 tests:
ward-mismatch rejection for both PAYMENT and PAYER). 125/125 backend tests passing.
Frontend — `apps/field/src/views/RegisterView.tsx`, `apps/field/src/views/ReceiptView.tsx`.
`tsc -b`, `vite build`, `vitest` all clean.

**Gotchas:** the race-condition fix is reasoned-correct (nested-atomic-plus-catch is a
standard Django idiom) but not covered by an automated concurrency test — simulating true
concurrency reliably in this test suite would need threading or mocking disproportionate
to the value here. If this class of bug ever recurs, that's the gap to close.

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
