# ACRev360 — Technical Design Document

Companion to [PRD.md](PRD.md) (what/why) and [SCHEMA.md](SCHEMA.md) (data model in
detail). This document covers how the system is actually built.

---

## 1. Architecture overview

```
┌─────────────────┐      ┌──────────────────┐      ┌───────────────────┐
│  Web portal       │      │  Mobile PWA         │      │  Public / e-channel │
│  frontend/*.html   │◄────►│  mobile/*.html       │◄────►│  citizens, USSD,     │
│  + app.js (SPA)     │      │  + app.js (offline)  │      │  bank webhooks       │
└────────┬─────────┘      └────────┬───────────┘      └─────────┬─────────┘
         │  fetch() JSON               │  fetch() JSON                    │  HTTP
         └───────────────┬──────────────┴───────────────────────────────┘
                          ▼
                ┌───────────────────────┐
                │   Flask API (backend/app.py) │
                │   Bearer-token sessions       │
                │   Role-gated routes            │
                └───────────┬───────────────┘
                            ▼
                ┌───────────────────────┐
                │   SQLite (backend/acrev360.db) │
                │   backend/schema.sql          │
                └───────────────────────┘
```

Single Flask process serves the JSON API, the web portal's static files, and the mobile
PWA's static files, all from one origin — no CORS, no separate frontend build/deploy.

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Flask (Python), `sqlite3` stdlib | No ORM, no build step — every query is plain SQL in `backend/app.py`. Deliberately minimal for a demo. |
| Database | SQLite | Zero-ops for a demo; schema (`backend/schema.sql`) was written Postgres-compatible in spirit (`INTEGER PRIMARY KEY AUTOINCREMENT`, `CHECK` constraints, explicit `REFERENCES`) so a future Postgres migration is a port, not a rewrite. |
| Frontend | Vanilla JS SPA, no framework/build | `frontend/app.js` renders everything via template-literal HTML strings into a single `#pages` container. No React/Vue, no bundler, no npm install. |
| Mobile | Same pattern, separate app (`mobile/`) | PWA with a service worker (`mobile/sw.js`) and `localStorage`-backed offline queue. |
| Styling | Hand-written CSS (`frontend/styles.css`), no framework | Design tokens as CSS custom properties — see [DESIGN_BRIEF.md](DESIGN_BRIEF.md). |
| Fonts | Google Fonts: Fraunces (display), Public Sans (UI), IBM Plex Mono (tabular data) | Loaded via `<link>` in `index.html`. |
| Deployment | Render (`render.yaml`) or Railway (`Procfile`) | `gunicorn`-free — runs Flask's dev server directly; see gaps below. |

## 3. Repository structure

```
backend/
  app.py          Flask app — every API route
  echannels.py    Per-channel payload validate/normalise/verify-signature adapters
  schema.sql      SQLite DDL — source of truth for the data model
  seed.py         Deletes + recreates acrev360.db with demo data (idempotent)
  requirements.txt
frontend/
  index.html      Web portal shell (login + app chrome)
  app.js          Entire web portal SPA logic
  styles.css      Design tokens + all component CSS
  demand-notice.html   Standalone printable Harmonised Demand Notice
  demand-bill.html     Standalone printable Harmonised Demand Bill
mobile/
  index.html, app.js, manifest.json, sw.js    Field-agent PWA
docs/
  PRD.md, TDD.md, APP_FLOW.md, DESIGN_BRIEF.md, SCHEMA.md   This documentation set
  ARCHITECTURE.md, API_REFERENCE.md                          Prior architecture/API docs
  reference/                                                 Source KAC documents (Gazette, code list, demand notice samples)
render.yaml, Procfile   Deploy config
```

## 4. Backend design

### 4.1 Auth
`POST /api/auth/login` checks `hash_password()` (SHA-256, see [gaps](#production-readiness-gaps))
against `app_user.password_hash`, then mints an opaque token stored in an **in-memory**
`SESSIONS` dict (`token -> {user_id, access_level, consultant_id, ...}`). Every other
route wraps in `@auth_required(*allowed_levels)`, which reads the `Authorization: Bearer
<token>` header, looks it up in `SESSIONS`, and 401s/403s accordingly. No token expiry.

### 4.2 Per-portfolio scoping
A `CONSULTANT` session only ever sees rows tied to their own `consultant_id` — enforced
per-endpoint (`portfolio_filter()` in dashboard queries; explicit `WHERE consultant_id =
?` clauses elsewhere), not by a database-level policy. This is the pattern to formalise
first if this ever runs on Postgres (row-level security).

### 4.3 Money-in path
Every payment — POS, teller, bank transfer, USSD, agent banking, or manual portal entry
— eventually calls the single `post_payment()` function: inserts the `payment` row,
updates `bill.amount_paid`/`status`, issues a `receipt`, and closes any open `debt_case`
if the bill is now fully paid. This is the one place money changes state — channel
adapters (`echannels.py`) only normalise inbound payloads before handing off to it.

### 4.4 Bill editing & recomputation
`_recompute_bill(bill_id)` re-derives `total_amount` from `SUM(bill_line.line_amount) +
bill.arrears_amount` and re-settles `status` against `amount_paid` after every line
add/edit/delete. A bill can't be stripped to zero lines (the API refuses to delete the
last one — cancel the bill instead).

### 4.4a Arrears consolidation
Issuing a bill with `roll_arrears` sums the payer's prior open balances
(`ISSUED`/`PART_PAID`/`OVERDUE`) into `bill.arrears_amount`, adds it to the total, and
flips those prior bills to `SUPERSEDED` (with `superseded_by` linking them to the new
bill); any open `debt_case` on a superseded bill is closed in the same pass. Superseded
bills drop out of every live-outstanding query (which all filter on the three open
statuses), so the carried-forward amount is never double-counted. The bill-level
aggregates (`dashboard`, `global`) count `total_amount - arrears_amount` as *billed* so
the arrears — already billed once on the original bills — isn't re-counted, keeping
`billed − collected = outstanding` exact through a consolidation.

### 4.5 Reference generation (race-safety)
`payer_ref` and `bill_ref` are generated two-phase: insert the row with a throwaway
UUID placeholder, then derive the real reference from the row's own auto-increment ID
and `UPDATE` it in. This makes the reference generation itself immune to races — two
concurrent requests can never produce the same reference, because each derives from its
own already-unique primary key rather than a racy `COUNT(*)+1`.

### 4.6 e-Channel integration
`echannels.py` defines one adapter per channel implementing `validate()` /
`normalise()` / `verify_signature()`. Real-time channels (POS, USSD, FirstMonie, IB/MB)
land via `POST /api/channels/<code>/webhook`; OTC lands via an end-of-day settlement
file (`POST /api/channels/OTC/settlement`). Idempotency is enforced by a
`UNIQUE(channel_id, bank_txn_ref)` constraint on `channel_transaction_feed` — a replayed
webhook returns the original result instead of double-posting.

### 4.7 Audit logging
`audit(action, entity_type, entity_id, detail)` inserts into `audit_log` from inside
the route handler that performed the action, capturing `g.current_user`, a JSON detail
blob, and the requester's IP.

### 4.8 Custom reports
`GET /api/reports/meta` publishes the report catalog; `GET /api/reports` runs one.
`REPORT_DATASETS` is a server-side registry mapping each dataset to a fixed base query
and a **whitelist of selectable fields** (`alias → [label, SQL expression]`). Only
whitelisted aliases are ever interpolated into SQL, so field selection carries no
injection surface; filter values are always bound parameters. Datasets flagged `scoped`
carry a bill alias so `portfolio_filter()` restricts a `CONSULTANT` to their own rows,
identical to the rest of the app. Results cap at 1000 rows; CSV export is built
client-side from the returned JSON (no token in a URL).

## 5. Frontend design

- **No routing library.** `go(page)` swaps `#pages.innerHTML` and updates the active nav
  link; `state.page` tracks the current page for refresh.
- **No component framework.** Every `render*()` function fetches its data and returns a
  template-literal HTML string. Row/list interactions bind via inline `onclick="..."`
  attributes calling global functions — deliberately simple, no build step required.
- **List pages are click-through, not button-per-row.** Every list (Bills, Payers,
  Receipts, Debt, Revenue Items, Consultants, Agents) renders `tr.row-click` rows; a
  click opens a detail modal holding both the read view and whatever actions apply,
  gated by `state.user.access_level`. See [APP_FLOW.md](APP_FLOW.md).
- **Document viewer.** `demand-notice.html`/`demand-bill.html` are standalone pages (so
  they still work if opened directly), loaded into an `<iframe>` inside an in-app
  overlay (`#docViewerBg`) rather than a new tab — each hides its own Print/Close
  toolbar when it detects it's framed (`window.self !== window.top`).
- **State** lives in one `state` object (`token`, `user`, `page`) plus a handful of
  `window.__*` scratch variables for data being edited in the currently-open modal
  (e.g. `window.__editingBill`). Session persists via `sessionStorage`.

## 6. Mobile (field agent) design

Same vanilla-JS-no-build pattern as the web portal, with two additions:
- **Service worker** (`sw.js`) for installability.
- **Offline queue**: `S.queue` (persisted to `localStorage`) holds records captured
  without connectivity; `POST /api/mobile/sync` replays them server-side on reconnect,
  reporting `accepted`/`conflicts` per record.

## 7. Deployment

`render.yaml` / `Procfile` both run: `pip install -r backend/requirements.txt` (build),
then `python backend/seed.py && python backend/app.py` (start) — **the database is
recreated from scratch on every boot.** This is intentional for a demo (guarantees a
clean, consistent dataset every time) and is the single biggest reason this build is not
production-ready as deployed — see below.

## 8. Production readiness gaps

Carried over from an explicit review of this build; not yet remediated. Ordered by
severity.

**Blocking (would lose data or lose money in real use):**
- Database resets on every restart/redeploy (`seed.py` runs on every boot) — any real
  bill, payment, or payer would be wiped on the next deploy or dyno restart.
- `POST /api/channels/<code>/webhook` doesn't verify signatures unless
  `ACREV360_STRICT_SIGNATURES=1` is set — off by default, so an unsigned request could mark
  a bill paid without money moving.
- Passwords are hashed with plain unsalted SHA-256 (`hash_password()`), not
  bcrypt/argon2/scrypt. `payer.nin_bvn_hash` has the same weakness on sensitive PII.
- Every demo account shares one password (`acrev360-2026`), published in the repo.

**Should fix before any real pilot:**
- SQLite is single-writer — fine for a demo, not for concurrent field-agent + council
  usage at real volume. The schema was written to be Postgres-portable for this reason.
- Sessions are an in-memory dict with no expiry — a process restart logs everyone out,
  and it can't run across more than one server instance.
- Running Flask's built-in dev server (`app.run(...)`), which prints its own warning not
  to use it in production. Needs `gunicorn` (or similar) behind a real deploy.
- No automated test suite — every check performed on this codebase so far has been
  manual, live-browser verification.
- Revenue rates are illustrative placeholders, not the Council's actually-approved
  schedule.

**Worth hardening, not urgent:**
- No rate limiting, security headers, or enforced HTTPS config.
- No structured logging/monitoring/alerting beyond `audit_log`.
- Currency math uses Python floats (`REAL` in SQLite) rather than fixed-point/Decimal.
- No database migration tooling — schema changes mean editing `schema.sql` and
  re-seeding, which is fine now and won't be once there's real data to preserve.

All of the above describe the **v1 Flask/SQLite prototype**, not this repo (which has no
code yet — see [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md)). They're kept here as the
concrete list of gaps the v2 rewrite exists to close: Django + DRF on PostgreSQL from day
one, Alembic-equivalent migrations via Django's own migration framework, `Decimal`/
`NUMERIC` money throughout, argon2 passwords, HMAC signatures verified by default, a
real pytest suite, and gunicorn behind nginx — see V2_ARCHITECTURE.md §2, §7, §8, §10.
