# ACRev360 — Product Requirements Document

**Product:** ACRev360 (Revenue Administration & Collection)
**Deployment:** Kuje Area Council (KAC), Federal Capital Territory, Nigeria
**Consultant:** Alliance Consulting & Digital Solutions Ltd (ACDSL)
**Status:** Demonstration build on illustrative data — see [Production Readiness](TDD.md#production-readiness-gaps) before any real pilot.

---

## 1. Problem statement

KAC collects revenue (rates, licences, fees, levies) through a mix of Council staff and
outsourced sub-consultants, across five payment channels (POS, over-the-counter teller,
internet/mobile banking, USSD, FirstMonie agent banking). Today that means: no single
source of truth for what's billed vs. collected, no way to see whether platform records
match what actually hit the bank, and manual reconciliation between consultants' books
and the Council's.

ACRev360 is the system of record for that whole chain: enumerate a payer, assess what they
owe against the harmonised chart of revenue, bill them, collect through any channel,
receipt it, reconcile it against the bank feed, and settle each consultant's commission —
with an audit trail and per-portfolio segregation throughout.

## 2. Goals

- One harmonised chart of revenue, one payer registry, one bill/payment/receipt record —
  regardless of which consultant or channel touched it.
- Every sub-consultant sees only their own portfolio; the Council sees everything.
- Collections reconcile against the bank feed automatically, with an exception queue for
  what doesn't match.
- Field agents can enumerate payers and record collections without connectivity, syncing
  when back online.
- Every sensitive action (bill edited, rate changed, consultant status changed, debt
  escalated) is attributable and audit-logged.

## 3. Users & roles

| Role | Access level | Can do |
|---|---|---|
| Council Revenue Administrator / Head of Revenue | `COUNCIL_ADMIN` | Everything: onboard consultants and agents, assign revenue-item portfolios, change what a revenue item costs, edit issued bills, run reconciliation, compute settlements, escalate debt, view the audit log. |
| Sub-Consultant Manager | `CONSULTANT` | Everything scoped to their own portfolio: enumerate payers, bill, view payments/receipts, view reconciliation and their own settlements and debt cases, onboard their own field agents, view their agents' activity. Cannot change rates, edit others' bills, or manage other consultants. |
| Field Collection Agent | `AGENT` | Mobile app only. Worklist of assigned-ward payers, enumerate new payers/businesses, collect payments across channels, works offline with sync-on-reconnect. |
| Council Stakeholder | `GLOBAL_VIEW` | Read-only: dashboard and global performance (by consultant, by ward), consultant list. No write access anywhere. |
| Ratepayer (public, unauthenticated) | — | Look up a bill by reference, verify a receipt by QR/SMS code, pay/check balance/verify via USSD. |

## 4. Core features

### 4.1 Harmonised chart of revenue
Revenue categories (Rates, Licences and Permits, Fees and Charges, Registration and
Professional Fees, Levies) → revenue items, each with a harmonised code, unit of charge,
and a versioned rate history. **Only `COUNCIL_ADMIN` can change what an item costs** —
changing a rate closes the old `rate_schedule` row and opens a new one, so history is
never overwritten.

### 4.2 Enumeration (payer registry)
Available to admin, consultant, and agent alike, on both the web portal and the mobile
app. Individual and business registration are separate flows with distinct ID formats
(`IND-xxxxxxx` vs `C-xxxxxxx`) and distinct required fields (NIN/BVN vs TIN). Business
registration also captures a **size classification** (Micro/Small/Medium/Large,
SMEDAN-style MSME bands) — optional, and only for non-individual payers. Revenue items
relevant to the payer can be selected at enumeration time, which records them as `DRAFT`
assessments — priced now, not yet billed.

### 4.3 Assessment & harmonised billing
A bill is however many revenue items a payer is being charged, billed together as one
`bill_ref` — "harmonised" means paying it settles everything on it at once. Two ways to
build one: pick specific items and quantities by hand, or roll up every `DRAFT`
assessment already enumerated for that payer into one bill in a single action ("Issue
Harmonized Bill"). Either way, the bill can optionally **consolidate prior arrears** —
every still-unpaid balance from the payer's earlier bills is brought forward as an
arrears segment on the new bill, and those earlier bills are marked `SUPERSEDED` so the
consolidated bill becomes the single live demand (nothing is owed, or counted, twice).
Admin can subsequently edit an issued bill's lines — add an item, override a specific
line's cost for that payer without touching the item's standard rate, or remove a line —
with the total and status recomputed automatically.

### 4.4 Multi-channel e-payments
POS, over-the-counter teller, internet/mobile banking, USSD, and FirstMonie agent
banking. Each channel posts through one shared code path (`post_payment`) so a payment
looks the same regardless of channel: it updates the bill's balance/status, issues a
receipt, and closes any associated debt case if the bill is now fully paid.

### 4.5 e-Receipting
Every confirmed payment gets a receipt with a QR/SMS verification token. Receipts are
independently, publicly verifiable (`/api/verify/<qr_token>`) — a bank, an auditor, or
the payer themselves can confirm a receipt is real without logging in.

### 4.6 Printable documents
Two distinct real-world documents, both reachable from a bill:
- **Harmonised Demand Notice** — the formal legal notice (Grand Total, Amount in Words,
  14-day payment window), matching KAC's official blank template.
- **Harmonised Demand Bill** — the itemised statement with per-item bill references and
  a Debit/Credit/Balance table, matching the real bills KAC distributes.

Both are print-formatted pages, explicitly separated from *viewing* a bill (which uses
the app's own native detail view).

### 4.7 Reconciliation
Matches platform payment records against the bank's transaction feed per channel per
day. Anything that doesn't match lands in an exception queue rather than silently
disappearing.

### 4.8 Commission settlement
Computes each active consultant's commission (gross collections × their contract rate)
for a period, with a status ladder (`COMPUTED → APPROVED → SETTLED`, or `DISPUTED`).

### 4.9 Debt management
Overdue bills age into buckets (0–30/31–60/61–90/90+ days) and follow an enforcement
ladder (`FIRST_NOTICE → FINAL_NOTICE → ENFORCEMENT → LEGAL → CLOSED`), escalated
one step at a time by admin.

### 4.10 Sub-consultant & agent management
Admin onboards consultants and controls exactly which revenue items each one is allowed
to handle (portfolio assignment/revocation). Consultants onboard their own field agents;
admin can onboard for any consultant or council-direct. A consultant can see their own
team's daily activity (visits, bills issued, amount collected) and recent payments —
never another consultant's.

### 4.11 Offline field operations
The mobile app caches the agent's worklist, queues enumeration/payment records locally
when offline, and replays them to the server on reconnect.

### 4.12 Audit trail
Every state-changing action of consequence is logged: who, what, when, and — where
relevant — the before/after detail.

## 5. Non-goals / out of scope

- Real payment processing — channel webhooks are simulated/illustrative; there is no live
  bank integration.
- Multi-council operation — the platform's schema supports it (every tenant-scoped table
  carries `council_id`), but this build is configured for KAC only.
- A native mobile app — the "mobile app" is a browser-based PWA, not an app-store binary.
- Tiered/banded rates (e.g. liquor licensing by premises size) — the real KAC Gazette
  defines these, but actual distributed demand notices show one flat figure per item, so
  that's what this build reproduces.

## 6. Data notes

Revenue items and codes come from KAC's actual Revenue Item/Revenue Code list; rates
come from the KAC Gazette where it gives a single clean figure. Payers, wards' demo
assignments, sub-consultants, and all transactions are **illustrative demonstration
data**, not real ratepayer records, pending the Harmonisation Workstream and the
Council's confirmed current rates schedule.

## 7. Success criteria (for a real pilot, not this demo)

- Every payment collected through any channel is reflected in the platform within the
  reconciliation window, with a bank-matched receipt.
- Zero un-attributed sensitive actions — the audit log accounts for every rate change,
  bill edit, and status change.
- A consultant can answer "what do I owe the Council this period, and why" from the
  settlement screen alone, no spreadsheet needed.
