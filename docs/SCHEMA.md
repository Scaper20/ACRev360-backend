# ACRev360 — Backend Schema Reference

Human-readable companion to the **v1 prototype's** schema
([`backend/schema.sql`](../backend/schema.sql), SQLite, written Postgres-portable on
purpose — see [TDD.md](TDD.md)): 28 tables + 3 reporting views. Retained as the reference
implementation's data model — [V2_ARCHITECTURE.md §6](V2_ARCHITECTURE.md) documents
exactly what changes when this is ported to the real v2 PostgreSQL schema (Django
migrations under `backend/apps/*/migrations/`, which become the actual source of truth
once the rewrite lands).

---

## 1. Organisation & territory

**`council`** — the tenant. One row per Area Council (`council_code`, `council_name`).
Every tenant-scoped table below carries a `council_id`, even though this build only
seeds one (`KAC`) — that's what makes multi-council a configuration change rather than a
rewrite (see [TDD.md](TDD.md)).

**`ward_zone`** — sub-council geography (`ward_code`, `ward_name`, `zone_type`:
WARD/ZONE/DISTRICT). Payers, agents, and POS terminals all attach to a ward.

**`sub_consultant`** — an outsourced revenue-collection firm. `commission_rate`,
`contract_ref`, and a lifecycle `status` (`PENDING → ACTIVE → SUSPENDED/EXITED`).

## 2. Users, roles & access

**`app_role`** — the four `access_level`s (`COUNCIL_ADMIN`, `CONSULTANT`, `AGENT`,
`GLOBAL_VIEW`) each map to one or more named roles (e.g. both `COUNCIL_ADMIN` and
`HEAD_REVENUE` roles carry the `COUNCIL_ADMIN` access level).

**`app_user`** — login identity. `consultant_id` is set for consultant-side users
(managers and their agents) and null for Council-direct staff; `role_id` determines
access level.

**`field_agent`** — the agent-specific extension of `app_user` (one-to-one via
`user_id`): `agent_code`, `assigned_ward_id`, `device_imei`, own lifecycle status.

**`audit_log`** — append-only. `action` (e.g. `BILL_ISSUED`, `RATE_CHANGED`),
`entity_type`/`entity_id` it happened to, a JSON `detail` blob, the actor, their IP, and
a timestamp. Indexed on `(entity_type, entity_id)` for "show me everything that happened
to this record."

## 3. Harmonised chart of revenue

**`revenue_category`** → **`revenue_item`** → **`rate_schedule`**, in a strict
one-to-many chain:
- A category (Rates, Licences and Permits, Fees and Charges, Registration and
  Professional Fees, Levies) groups items.
- An item (`harmonised_code`, `item_name`, `unit_of_charge`) is the thing that gets
  billed. `in_initial_scope` flags whether it's actively used vs. reserved for later.
- `rate_schedule` is the item's price **history**, not a single field — each row has
  `effective_from`/`effective_to`. Changing a rate never overwrites the old row; it
  closes it and inserts a new one, so `assessment` rows always cite the exact rate they
  were priced at, even years later.

**`consultant_portfolio`** — which revenue items (optionally scoped to a ward) a given
consultant is allowed to handle, each with its own `effective_from`/`effective_to`.
Ending an assignment sets `effective_to` rather than deleting the row — history is kept.

## 4. Taxpayer enumeration & registry

**`payer`** — an individual or business ratepayer. `payer_ref` is format-distinct by
type (`IND-xxxxxxx` individuals, `C-xxxxxxx` everyone else). `nin_bvn_hash` and `tin`
capture whichever ID type applies; `kyc_status` and `is_duplicate_of` (self-referencing,
for dedup) support enumeration quality control. `business_size`
(`MICRO`/`SMALL`/`MEDIUM`/`LARGE`, SMEDAN-style MSME bands) classifies non-individual
payers — it's null for `INDIVIDUAL` and optional otherwise, captured at enumeration.

**`enumerated_asset`** — a physical thing tied to a payer (premises, shop, kiosk,
signage), captured with GPS at enumeration time. Assessments can optionally cite the
specific asset being charged for.

## 5. Assessment & e-billing

**`assessment`** — one line of "payer X owes Y for revenue item Z," priced against a
specific `rate_schedule` row. Status is a small state machine:
`DRAFT → BILLED` (normal path) or `DRAFT → CANCELLED` (if a bill line is later removed).
`DRAFT` assessments are exactly what enumeration creates and what "Issue Harmonized
Bill" rolls up.

**`bill`** — the harmonised, payable unit. `bill_ref` (globally unique, race-safe —
see [TDD.md](TDD.md) §4.5), `total_amount`/`amount_paid`, and a status machine:
`ISSUED → PART_PAID → PAID`, or `→ OVERDUE` if unpaid past `due_date`, or `→ CANCELLED`,
or `→ SUPERSEDED` (see below). `_recompute_bill()` re-derives both `total_amount`
(billed lines + `arrears_amount`) and `status` after any line edit.

`arrears_amount` and `superseded_by` support **arrears consolidation**: issuing a bill
with `roll_arrears` sums every prior open balance (`ISSUED`/`PART_PAID`/`OVERDUE`) for
that payer into `arrears_amount`, adds it to the total, and flips those prior bills to
`SUPERSEDED` with `superseded_by` pointing at the new consolidated bill (so the amount is
never owed — or counted — twice). Because `arrears_amount` is a bill-level figure, not a
`bill_line`, it carries no item attribution; billed-by-item totals subtract it to stay
exact.

**`bill_line`** — join table between `bill` and `assessment` (`ON DELETE CASCADE` from
`bill`), each carrying its own `line_amount` — which can be admin-overridden per payer
without touching the item's standard rate.

## 6. Multi-channel e-payments

**`payment_channel`** — the five channels (POS, OTC, IB_MB, USSD, FIRSTMONIE), each with
a `provider`.

**`pos_terminal`** — physical POS fleet, each assigned to an agent and a ward, with its
own `status` (ACTIVE/FAULTY/RETIRED).

**`payment`** — the actual money-in event. Every channel's webhook/settlement/manual
entry ends up as one of these via `post_payment()`. `txn_status` defaults `CONFIRMED`
(this demo doesn't model a pending-then-confirmed async flow, though the column supports
it: `PENDING/CONFIRMED/FAILED/REVERSED`).

**`channel_transaction_feed`** — the *bank's* side of the story: raw inbound
notifications before they're matched to a `payment`. `UNIQUE(channel_id, bank_txn_ref)`
is what makes webhook replays idempotent. `match_status`
(`UNMATCHED → MATCHED`/`EXCEPTION`) drives the reconciliation exception queue.

**`api_client`** — registered API credentials per channel integration (`api_key`,
`secret_hash`) for HMAC signature verification on inbound webhooks.

## 7. e-Receipting

**`receipt`** — one per `payment` (`UNIQUE`), carrying a `qr_token` (UUID) for public
verification and a `verified_count` that increments on every check.

## 8. Reconciliation & commission settlement

**`reconciliation_run`** — one row per channel per day a reconciliation was run:
`total_platform` vs `total_bank`, with `status` (`OPEN/BALANCED/EXCEPTIONS/CLOSED`).

**`reconciliation_exception`** — an unresolved mismatch from a run, linkable to the
specific `channel_transaction_feed` row and/or `payment` in question, with a resolution
note and resolver once closed.

**`commission_settlement`** — one row per consultant per period
(`UNIQUE(consultant_id, period_start, period_end)`): `gross_collections`, the
`commission_rate` *snapshot* at settlement time (not a live join to `sub_consultant`, so
a later rate change doesn't retroactively alter a past settlement), and a status ladder
(`COMPUTED → APPROVED → SETTLED`, or `DISPUTED`).

## 9. Debt management & enforcement

**`debt_case`** — one per overdue bill. `ageing_bucket` (`0_30/31_60/61_90/OVER_90`) and
`enforcement_stage`, a six-step ladder:
`NONE → FIRST_NOTICE → FINAL_NOTICE → ENFORCEMENT → LEGAL → CLOSED`. Escalation moves
exactly one step at a time and bumps `reminder_count`. A case auto-closes
(`enforcement_stage = CLOSED`) the moment its bill is fully paid, regardless of what
stage it was at.

## 10. Field operations (offline mobile)

**`agent_daily_return`** — one row per agent per day (`UNIQUE(agent_id, return_date)`):
visits, bills issued, amount collected — the roll-up a consultant sees in their team's
activity view.

**`sync_queue`** — records captured offline on a device, replayed via
`POST /api/mobile/sync`. Kept even after successful sync, as a durable log of what was
captured and when (`device_created_at` vs `synced_at`).

## 11. API client registry

Covered under §6 above (`api_client`) — grouped there in the schema file because it's
specifically e-channel integration credentials, not a general-purpose API key system.

---

## Reporting views

- **`v_collections_by_channel`** — confirmed payment totals, grouped by channel and day.
- **`v_global_performance`** — billed vs. collected, grouped by consultant (or "Council
  Direct"), revenue item, and ward. `total_collected` is apportioned pro-rata from each
  bill's overall payment ratio (`amount_paid / total_amount`), since payments settle a
  whole bill, not individual lines.
- **`v_debt_ageing`** — case count and outstanding amount per ageing bucket, for bills
  still in `ISSUED`/`PART_PAID`/`OVERDUE`.

## Key relationships at a glance

```
council ──< ward_zone
council ──< sub_consultant ──< field_agent >── app_user
council ──< revenue_category ──< revenue_item ──< rate_schedule
sub_consultant ──< consultant_portfolio >── revenue_item

payer ──< enumerated_asset
payer ──< assessment >── revenue_item, rate_schedule
assessment ──< bill_line >── bill ──< payment ──< receipt
bill ──< debt_case

payment_channel ──< pos_terminal, payment, channel_transaction_feed
channel_transaction_feed ──? payment          (matched via reconciliation)
reconciliation_run ──< reconciliation_exception

sub_consultant ──< commission_settlement
field_agent ──< agent_daily_return, sync_queue
```

## Status machines, all in one place

| Entity | States |
|---|---|
| `sub_consultant.status` | `PENDING → ACTIVE → SUSPENDED/EXITED` |
| `field_agent.status` | `ACTIVE → SUSPENDED/EXITED` |
| `payer.kyc_status` | `PENDING → VERIFIED`, or `FLAGGED` |
| `revenue_item` rate | versioned via `rate_schedule.effective_from/to`, never mutated in place |
| `assessment.status` | `DRAFT → BILLED`, or `→ CANCELLED`; (`APPROVED` reserved, unused by this build) |
| `bill.status` | `ISSUED → PART_PAID → PAID`, or `→ OVERDUE`, or `→ CANCELLED`, or `→ SUPERSEDED` (rolled into a consolidated bill) |
| `payer.business_size` | `MICRO`/`SMALL`/`MEDIUM`/`LARGE` (non-individuals only; nullable) |
| `payment.txn_status` | `PENDING/CONFIRMED/FAILED/REVERSED` (this build only ever writes `CONFIRMED`) |
| `channel_transaction_feed.match_status` | `UNMATCHED → MATCHED`/`EXCEPTION` |
| `reconciliation_run.status` | `OPEN → BALANCED/EXCEPTIONS → CLOSED` |
| `commission_settlement.status` | `COMPUTED → APPROVED → SETTLED`, or `DISPUTED` |
| `debt_case.enforcement_stage` | `NONE → FIRST_NOTICE → FINAL_NOTICE → ENFORCEMENT → LEGAL → CLOSED` |
