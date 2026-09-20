# Backend handoff — add deactivate/remove endpoints for 5 entities

From: frontend audit (`ACRev360-frontend`)
To: Scaper20 (`ACRev360-backend`)
Date: 2026-09-20

## Context

Ask: "Any field where you're able to add items, you should also be able to
delete them." Audited every "Add X" flow in the portal (v1 + v2) and field
app for a matching remove/deactivate action. Most already have one under a
different name — revenue items have `retire`, consultant/agent portfolio
assignments have `portfolio/{id}/end`, API clients have `revoke`, payers
and bill lines have a real `DELETE`. Five entities don't have any removal
path at all: **Areas** (`WardZone`), **Departments**, **Revenue Officer
accounts**, **Stakeholder accounts**, and **Field Agent accounts**.

Of those five, three (Revenue Officer, Stakeholder, Field Agent) already
have a dormant `is_active`/`status` field on the model that's declared
`read_only` in its serializer and never written to by any endpoint — the
field exists, nothing flips it. The other two (Areas, Departments) have no
soft-delete field at all yet.

This is written to go straight into implementation: each item below points
at the exact model/serializer/viewset file:line, and at the specific
existing pattern in this codebase to mirror, rather than inventing a new
shape. File paths are relative to the `ACRev360-backend` repo root.

Soft-deactivate, not hard delete, on purpose: several of these rows may
already be referenced elsewhere (a ward with payers, a department with
revenue items, an account with activity/audit history), and this codebase's
own convention everywhere else is deactivate-in-place
(`is_active`/`status`), not a real `DELETE`.

---

## Item 1 — Areas (`WardZone`) can't be deactivated

**Model**: `apps/tenancy/models.py:34-53`. No soft-delete field exists at
all — `ward_code`, `ward_name`, `zone_type` only.

**ViewSet**: `WardZoneViewSet`, `apps/tenancy/api/views.py:14-32`. Router
registration only wires `get, post, head, options` — no `PATCH`, `DELETE`,
or custom action today.

**Needed**:
1. Migration adding `is_active = models.BooleanField(default=True)` to
   `WardZone`, following `CouncilRevenueItem.is_active`'s shape
   (`apps/revenue/models.py:60`) — a plain boolean, no separate timestamp
   column, consistent with every other soft-delete field in this codebase.
2. A `deactivate` action, closest precedent is `APIClientViewSet.revoke`
   (`apps/payments/api/views.py:350-363`):
   `POST /api/v1/wards/{id}/deactivate`, idempotent (no-op + no duplicate
   audit entry if already inactive), audit `action="WARD_DEACTIVATED"`,
   `entity_type="WARD_ZONE"`, `detail={"ward_code": ..., "ward_name": ...}`.
3. `WardZoneViewSet.get_queryset()` (`apps/tenancy/api/views.py:28-29`)
   already filters `council_id=self.request.user.council_id` — reuse as-is
   for the new action's `get_object()`.
4. Frontend note: doesn't need a reference-blocking check before
   deactivating (payers/bills referencing a deactivated ward keep their
   historical FK; the portal would just stop offering it in "choose an
   area" pickers going forward) — but worth checking whether
   `PayerViewSet`/`FieldAgentViewSet`'s create-time `ward` picker queries
   should start filtering `is_active=True` once this lands, so newly
   registered payers/agents can't be assigned to a deactivated area.

## Item 2 — Departments can't be deactivated

**Model**: `apps/tenancy/models.py:56-80`. Same situation as Areas — no
soft-delete field. `department_name`, `department_code`, `head_name`,
`head_phone`, `legal_basis` only.

**ViewSet**: `DepartmentViewSet`, `apps/tenancy/api/views.py:35-56`. Already
supports `PATCH` (for editing the fields above) but no deactivate action and
no field to flip.

**Needed**: same shape as Item 1 — add `is_active` via migration, add a
`POST /api/v1/departments/{id}/deactivate` action following the same
`APIClientViewSet.revoke` idempotent-boolean-flip pattern, audit
`action="DEPARTMENT_DEACTIVATED"`, `entity_type="DEPARTMENT"`. Existing
`get_queryset()` council filter (`apps/tenancy/api/views.py:52-53`) covers
scoping already.

## Item 3 — Revenue Officer accounts can't be deactivated

**Model**: not a dedicated model — an `AppUser` row
(`apps/accounts/models.py:90-130`) with `role.access_level ==
AppRole.REVENUE_OFFICER`, created via
`SubConsultantViewSet.revenue_officers` POST
(`apps/accounts/api/views.py:342-369`).

**The field already exists and is already wired to nothing**:
`AppUser.is_active` (`apps/accounts/models.py:113`) is real, but
`RevenueOfficerSerializer` (`apps/accounts/api/serializers.py:142-155`)
declares it `read_only` — nothing in the codebase ever writes to it for this
role.

**Needed**: a `deactivate` action on `SubConsultantViewSet`, nested the same
way `end_portfolio` already is (`apps/accounts/api/views.py:413-423`, URL
pattern `portfolio/(?P<portfolio_id>[0-9]+)/end`) — something like
`POST /api/v1/consultants/{id}/revenue-officers/{officer_id}/deactivate`,
flip `AppUser.is_active = False` (`save(update_fields=["is_active"])`),
idempotent like `APIClientViewSet.revoke`. Audit
`action="REVENUE_OFFICER_DEACTIVATED"`, `entity_type="APP_USER"`,
`detail={"username": ...}`. Worth confirming a deactivated user's login
already stops working via `is_active=False` at auth time (Django's own
`AbstractBaseUser`/auth backends check `is_active` by default) rather than
this needing new auth-layer code.

## Item 4 — Stakeholder accounts can't be deactivated

Same shape as Item 3: `AppUser` row with `role.access_level ==
AppRole.GLOBAL_VIEW` (role name `"STAKEHOLDER"`), created via
`StakeholderViewSet.perform_create` (`apps/accounts/api/views.py:669-709`).
`StakeholderSerializer` (`apps/accounts/api/serializers.py:131-139`) also
declares `is_active` read-only — same dormant-field situation. The frontend
already shows an "Active/Inactive" tag on the Stakeholders page that nothing
ever changes.

**Needed**: `POST /api/v1/stakeholders/{id}/deactivate` on
`StakeholderViewSet`, same idempotent boolean-flip pattern, audit
`action="STAKEHOLDER_DEACTIVATED"`, `entity_type="APP_USER"`.

## Item 5 — Field Agent accounts can't have their status changed

**Model**: `FieldAgent`, `apps/accounts/models.py:212-247`. Different shape
from Items 3-4 — already has a 3-state `status` field
(`ACTIVE`/`SUSPENDED`/`EXITED`, default `ACTIVE`), not a boolean. Currently
`read_only` in `FieldAgentSerializer`
(`apps/accounts/api/serializers.py:158-177`, line 177) — set at creation,
never changed after.

**Closer precedent than Items 1-4**: `SubConsultant` already has this exact
3-state shape and a working status-change endpoint —
`SubConsultantViewSet.status_change` (`apps/accounts/api/views.py:278-307`,
`POST /api/v1/consultants/{id}/status_change`, body `{"status": "..."}`,
validated via `SubConsultantStatusSerializer`). Point `FieldAgentViewSet` at
the same pattern rather than the boolean-flip one used above:
`POST /api/v1/agents/{id}/status_change`, body `{"status":
"ACTIVE"|"SUSPENDED"|"EXITED"}`, audit `action="AGENT_STATUS_CHANGED"`,
`entity_type="FIELD_AGENT"`, `detail={"from": ..., "to": ...}` (matching
whatever `status_change`'s existing audit detail shape looks like for
consultants, for consistency).

---

## Shared conventions used above

**Audit log** — `apps/audit/services.py:4-13`,
`audit(*, council_id, actor, action, entity_type, entity_id, detail=None, actor_ip=None)`.
`action` is `{ENTITY}_{VERB_PAST}` SCREAMING_SNAKE_CASE; `entity_type` is a
fixed string per model; `council_id` comes from the mutated row's own
`council_id`.

**Idempotency** — `APIClientViewSet.revoke` wraps its flip+audit in
`if client.is_active:` so calling it twice is a silent no-op the second
time, still returns `200`. All five new actions above should follow the
same shape rather than erroring on a repeat call.

**Council scoping** — every viewset here already filters
`get_queryset()` by `council_id=self.request.user.council_id`
(`WardZoneViewSet`, `DepartmentViewSet` directly; the account-backed ones
via their parent `SubConsultant`/`FieldAgent`/`AppUser` queries). Reuse
those existing filters for the new actions' `self.get_object()` calls — no
new RLS wiring needed. One caveat: `apps/tenancy/context.py`'s module
docstring notes `app_role`/`app_user`/`council_grant` sit **outside**
Postgres RLS by design, so for Items 3-4 (both `AppUser` rows) scoping
relies entirely on the explicit `get_queryset()` council filter, not RLS —
match the existing pattern, don't add RLS to `AppUser` as part of this.

---

## Frontend scope, once any of these land

Frontend already has the exact UI slots identified for all five (Areas
table, Revenue Officer list in the consultant detail panel, Stakeholders
table, Departments edit modal, Field Agents table/detail panel) — wiring up
a remove/deactivate button in both v1 and v2 for whichever of these ship is
a same-day frontend follow-up once the endpoint exists, no further backend-
side investigation needed on that end.

---

## Summary table

| # | Area | Kind of gap | Effort |
|---|---|---|---|
| 1 | Areas can't be deactivated | New column + migration + action | Small |
| 2 | Departments can't be deactivated | New column + migration + action | Small |
| 3 | Revenue Officers can't be deactivated | Existing field, unlock + new action | Small |
| 4 | Stakeholders can't be deactivated | Existing field, unlock + new action | Small |
| 5 | Field Agents can't have status changed | Existing field, unlock + new action (mirror SubConsultant's) | Small |
