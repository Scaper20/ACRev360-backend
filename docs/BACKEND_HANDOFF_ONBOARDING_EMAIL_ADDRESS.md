# Backend handoff — email-based onboarding + address field for 3 account types

From: frontend audit (`ACRev360-frontend`)
To: Scaper20 (`ACRev360-backend`)
Date: 2026-09-24

## Context

Two asks from the same batch of feedback:

1. "Login is currently fully email based, but onboarding is username based" —
   confirmed true. Every onboarding form (field agent, revenue officer,
   stakeholder, ratepayer invite, consultant manager) still collects a
   `username`, and none of them collect an `email` — so none of those
   accounts can actually sign in with a real email today (see Item 1's
   "why this matters" below).
2. "Address should be one of the personal information requested when
   onboarding users" — true for Field Agent, Stakeholder, and Revenue
   Officer specifically. Payer already has `address`; Consultant already
   has an equivalent field (`registered_address`, not literally named
   `address` — see Item 2).

This is written to go straight into implementation: each item points at
the exact model/serializer/view file:line, and at the specific existing
pattern in this codebase to mirror. File paths are relative to the
`ACRev360-backend` repo root. Everything below was verified against the
current checkout, not assumed from an older doc — a few things (see the
duplicate-username bug in Item 1) have drifted since related decisions
were made elsewhere in this codebase.

---

## Item 1 — onboarding collects `username`, not `email`

### Why this matters, precisely

`AppUser` (`apps/accounts/models.py:90-135`) has both fields:

```python
99   username = models.CharField(max_length=64, unique=True)
104  email = models.EmailField(unique=True)
```

`USERNAME_FIELD` stays `"username"` deliberately (documented in the
model's own docstring at lines 101-103, to avoid touching Django
admin/permissions internals that key off it). Login itself
(`AppTokenObtainPairSerializer`, `apps/accounts/tokens.py:37-90`) accepts
`email` at the HTTP layer, resolves it to the matching `username`
server-side, then authenticates through that resolved username — so the
*login form* is email-based, but only because email gets translated to
username internally. That only works for an account that actually has a
real email address stored.

None of the five onboarding flows below ever pass `email=` when calling
`AppUser.objects.create_user(...)`. When no `email` kwarg is given,
`AppUserManager._create_user` auto-assigns a placeholder
(`apps/accounts/managers.py:18`):

```python
extra_fields.setdefault("email", f"{username}@placeholder.acrev360.local")
```

So every field agent, revenue officer, stakeholder, ratepayer, and
consultant-manager account onboarded today gets a fake
`@placeholder.acrev360.local` email and **cannot sign in with a real
email address** — only `username` (via the login form's internal
resolution, which still works because `username` is real) or, for the
rare account whose email was changed later through
`UpdateProfileSerializer`'s self-service flow
(`apps/accounts/api/serializers.py:17-57`), that later-set real email.

### The five onboarding flows, current state

All five write to `AppUser` (four of them via a role-tagged `AppUser` row
directly, one — Field Agent — via a linked `FieldAgent` + `AppUser`
pair). All five need the same shape of fix: collect `email` from the
client, pass it through to `create_user(..., email=email, ...)`, drop
`username` as a client-supplied field (see "What username becomes" below
for how account lookup should work without it).

| # | Flow | Serializer | File:line | `username` field | `email` field today |
|---|---|---|---|---|---|
| 1 | Field Agent | `FieldAgentSerializer` | `apps/accounts/api/serializers.py:193-212` | `username = serializers.CharField(write_only=True)` (line 195, required) | none |
| 2 | Revenue Officer | `RevenueOfficerSerializer` | `apps/accounts/api/serializers.py:177-190` | `username = serializers.CharField()` (line 184, required) | none |
| 3 | Stakeholder | `StakeholderSerializer` | `apps/accounts/api/serializers.py:166-174` | `username = serializers.CharField()` (line 168, required) | none |
| 4 | Ratepayer invite | `InviteRatepayerSerializer` | `apps/registry/api/serializers.py:71-76` | `username = serializers.CharField(max_length=64)` (line 75, required) | none |
| 5 | Consultant manager | `SubConsultantSerializer` (manager fields inline) | `apps/accounts/api/serializers.py:109-150` | `manager_username = serializers.CharField(write_only=True, required=False)` (line 113, **optional** — onboarding a firm with no login yet is valid) | none |

Endpoints, for reference:

- Field Agent: `POST /api/v1/agents` — `FieldAgentViewSet.perform_create`,
  `apps/accounts/api/views.py:563-629` (the `create_user` call is at
  lines 604-613).
- Revenue Officer: `POST /api/v1/consultants/{id}/revenue-officers` —
  `SubConsultantViewSet.revenue_officers`, action defined at
  `apps/accounts/api/views.py:378-385`, `create_user` call at
  lines 403-410.
- Stakeholder: `POST /api/v1/stakeholders` —
  `StakeholderViewSet.perform_create`, `apps/accounts/api/views.py:814-834`,
  `create_user` call at lines 819-827.
- Ratepayer invite: `POST /api/v1/payers/{id}/invite-ratepayer` —
  `PayerViewSet.invite_ratepayer`, `apps/registry/api/views.py:213-242`,
  `create_user` call at lines 229-235 (note `full_name` here comes from
  the payer's own record, not client input — keep that as-is).
- Consultant manager: `POST /api/v1/consultants` — plain `create` on
  `SubConsultantViewSet`, `perform_create` at
  `apps/accounts/api/views.py:236-320`, the manager-login branch
  (`if manager_username:`) at lines 282-296, `create_user` call at
  lines 285-289.

### What to change, per flow

For each of the 5 serializers above:

1. Add `email = serializers.EmailField(write_only=True[, required=False for #5])`
   next to the existing `username`/`manager_username` field.
2. Remove `username`/`manager_username` as a **client-supplied** field —
   see "What username becomes" below for how the view should still
   populate `AppUser.username` without asking the caller for it.
3. In each view's creation path, pass `email=validated_data["email"]` (or
   `.get("email")` for the optional consultant-manager case) into the
   `create_user(...)` call, replacing the current placeholder-triggering
   omission.
4. Add a pre-check for email uniqueness before calling `create_user`,
   mirroring `FieldAgentViewSet`'s existing username pre-check exactly
   (`apps/accounts/api/views.py:594-595`):
   ```python
   if AppUser.objects.filter(username=username).exists():
       raise serializers.ValidationError({"username": "That username is already in use."})
   ```
   becomes (adapt field name):
   ```python
   if AppUser.objects.filter(email__iexact=email).exists():
       raise serializers.ValidationError({"email": "That email is already in use."})
   ```
   Case-insensitive check (`email__iexact`) to match how login itself
   resolves email (`apps/accounts/tokens.py:58`,
   `email__iexact=email`) — a duplicate that differs only in case would
   otherwise pass this check but still collide with login's own lookup
   semantics, or worse, silently shadow an existing account at login time
   depending on which row Postgres returns first for a case-only
   difference the DB-level `unique=True` doesn't actually block (`email`
   is `EmailField(unique=True)`, a case-sensitive unique constraint at the
   DB layer — this app-level check is what actually enforces
   case-insensitivity).

### What `username` becomes

`AppUser.username` keeps `unique=True` at the DB level and stays
`USERNAME_FIELD` for Django's own machinery (per the model docstring's
reasoning — don't change that, it's a deliberate, documented choice, not
an oversight). Once onboarding stops asking the client for one, the
server needs to derive something usable for that column itself. Simplest
option, consistent with `email` already being unique: **generate
`username` from `email`** at creation time — slugify the local-part
before the `@` (e.g. `jane.doe@example.com` → `jane.doe`), then append a
numeric suffix on collision (`jane.doe`, `jane.doe2`, `jane.doe3`, ...)
checked via the same `AppUser.objects.filter(username=...).exists()`
pattern already used at line 594. This keeps `username` fully internal —
never shown on any onboarding form again, never round-tripped from the
frontend — while satisfying the column's existing constraints without a
migration.

(Frontend note, so this reads correctly against
`FRONTEND_HANDOFF_RBAC.md`/the portal's own onboarding forms once this
lands: swap every onboarding form's "Username" input for an "Email"
input of the same required-ness as shown in the table above — required
for agent/revenue-officer/stakeholder/ratepayer, optional for consultant
manager.)

### Bug found on the way — 4 of 5 flows 500 on duplicate username today

Only Field Agent (`apps/accounts/api/views.py:594-595`, above) pre-checks
uniqueness before calling `create_user`. The other four —
Revenue Officer, Stakeholder, Ratepayer invite, and Consultant manager —
call `AppUser.objects.create_user(...)` directly with no existence check
first. A duplicate `username` there hits the raw DB-level `unique=True`
constraint and raises an uncaught `IntegrityError`, which is **not** a
DRF `APIException`, so it skips `acrev360_exception_handler`
(`apps/common/exceptions.py:4-29`, returns `None` for anything DRF's own
`exception_handler` doesn't recognize) and falls through to Django's
generic 500 page — no detail to the client, and (DEBUG off, per this
project's Docker setup) nothing in the logs either. This exact failure
shape is already fixed once elsewhere in this same file, for
`contract_ref` (`apps/accounts/api/views.py:244-250`, comment explains
the same reasoning) — the fix here is the same shape, just applied to
`email` (per Item 1's step 4 above) instead of `username`, since `email`
is what the client will be duplicating going forward once `username`
stops being client-supplied.

---

## Item 2 — no `address` field for Field Agent, Stakeholder, Revenue Officer

### Confirmed: Payer and Consultant already have it (one naming caveat)

- **Payer**: `apps/registry/models.py:40` —
  `address = models.CharField(max_length=255, blank=True)`. Present on
  both `PayerSerializer` (`apps/registry/api/serializers.py:7-26`,
  `Meta.fields` line 16) and the creation-time
  `CreatePayerSerializer` (`apps/registry/api/serializers.py:29-45`,
  `Meta.fields` line 42). Field name is literally `address`.
- **SubConsultant** (Consultant firm): `apps/accounts/models.py:200` —
  `registered_address = models.CharField(max_length=255, blank=True)`.
  **Field name is `registered_address`, not `address`** — flagging this
  explicitly since the original ask assumed a literal `address` field
  name on both; it's only true for Payer. Present and writable on
  `SubConsultantSerializer`
  (`apps/accounts/api/serializers.py:109-150`, `Meta.fields` at
  lines 126-132, not in `read_only_fields` at line 134).

### Confirmed: none of the three target entities has any address field today

- **Field Agent**: `FieldAgent` model, `apps/accounts/models.py:217-252`.
  Full own-field list: `user`, `agent_code`, `assigned_ward`,
  `device_imei`, `status`, `id_type`, `id_hash`, `next_of_kin_name`,
  `next_of_kin_phone` (lines 233-243). No address-shaped field anywhere
  in the class. Serializer (`FieldAgentSerializer`,
  `apps/accounts/api/serializers.py:193-212`, `Meta.fields` at
  lines 207-211) has no `address` field either.
- **Stakeholder**: no dedicated model — a role-tagged `AppUser` row
  (`role.access_level == AppRole.GLOBAL_VIEW`). `AppUser`'s full field
  list (`apps/accounts/models.py:90-135`) has no address-shaped field.
  Serializer (`StakeholderSerializer`,
  `apps/accounts/api/serializers.py:166-174`, `Meta.fields` at line 173)
  has no `address` field.
- **Revenue Officer**: same underlying model as Stakeholder — a
  role-tagged `AppUser` row (`role.access_level ==
  AppRole.REVENUE_OFFICER`). Same absence on both the model and
  `RevenueOfficerSerializer`
  (`apps/accounts/api/serializers.py:177-190`, `Meta.fields` at line 189).

### What to add

**Field Agent** gets its own new column, following `FieldAgent`'s own
existing string-field shape (`next_of_kin_name`, blank-allowed, no
separate required-ness beyond the column type):

1. Migration adding `address = models.CharField(max_length=255,
   blank=True)` to `FieldAgent` (`apps/accounts/models.py:217-252`),
   sized and shaped to match `Payer.address` exactly (same 255-char cap,
   same `blank=True`, no `null=True` — consistent with every other
   optional string field in this codebase, which use `blank=True` alone
   and let the DB store `''` rather than `NULL`).
2. Add `address` to `FieldAgentSerializer.Meta.fields`
   (`apps/accounts/api/serializers.py:207-211`).

**Stakeholder and Revenue Officer** are both bare `AppUser` rows with no
entity-specific model to attach a column to — adding `address` to
`AppUser` itself would put it on every account type (including
council-staff logins that have no reason to carry a physical address).
Two options, in order of preference:

- **Option A (recommended): add `address` to `AppUser` anyway, blank for
  roles that don't use it.** Simplest migration, matches how `phone`
  already works on `AppUser` (`apps/accounts/models.py:105`,
  `CharField(max_length=32, blank=True)`, present on every role whether
  or not it's asked for on that role's own onboarding form — Field Agent
  and Consultant Manager onboarding already collect `phone` this same
  way despite it living on the shared `AppUser` model, not a per-role
  one). `address = models.CharField(max_length=255, blank=True)` on
  `AppUser` (`apps/accounts/models.py:90-135`), surfaced only on
  `StakeholderSerializer` and `RevenueOfficerSerializer`'s `Meta.fields`
  (not on every other `AppUser`-backed serializer in the codebase, so it
  doesn't show up somewhere it shouldn't — e.g. council-admin account
  management, if that has its own serializer, should leave it out of
  `Meta.fields` there).
- **Option B: give Stakeholder and Revenue Officer their own thin
  models** (each currently exists only as a role tag on `AppUser`, no
  dedicated table) purely to hold `address` plus room for future
  role-specific fields. Larger change — new migration, new FK, view/
  serializer restructuring to join against it — not justified by this
  one field alone. Only worth it if a second role-specific field is
  already anticipated; flagging as the alternative rather than picking
  it, since nothing else in this batch of asks needs it.

This spec assumes **Option A** for the rest of its wording, but either is
implementable from the facts above — Option B just needs its own smaller
follow-up spec once/if a second Stakeholder- or Revenue-Officer-only
field is actually needed.

3. Migration adding `address = models.CharField(max_length=255,
   blank=True)` to `AppUser` (`apps/accounts/models.py:90-135`).
4. Add `address` to `StakeholderSerializer.Meta.fields`
   (`apps/accounts/api/serializers.py:173`) and
   `RevenueOfficerSerializer.Meta.fields`
   (`apps/accounts/api/serializers.py:189`).
5. Pass `address=validated_data.get("address", "")` through to each
   `create_user(...)` call — `StakeholderViewSet.perform_create`
   (`apps/accounts/api/views.py:819-827`) and
   `SubConsultantViewSet.revenue_officers`
   (`apps/accounts/api/views.py:403-410`).

---

## Shared conventions used above

**Optional string fields** — `blank=True`, no `null=True`, matching
every existing optional `CharField` in this codebase (`phone`,
`device_imei`, `next_of_kin_name`, `registered_address`, `Payer.address`
itself) — empty string is the "not provided" state, never `NULL`.

**Case-insensitive uniqueness on an identity-shaped field** — `email`'s
DB constraint (`EmailField(unique=True)`) is case-sensitive; every
app-level check against it (login's own lookup at
`apps/accounts/tokens.py:58`, and the new pre-checks in Item 1) uses
`email__iexact` to actually be case-insensitive in practice. Match this
pattern for any new email-uniqueness check rather than a bare
`.filter(email=email)`.

**Pre-check before `create_user`, not a caught `IntegrityError`** — every
existing example of this pattern in the codebase (`contract_ref` at
`apps/accounts/api/views.py:244-250`, `username` at lines 594-595) is an
explicit `.filter(...).exists()` check raised as a DRF
`ValidationError` *before* the write, not a `try/except IntegrityError`
around it — keep the same shape for the new `email` checks in Item 1
rather than introducing a different error-handling style.

---

## Frontend scope, once any of this lands

**Item 1 (email-based onboarding)**: swap "Username" for "Email" on 5
onboarding forms (v1 + v2 pairs where they exist) — Field Agent onboard
modal, Revenue Officer create form (inside Consultant detail
panel/context panel), Stakeholder onboard modal, ratepayer-invite dialog
(inside Payer detail modal/context panel), and the Consultant creation
form's manager-login section (optional field, same as today). No other
UI restructuring — this is a field swap, not a new flow.

**Item 2 (address field)**: add an "Address" input to the same 3
onboarding forms — Field Agent, Stakeholder, Revenue Officer (v1 + v2
pairs) — matching how Payer's and Consultant's forms already present
their own address input (a single-line text field, optional, no format
validation beyond length).

Both are same-day frontend follow-ups once the corresponding endpoint
changes ship — no further backend-side investigation needed on either.

---

## Summary table

| # | Area | Kind of gap | Effort |
|---|---|---|---|
| 1 | 5 onboarding flows collect `username`, not `email` — none of those accounts can sign in with a real email | Serializer field swap + `username` auto-derivation + duplicate-username 500 fix on 4/5 flows | Medium |
| 2 | Field Agent has no `address` column at all | New column + migration + serializer field | Small |
| 2 | Stakeholder / Revenue Officer have no `address` column (shared `AppUser` model) | New column on `AppUser` + migration + 2 serializer fields + 2 view wiring changes | Small |
