# ACRev360 — App Flow

How the pieces connect, screen to screen, for each surface and role. Pairs with
[DESIGN_BRIEF.md](DESIGN_BRIEF.md) for how things look and [PRD.md](PRD.md) for why.

---

## 1. Entry points

| Surface | URL | Who |
|---|---|---|
| Web portal | `/` | `COUNCIL_ADMIN`, `CONSULTANT`, `GLOBAL_VIEW` |
| Mobile field app | `/m` | `AGENT` only — the portal login rejects agent accounts and tells them to use `/m`; the mobile login rejects everyone else the same way, in reverse. |
| Public bill lookup | `GET /api/bills/{bill_ref}` | Anyone — powers the demand notice/bill print pages and USSD |
| Public receipt verification | `GET /api/verify/{qr_token}` | Anyone with a receipt's QR/SMS code |
| USSD | `POST /api/channels/USSD/session` | Feature-phone users via a telco gateway |

Signing in with the wrong surface's account type fails fast with a message telling you
where to go instead — there's no silent partial-access state.

## 2. Web portal navigation map

The sidebar is built per-role from `NAV_SECTIONS` in `app.js`; a role never sees a nav
item it isn't allowed to act on.

**`COUNCIL_ADMIN`**
```
Overview          Dashboard · Global Performance
Revenue Ops       Payer Registry · Assessment & e-Billing · Payments · e-Receipts
Finance           Reconciliation · Commission Settlements · Debt Management
Administration    Revenue Items · Sub-Consultants · Field Agents · POS Terminal Fleet · Reports · Audit Log
```

**`CONSULTANT`** (everything scoped to their own portfolio)
```
Overview          Dashboard
Revenue Ops       Payer Registry · Assessment & e-Billing · Payments · e-Receipts
Finance           Reconciliation · Commission Settlements · Debt Management
Team              Field Agents · POS Terminal Fleet · Reports
```

**`GLOBAL_VIEW`** (read-only)
```
Overview          Dashboard · Global Performance
Administration    Sub-Consultants (list only, no status control)
```

## 3. Interaction pattern: click the row, not a button beside it

Every list page (Bills, Payers, Receipts, Debt Management, Revenue Items,
Sub-Consultants, Field Agents) follows the same shape:

```
list page  →  click any row  →  detail modal
                                   ├─ full record detail (read-only for everyone)
                                   ├─ role-gated edit controls, inline in the same modal
                                   └─ role-gated actions (Escalate / Change Rate / etc.)
```

There is no trailing "Actions" column anywhere — the row itself is the control, signalled
by a hover tint and a trailing `›` chevron. This keeps the table itself clean and puts
every action for an item in one place instead of scattered across button cells.

## 4. Core journey: enumerate → bill → collect → reconcile → settle

This is the spine of the product — every other screen supports one step of it.

```
1. ENUMERATE                 admin, consultant, or agent (web or mobile)
   Register Individual / Register Business
   → payer created (IND-xxxxxxx / C-xxxxxxx)
   → optionally select revenue items now liable
     → each becomes a DRAFT assessment, priced at today's rate

2. BILL                      from the payer's detail view
   Option A: "Issue Harmonized Bill" — rolls up every DRAFT assessment into one bill
   Option B: "New Bill" — hand-pick items + quantities
   (either can also "consolidate prior arrears": prior unpaid balances are brought
    forward as an arrears segment and those earlier bills become SUPERSEDED)
   → one bill_ref, status ISSUED, due in 30 days

3. (optional) ADMIN EDITS THE BILL
   Add a line · override one line's cost for this payer · remove a line
   → total_amount and status recompute automatically

4. PRINT (if needed)
   From the bill's detail view: "Print Notice" (formal legal notice) or
   "Print Bill" (itemised statement) — opens a print-preview overlay

5. COLLECT                   any of 5 channels
   POS / OTC teller / internet-mobile banking / USSD / FirstMonie
   → post_payment(): bill.amount_paid updated, status recalculated
   → receipt issued with a QR/SMS verification token
   → if bill is now fully paid, any open debt_case on it closes automatically

6. RECONCILE                 admin, per day per channel
   "Run Reconciliation" compares platform payments against the bank feed
   → BALANCED, or EXCEPTIONS queued for investigation

7. SETTLE                    admin, per period
   "Compute Settlements" → each active consultant's commission
   = gross collections in the period × their contract rate
```

If step 5 never happens and the due date passes, the bill ages into the **debt track**
instead: `refresh_debt()` buckets it (0–30/31–60/61–90/90+ days) and opens a case at
`FIRST_NOTICE`; admin escalates it up the ladder
(`FIRST_NOTICE → FINAL_NOTICE → ENFORCEMENT → LEGAL → CLOSED`) from the case's detail
view.

## 5. Consultant/agent management journey

```
ADMIN                                    CONSULTANT
  │                                          │
  ├─ Onboard Consultant                      │
  │    (name, contract ref, commission %)    │
  │                                          │
  ├─ Assign portfolio                        │
  │    pick revenue items this consultant    │
  │    is allowed to handle (+ revoke later) │
  │                                          │
  │                                          ├─ Onboard Agent
  │                                          │    (own team only —
  │                                          │     consultant_id is forced
  │                                          │     to their own)
  │                                          │
  │                                          ├─ View agent activity
  │                                          │    daily returns + recent
  │                                          │    payments, own agents only
  │                                          │
  ├─ Onboard Agent (any consultant,          │
  │    or council-direct)                    │
  ├─ View any agent's activity               │
  └─ Suspend/reactivate a consultant          │
```

## 6. Mobile (field agent) journey

```
Sign in (agent account only)
  → Worklist: payers in the agent's assigned ward, sorted by outstanding balance
  → tap a payer → Collect: pick channel, amount, capture GPS → post payment
       (offline? queued locally, synced automatically on reconnect)
  → Register: enumerate a new Individual/Business, same item-selection pattern
       as the web portal
  → Status: today's collection total, sync queue depth, force-sync button
```

## 6a. Custom reports (admin + consultant)

The **Reports** page is a small report builder over four whitelisted datasets
(Payers, Bills, Payments, Debt Cases):

```
pick dataset → tick the fields to include → (optional) search + status filter
   → Run report → results table → Download CSV
```

A consultant's report is portfolio-scoped exactly like every other screen (their
Bills/Payments/Debt rows only); admin sees everything. Field selection is a fixed
whitelist per dataset — see [TDD.md](TDD.md) §4.8 — so it can't be used to reach columns
a role shouldn't see.

## 6b. Identifying a payer: search, don't type an ID

Anywhere a flow needs an *existing* payer (e.g. New Bill), the field is a live search,
not a raw-ID box: type a name / payer ref / phone, pick from the matching results. The
payer's internal ID is never entered by hand.

## 7. Public / unauthenticated journeys

- **Bill lookup** — anyone with a `bill_ref` can look up balance and line items (powers
  the print-preview pages and USSD option 1).
- **Receipt verification** — anyone with a receipt's QR/SMS `qr_token` can confirm it's
  real; each check increments `receipt.verified_count`.
- **USSD** — a stateless menu (`1` pay a bill, `2` check balance, `3` verify a receipt)
  driven entirely by the accumulated input string a telco gateway sends per keypress.
