# acrev360 — User Roles & Permissions Matrix (RLS Design Draft)

**Status: implemented 2026-09-11 — see `docs/RBAC_EXPANSION_DESIGN.md` for what
actually shipped.** This file remains the original planning draft (kept as-is
below for history); the design doc is the authoritative, current reference for
access levels and per-endpoint permissions — two items here (ACDSL's own
council-invoicing feature, and ratepayer self-service *payment*) were
deliberately not built, for reasons explained there.

**Purpose:** Full inventory of user types across the platform, top (ACDSL) to bottom (ratepayers), to drive Row-Level Security (RLS) design on the backend.

**Suggested RLS scoping keys:** `company_id` (ACDSL, fixed) → `council_id` → `consultant_id` (sub-consultant firm) → `agent_id` → `ratepayer_id`. Most rows in the data model should carry `council_id` at minimum, since almost every permission boundary below is "which council(s) can this user see."

---

## 1. ACDSL (Platform Owner)

| Role | Scope | Key Permissions |
|---|---|---|
| **Super Admin** | Global — all councils, all data | Full CRUD on everything; user/role management; can impersonate/view as any role; system config, integration keys (payment channels), audit log access |
| Platform/System Admin | Global | Manage councils (onboard/offboard), manage consultant firms, manage feature flags/module toggles, view all data (read-heavy, limited destructive rights vs Super Admin) |
| Technical/DevOps Admin | Global (infra-level) | API keys, integration configs (POS/USSD/bank/agent-banking), server/DB access, no direct business-data edit rights needed |
| Business Development / Account Manager | Assigned council(s) or all (read) | Read-only cross-council dashboards, proposal/contract status, no ratepayer PII by default |
| Legal/Compliance Officer | Global (read) | Read access to contracts, audit trails, compliance reports; no transactional edit rights |
| Finance/Billing Admin (ACDSL's own commercial side) | Global | ACDSL's own revenue-share/invoice tracking per council — separate from council revenue data |
| Support/Helpdesk | Global (limited) | View user accounts and ticket-relevant data only; password resets, account unlocks; no financial data edit |

---

## 2. Area Council (Client — Government)

| Role | Scope | Key Permissions |
|---|---|---|
| Council Administrator (Chairman/ED/Head of Service level) | Own council only | Full read on council's data; approve/reject major actions (e.g. write-offs); manage council-level sub-users |
| Council IGR Department Head | Own council only | Manage revenue officers/agents for the council, view all council collections, approve reconciliations |
| Council Finance/Treasury Officer | Own council only | View/reconcile remittances, generate financial reports, no agent-management rights |
| Revenue Supervisor/Officer | Own council, possibly own zone/ward | Monitor assigned agents' collections, approve field corrections, cannot edit system config |
| Council Internal Auditor | Own council (read) | Read-only access to all council transactions and agent activity logs |

---

## 3. Consultants

| Role | Scope | Key Permissions |
|---|---|---|
| Lead Revenue Technology Consultant (ACDSL's role at a council) | Assigned council(s) | Full operational oversight of that council's platform use; onboard sub-consultants; cross-module reporting |
| Sub-Consultant Firm Admin | Own firm's portfolio (within a council) | Manage own firm's field agents, view own firm's collection performance only — **not** other sub-consultants' data |
| Sub-Consultant Field Staff/Analyst | Own firm's portfolio | Same as above but view-only, no agent management |

*(This matches the "Consultant Dashboard — own portfolio only" access level already defined for RevAc.)*

---

## 4. Field Agents

| Role | Scope | Key Permissions |
|---|---|---|
| Field Agent Supervisor/Team Lead | Own team, own council/zone | View team's collections, reassign ratepayers/routes, approve agent-submitted adjustments |
| Field Agent / Collector | Own transactions only | Record payments (POS/cash receipt issuance), view own assigned ratepayer list, cannot see other agents' collections |
| POS/Terminal Operator (if distinct from field agent) | Own terminal transactions | Transaction-level access only, tied to device/terminal ID |

---

## 5. Ratepayers

| Role | Scope | Key Permissions |
|---|---|---|
| Individual Ratepayer | Own account/assessment only | View own assessment, payment history, outstanding balance; make payments; download receipts |
| Corporate/Business Ratepayer | Own account only | Same as above, plus possibly multiple linked properties/premises under one entity |
| Ratepayer Proxy/Agent (e.g. accountant paying on a business's behalf) | Delegated ratepayer account(s) only | Needs a delegation model — access granted *by* the ratepayer, revocable |

---

## 6. Shareholders / ABC Group

| Role | Scope | Key Permissions |
|---|---|---|
| Shareholder/Board Member | Global (aggregate, read-only) | High-level dashboards only — total revenue trends, council count, no line-item ratepayer or agent PII |

---

## 7. Roles You May Have Missed

| Role | Scope | Key Permissions |
|---|---|---|
| **Payment Channel Integration Account** (FirstBank, FirstMonie, USSD gateway, POS provider) | System-to-system, per channel | API-level access only — post transaction confirmations, no dashboard login; should be scoped to service accounts, not human RLS |
| **External/Regulatory Auditor** (e.g. FCT IRS, Auditor-General's office) | Assigned council(s), time-boxed | Read-only, likely needs a temporary/expiring access grant rather than a standing role |
| **Oversight/Regulatory Body** (State/FCT revenue oversight) | Cross-council aggregate (read) | Similar to shareholder view but scoped to policy/compliance metrics, not financial detail |
| **Data Analyst/BI Viewer** (ACDSL internal) | Global or assigned council(s), read-only | Access to reporting/analytics layer only, ideally via anonymized or aggregated views, not raw ratepayer records |
| **System/Service Account** (batch jobs, scheduled reconciliation, notifications) | Global, non-human | Needs its own RLS bypass or dedicated elevated scope — flag separately from human roles |
| **Council IT/Data Officer** (if councils have their own technical liaison) | Own council | Manage local user accounts at council level (agents, officers) without touching financial approvals |

---

## Decisions (Settled)

| # | Question | Decision |
|---|---|---|
| 1 | Super Admin audit trail | Full before/after audit logging on all Super Admin/Platform Admin actions — heavier scrutiny than standard roles |
| 2 | Ratepayer Proxy delegation | Linked-account model — proxy has own login, ratepayer explicitly grants/revokes access per account |
| 3 | Sub-Consultant firm visibility | Strict silo — each firm sees only its own portfolio, zero visibility into other firms' data |
| 4 | Field Agent Supervisor scope | Own zone/team only — no cross-zone visibility |
| 5 | Payment channel & system accounts | Modeled entirely separately from the human `users` table — own service-account table with scoped API permissions, not part of the human RLS policy set |

---

## Test Credentials Requirement

A dedicated **test login (email, username, and password)** must be created for every role listed above, to validate RLS boundaries during implementation and QA. This covers:

- All ACDSL roles (Super Admin, Platform/System Admin, Technical/DevOps Admin, BD/Account Manager, Legal/Compliance Officer, Finance/Billing Admin, Support/Helpdesk)
- All Area Council roles (Council Administrator, IGR Department Head, Finance/Treasury Officer, Revenue Supervisor/Officer, Internal Auditor)
- All Consultant roles (Lead Revenue Technology Consultant, Sub-Consultant Firm Admin, Sub-Consultant Field Staff/Analyst)
- All Field Agent roles (Supervisor/Team Lead, Field Agent/Collector, POS/Terminal Operator)
- Ratepayer roles (Individual, Corporate/Business, Ratepayer Proxy)
- Shareholder/Board Member
- External/Regulatory Auditor, Oversight/Regulatory Body, Data Analyst/BI Viewer, Council IT/Data Officer

**Purpose:** each test account should sit in a *different* council/zone/firm/ratepayer scope where relevant, so RLS testing can confirm not just "does this role see the right module" but "does this role see *only* its own council/zone/firm/account and nothing belonging to a sibling scope." At minimum, two test accounts per role (in two different scopes) would catch cross-scope leakage that a single test account per role would miss.

---

*Draft for internal backend planning — figures and role names to be validated against actual AMAC/council contract terms before finalizing schema.*
