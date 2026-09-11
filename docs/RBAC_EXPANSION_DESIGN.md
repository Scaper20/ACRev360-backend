# RBAC / RLS Expansion — Implementation Spec

**Status:** Implemented 2026-09-11. This is the concrete mapping from
`docs/acrev360-roles-permissions-matrix.md` (a planning draft, not
implementable as written) onto real `AppRole.access_level` values, models,
and permission wiring. Read this file, not the matrix doc, to find out what
a given role can actually do in code.

## Design principles

1. **No RLS bypass, ever.** Every table stays `FORCE ROW LEVEL SECURITY`.
   Platform-wide roles (ACDSL tier) get their cross-council visibility by
   looping over councils in each one's own RLS context — the same proven
   pattern `apps.tenancy.context.find_across_active_councils` already uses
   for anonymous receipt lookup, and `migration_helpers.for_each_council`
   uses for data migrations. No new "superuser policy clause" was added to
   any RLS policy. See `apps/common/platform_scope.py`.
2. **No `company_id` column.** There is exactly one platform operator
   (ACDSL). `AppUser.council = null` already means "platform-level account"
   per the existing docstring — that's reused as-is rather than adding a
   redundant single-valued tenant column.
3. **Named roles vs. access levels stay separate**, per the existing
   convention (`AppRole.name` is the job title, `access_level` is the
   permission bucket several names can share — e.g. `COUNCIL_ADMIN` and
   `HEAD_REVENUE` both already carry the `COUNCIL_ADMIN` access level).
   Several matrix roles collapse onto one access level below where their
   permission boundary is genuinely identical; where the matrix describes a
   materially different boundary, it gets its own access level.
4. **Least privilege, and no speculative endpoints.** A role only gets
   wired into an existing viewset where the matrix describes a concrete
   permission a real endpoint already serves. Two matrix items are
   deliberately **not** built as new features here because they're their
   own epics, not an RLS/permissions concern:
   - ACDSL's own commercial revenue-share/invoice tracking of councils
     (Finance/Billing Admin's main duty per the matrix) — the *role* and
     *login* exist; the *invoicing feature* doesn't. Building it would mean
     inventing a second billing domain with no spec beyond one bullet point.
   - Ratepayer self-service **payments** (making a payment as a ratepayer,
     vs. viewing your own bills/payments) — needs a real payment-gateway
     decision that's out of scope here. Read-only self-service is built;
     paying isn't.
   Both are flagged in the matrix doc itself as "figures to be validated" —
   deferring them isn't scope-cutting the ask, it's not building undesigned
   features on spec.
5. **Payment channel / system accounts** (matrix Decision #5) — already
   fully implemented as `apps.payments.models.APIClient` (a service-account
   table, HMAC secret, scoped `scopes` JSON field, no human login). Nothing
   new needed here; the matrix's "settled decision" was already shipped
   before this doc existed.

## Access levels

`AppRole.ACCESS_LEVEL_CHOICES` (all ≤16 chars — the column's max_length):

| access_level | Tier | council_id | Matrix role(s) it covers |
|---|---|---|---|
| `SUPER_ADMIN` | ACDSL | null | Super Admin |
| `PLATFORM_ADMIN` | ACDSL | null | Platform/System Admin |
| `DEVOPS_ADMIN` | ACDSL | null | Technical/DevOps Admin |
| `BD_VIEW` | ACDSL | null | Business Development/Account Manager |
| `COMPLIANCE_VIEW` | ACDSL | null | Legal/Compliance Officer, Oversight/Regulatory Body |
| `FINANCE_ADMIN` | ACDSL | null | Finance/Billing Admin (ACDSL's own commercial side) |
| `SUPPORT_ADMIN` | ACDSL | null | Support/Helpdesk |
| `ANALYTICS_VIEW` | ACDSL | null | Data Analyst/BI Viewer |
| `EXTERNAL_AUDITOR` | ACDSL, time-boxed | null + `CouncilGrant` | External/Regulatory Auditor |
| `COUNCIL_ADMIN` *(existing)* | Council | set | Council Administrator, Head of Service |
| `COUNCIL_IGR_HEAD` | Council | set | Council IGR Department Head |
| `COUNCIL_TREASURY` | Council | set | Council Finance/Treasury Officer |
| `REVENUE_OFFICER` *(existing)* | Council | set | Revenue Supervisor/Officer |
| `COUNCIL_AUDITOR` | Council | set | Council Internal Auditor |
| `COUNCIL_IT` | Council | set | Council IT/Data Officer |
| `CONSULTANT` *(existing)* | Consultant | set | Sub-Consultant Firm Admin |
| `CONSULTANT_STAFF` | Consultant | set | Sub-Consultant Field Staff/Analyst |
| `AGENT` *(existing)* | Field | set | Field Agent/Collector |
| `AGENT_SUPERVISOR` | Field | set | Field Agent Supervisor/Team Lead |
| `RATEPAYER` | Ratepayer | set | Individual/Corporate Ratepayer |
| `RATEPAYER_PROXY` | Ratepayer | set | Ratepayer Proxy |
| `GLOBAL_VIEW` *(existing)* | Council or null | either | Stakeholder (per-council), Shareholder/Board (null = all-council aggregate) |

"Lead Revenue Technology Consultant" (ACDSL's own cross-council consultant
role) is **not** a new access level — it's exactly what `SUPER_ADMIN`/
`PLATFORM_ADMIN` already cover cross-council; a separate bucket would be a
distinction without a permission difference. "POS/Terminal Operator, if
distinct from field agent" is explicitly hedged in the matrix itself and
has no separate login flow today (`POSTerminal` is a device record, not a
user) — not built, for the same reason.

## New models / fields

- `apps.accounts.models.CouncilGrant.expires_at` (nullable) — makes the
  existing (previously unenforced) grant table support the matrix's
  "time-boxed/expiring access grant" requirement for external auditors.
  `apps.common.platform_scope.granted_council_ids(user)` filters out
  expired grants.
- `apps.registry.models.Payer.user` — nullable `OneToOneField(AppUser,
  related_name="payer_profile")`, the ratepayer's own login, mirroring
  `FieldAgent.user`'s existing pattern exactly.
- `apps.registry.models.PayerDelegation` — new `CouncilScopedModel` (RLS
  policy in the same migration that creates it, per convention): `payer`
  FK, `proxy_user` FK(AppUser), `granted_by` FK(AppUser), `granted_at`,
  `revoked_at` (nullable — null = active). Implements matrix Decision #2
  verbatim: "proxy has own login, ratepayer explicitly grants/revokes
  access per account."
- `AppTokenObtainPairSerializer.get_token()` gains a `payer_id` claim
  (parallel to the existing `consultant_id` claim) for ratepayer/proxy
  logins.

## Cross-council read helper — `apps/common/platform_scope.py`

`platform_wide_queryset(model_cls, user, *, council_field="council_id")`
resolves which councils a platform-tier user may read (all active councils
for `SUPER_ADMIN`/`PLATFORM_ADMIN`/`BD_VIEW`/`COMPLIANCE_VIEW`/
`ANALYTICS_VIEW`/`FINANCE_ADMIN`; only non-expired `CouncilGrant` rows for
`EXTERNAL_AUDITOR`; the caller's own council for anyone else), then unions
one filtered queryset per council, evaluated inside that council's own RLS
context via `council_context()`. This never sets `app.council_id` to a
value the requesting user isn't entitled to — it makes N narrow, correctly-
scoped queries rather than one wide unscoped one.

## Second-layer (post-RLS) scoping — `apps/common/scoping.py`

- `CONSULTANT_STAFF` added to `_PORTFOLIO_SCOPED_LEVELS` — same portfolio
  visibility as `CONSULTANT`, enforced read-only purely by which endpoints
  it's granted in (see below), not by a different queryset shape.
- New `AGENT_SUPERVISOR` branch: scoped to payers/bills/etc. whose
  registering or assigned agent's `FieldAgent.assigned_ward_id` matches the
  supervisor's own `FieldAgent.assigned_ward_id` — "own zone/team only," per
  matrix Decision #4. A supervisor with no `FieldAgent` profile (shouldn't
  happen — provisioning always creates one) sees nothing, fail-closed.

## Ratepayer self-service — `apps/registry/api/`

New `RatepayerPortalViewSet` (`/api/registry/my/`), permission class
`IsRatepayerOrDelegate` (`apps/common/permissions.py`):
- `bills/`, `payments/`, `receipts/` — read-only, scoped to
  `accessible_payer_ids(request.user)` = the caller's own `payer_profile_id`
  plus every payer with an active (`revoked_at is null`) `PayerDelegation`
  naming them as `proxy_user`.
- `delegations/` — `RATEPAYER` only (not `RATEPAYER_PROXY` — a proxy can't
  grant further proxies, matching "ratepayer explicitly grants/revokes").
  List own grants, POST to create (by proxy's email — resolved to an
  existing `AppUser` with `access_level=RATEPAYER_PROXY`, never
  auto-created), POST `{id}/revoke/` to set `revoked_at`.

`PayerViewSet.invite_ratepayer` (COUNCIL_ADMIN, COUNCIL_IT, AGENT) creates
the linked `AppUser` (role → `RATEPAYER` access level) for an existing
`Payer`, write-only `username`/`password` fields — same shape as
`FieldAgentViewSet`'s agent-creation flow.

**Closed-world guarantee:** `RATEPAYER`/`RATEPAYER_PROXY` must never appear
in any staff-facing `access_level_permission(...)` call. Enforced by
`tests/test_rbac_closed_world.py`, which source-scans every `apps/*/api/`
file for `access_level_permission(` calls outside `apps/registry/api/` and
fails if either level appears.

## Per-endpoint wiring

See the exhaustive table in `tests/test_rbac_matrix.py` (one row per
viewset/action × access level, asserted directly against each view's
`permission_classes`/`get_permissions()`) — that test file is the
executable source of truth for "who can hit what," kept in sync with this
doc rather than duplicating the table twice by hand.

Summary of the shape (full detail in the test file — this prose is a
summary, not the source of truth; if the two disagree, trust the test):
- **`COUNCIL_AUDITOR`** — added to every read path (list/retrieve/GET
  actions) across payers, bills, payments, receipts, debt cases,
  reconciliation, settlements, audit log, field agents, sub-consultants,
  reports, dashboard. Never added to a create/update/delete/mutating
  action. (`PayerViewSet` was missed entirely in the first pass — every
  council-tier read role 403'd there despite already reading the same
  payer's name/ref embedded in `BillSerializer` etc. Confirmed live against
  production by the frontend team and fixed; see the 2026-09-11 CHANGELOG
  entry. This is exactly the class of gap `tests/test_rbac_matrix.py` now
  exists to catch before it ships again.)
- **`COUNCIL_IGR_HEAD`** — added everywhere `COUNCIL_ADMIN` appears on
  field-agent management, billing/payments/receipts, reconciliation
  (including running a reconciliation), debt cases, audit log, **and
  reports** (via `_COUNCIL_READ_LEVELS` in `apps/common/api/reports.py`,
  applied to every entity) — but *not* sub-consultant onboarding/contract
  terms or stakeholder-account management (matrix scopes IGR head to
  agents/collections/reconciliation/reporting, not firm-level commercial
  terms or ACDSL-relationship-adjacent Stakeholder accounts).
- **`COUNCIL_TREASURY`** — read-only on billing/payments/receipts, full
  access to reconciliation and reports/settlements (its actual job); no
  agent management.
- **`COUNCIL_IT`** — added to every `AppUser`-account-creation endpoint
  (field agents, revenue officers, stakeholders) plus the new
  `invite_ratepayer` action, **and to list/retrieve on the same viewsets**
  (`FieldAgentViewSet`, `SubConsultantViewSet`) — a role that can create a
  resource always needs to be able to list it, that's not a separate grant
  to weigh. (Originally missed on both viewsets' list/retrieve branches —
  `COUNCIL_IT` could create an agent but not see the list it just created
  it into; same gap on `SubConsultantViewSet`, blocking the path to its own
  `revenue_officers` action. Confirmed live against production by the
  frontend team and fixed; see the 2026-09-11 CHANGELOG entry.) Explicitly
  excluded from `PayerViewSet` and every financial viewset — its job is
  account management, never payer PII or money. Not excluded from
  read-only reference data (`wards`, `departments`, `revenue-items`) — that
  data isn't payer-identifying or transactional, the same reasoning
  `GLOBAL_VIEW` already gets those on; "zero financial access" in the
  matrix means transactional/ledger data (bills, payments, settlements),
  not a public-facing fee schedule.
- **`CONSULTANT_STAFF`** — read-only mirror of `CONSULTANT` (portfolio
  scoping is identical via `scoping.py`); `FieldAgentViewSet` gained a
  `get_permissions()` split (create vs. read) specifically so this role
  (and `COUNCIL_AUDITOR`/`COUNCIL_IGR_HEAD`/`COUNCIL_IT`) can read without
  inheriting create rights that used to be bundled at class level.
- **`AGENT_SUPERVISOR`** — read access matching `AGENT`'s existing
  endpoints plus `FieldAgentViewSet.assign_payer` (the "reassign" action),
  all narrowed to their own ward via `scoping.py`.
- **`SUPER_ADMIN`** — added everywhere `COUNCIL_ADMIN` appears, queryset
  resolved via `platform_wide_queryset` instead of `council_id=user.
  council_id` when `user.council_id is None`. Also gets `OnboardCouncilView`.
- **`PLATFORM_ADMIN`** — same read surface as `SUPER_ADMIN`; excluded from
  payment reversal, settlement status changes, and stakeholder/consultant
  suspension (destructive/approval actions stay `SUPER_ADMIN`+council-local
  `COUNCIL_ADMIN` only, per matrix "limited destructive rights vs Super
  Admin").
- **`BD_VIEW`** — `DashboardGlobalView` only.
- **`COMPLIANCE_VIEW`** — `AuditLogViewSet` (read) and `SubConsultantViewSet`
  list/retrieve (read) across all councils via `platform_wide_queryset`.
- **`ANALYTICS_VIEW`** — `DashboardGlobalView` and `ReportsView` only,
  never a raw entity list/retrieve endpoint.
- **`FINANCE_ADMIN`** — `CommissionSettlementViewSet` and `ReportsView`,
  read-only, all councils.
- **`DEVOPS_ADMIN`** — `APIClientViewSet` only.
- **`EXTERNAL_AUDITOR`** — same read surface as `COMPLIANCE_VIEW`, but
  `platform_wide_queryset` restricts it to `CouncilGrant`-listed,
  non-expired councils only instead of every active council.

## Seed / test accounts

`apps/tenancy/management/commands/seed_rbac_test_accounts.py` (new,
separate from `seed_demo_data`/`seed_starter_data` since these are QA
fixtures, not demo data) creates **two** accounts per role wherever the
role is council-scoped (two different councils/wards/firms), one where it
isn't (platform tier), per the matrix's "Test Credentials Requirement" —
named `<role>_a` / `<role>_b`, shared password printed once at creation
(not a hardcoded demo constant, so it isn't accidentally committed
anywhere). Run manually, not part of automated demo seeding.

## What this doc deliberately does not cover

- ACDSL's own council-invoicing feature (see principle 4).
- Ratepayer self-service payment initiation (see principle 4).
- OTP/2FA login for ratepayers — matrix doesn't request it; reusing the
  existing email+password JWT flow is a smaller, already-audited surface.
- Public ratepayer self-registration — `invite_ratepayer` is staff-invoked
  only, to avoid taking on identity-fraud/duplicate-payer risk with no
  verification design.
