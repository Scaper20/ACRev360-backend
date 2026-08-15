# ACRev360 — Session Handoff

A running log of what was broken, what we fixed, and which way we chose to fix it —
written so a fresh chat session can pick this project up without re-deriving any of it.
Not pushed to GitHub (internal working notes, not project documentation — that's
[PRD.md](PRD.md) / [TDD.md](TDD.md) / [APP_FLOW.md](APP_FLOW.md) /
[DESIGN_BRIEF.md](DESIGN_BRIEF.md) / [SCHEMA.md](SCHEMA.md), which *are* meant to ship).

**If you're a new session reading this:** read the five docs above first for what the
product *is*; this file is for what happened *getting it here* and why specific calls
were made the way they were, so you don't accidentally re-litigate or reverse a
deliberate decision.

---

## 1. The project arrived broken, not empty

First inspection found a git repo whose root-level files (`app.py`, `schema.sql`,
`index.html`, `styles.css`, `manifest.json`, `run.sh`, `README.md`, plus a stray
`ACRev360_AMAC_Database_Schema.sql`, `API_REFERENCE.md`, `ARCHITECTURE.md`) each contained
content that **didn't match their filename** — leftovers from a bad initial export,
scrambled relative to each other. The properly-organized, correctly-matching code lived
in `backend/`, `frontend/`, `mobile/`, `docs/` all along.

**Fixed:** deleted every scrambled root file, wrote a real `README.md`, kept the
subfolder structure as canonical. (Commit `4ef8aeb`.)

**Separately discovered while fixing the above:** `frontend/app.js` was **entirely
missing** — not scrambled, just never present. `index.html` referenced it, so the whole
web portal's Sign In button did nothing, silently. Wrote it from scratch: the entire SPA
(dashboard, payers, bills, payments, receipts, reconciliation, settlements, debt,
consultants, agents, terminals, audit), matching the existing `styles.css` component
vocabulary. This is the file basically everything since has been layered on top of.

## 2. Pre-existing backend bug: bill lookup 404'd on every real reference

`GET /api/bills/<bill_ref>` used Flask's default route converter, which doesn't match
`/` — but every generated `bill_ref` (`AMAC/2026/000123`, later `KAC/2026/000123`)
**contains slashes**. This endpoint had never worked, for any bill, ever — not something
introduced this session, just never exercised until we built the demand-notice print
feature and it 404'd immediately.

**Fixed:** switched to Flask's `<path:bill_ref>` converter, which matches slashes
literally. (Folded into commit `4e2b8c3`.)

## 3. Mobile web portal had no navigation below 640px

`styles.css` hid the sidebar with `display: none` under 640px and never provided a
replacement — a logged-in mobile user had no way to switch pages at all. The topbar also
didn't wrap, so title/avatar/sign-out overflowed and got crushed together.

**Fixed:** off-canvas drawer (hamburger button, backdrop, `.sidebar.open` slide-in,
auto-closes on navigation), topbar collapses to one row. (Commit `a8239b0`.)

## 4. Deployment: GitHub Pages doesn't work here, Render does

Asked to put it on GitHub Pages — explained that's static-hosting only and this app
needs a live Flask backend, so Pages was a non-starter regardless of configuration.
Added `render.yaml` (Blueprint, auto-detected) and `Procfile` (Railway) instead, both
running `pip install -r backend/requirements.txt` then `python backend/seed.py &&
python backend/app.py`. Verified the exact build/start commands locally before
committing. **Decision:** the demo database reseeds on every boot by design — see §9,
this is also the top production-readiness gap. (Commit `bccc162`.)

I can't create the Render account or complete GitHub OAuth myself — that's the user's
own login, not something delegable. I prepared everything up to that boundary; the user
completes the click-through themselves.

## 5. The whole app was re-themed from AMAC to KAC using real source documents

The user supplied four real Kuje Area Council documents (a blank Harmonised Demand
Notice template, a real sample of the actual January 2026 distributed bill, the KAC
revenue item/code list, and the KAC Gazette). Original build was themed around AMAC
(Abuja Municipal Area Council) with entirely fictional/illustrative data.

**Decision point, asked directly:** replace AMAC with KAC entirely, add KAC alongside
AMAC as a second tenant, or just borrow KAC's document *format* while keeping AMAC's
identity? **User chose: replace entirely.** (This is why the schema's multi-council
support — every tenant table carries `council_id` — is real but currently only
configured for one tenant; see TDD §8 if a second council ever comes up again.)

Rebuilt: harmonised chart of revenue from KAC's actual 31-item code list (+ "Community
and Development Levy", which appears on every real sample bill but wasn't in that list,
so it got the next code in sequence, `30010062`) with rates from the Gazette where it
gives one clean figure; wards (real KAC ward names); payer ID format `C-xxxxxxx`
matching the real bills; sub-consultant contract-ref prefixes; every AMAC string
anywhere in the codebase (checked via full-repo grep, not just the obvious spots — one
was hiding in a CSS header comment). Moved the four source documents into
`docs/reference/` for provenance. (Commit `4e2b8c3`.)

**Also fixed in the same pass, discovered by re-running the exact steps from the very
first turn of this session:** deleting `backend/acrev360.db` mid-session (cleanup after a
Render deploy test) while the long-running dev server still held it open caused SQLite
to silently recreate an empty, schema-less file at that path — the dashboard started
500ing. Not a code bug, just: don't delete a SQLite file a live process has open;
reseed and restart instead.

## 6. Demand Notice and Demand Bill are two different real documents — got this wrong once

Built a single printable page first, called it "Demand Notice," matching the *sample
real bill*'s layout (Year/Bill Ref/Summary/Arrears/Debit/Credit/Balance table,
Bank's/Local Govt's copy duplicate). **User corrected:** the sample is actually a
**Demand Bill**; the blank template (different document, different layout — Grand
Total, Amount in Words, 14-day window, single copy) is the actual **Demand Notice**.

**Fixed:** renamed the existing file to `demand-bill.html`, built a proper
`demand-notice.html` from the blank template (including a Naira number-to-words
converter for "Amount in Words"). Both now open in an in-page iframe overlay
(`#docViewerBg`) instead of a new browser tab, per a later request — each standalone
page still works if visited directly (its own Print/Close toolbar hides itself when it
detects it's framed). (Commit `df81649`.)

**Later refinement:** the buttons that open these were originally labeled "Demand
Notice"/"Demand Bill," which read as *view* actions. **User clarified:** these are
specifically for *printing* — viewing a bill's contents should use the app's own native
detail view instead. Relabeled to "Print Notice"/"Print Bill," retitled the overlay
"Print Preview — ...", and made sure the bill detail modal (native, not print-styled) is
what a row-click opens by default. (Folded into commit `bc21585`.)

## 7. Feature build: consultant/agent management + enumeration overhaul

One big request covering: admin onboards consultants and controls their revenue-item
portfolio; consultants onboard their own agents; consultants can see their own team's
activity (never another consultant's); only admin can change what a revenue item costs;
individual vs. business enumeration are separate flows with distinct ID formats; revenue
items selected at enumeration become `DRAFT` assessments; a "harmonised bill" is just
rolling up everything a payer currently owes into one bill.

**Key technical decision:** made `payer_ref`/`bill_ref` generation race-safe. They were
built from `SELECT COUNT(*)+1` (a real race under concurrent requests). Switched to a
two-phase insert: write the row with a throwaway-UUID placeholder reference, then derive
the *real* reference from the row's own auto-increment ID and `UPDATE` it in — two
concurrent requests can never collide, because each derives from its own already-unique
primary key. Applied to both tables.

No schema changes were needed for any of this — `consultant_portfolio`,
`agent_daily_return`, and `assessment.status = 'DRAFT'` already existed in the original
schema and just didn't have API/UI built on top of them yet. (Commit `4ea21a7`.)

## 8. Admin can edit an already-issued bill

Added add/edit/delete-line endpoints scoped to `COUNCIL_ADMIN`, with a shared
`_recompute_bill()` helper that re-derives `total_amount` and re-settles `status`
against `amount_paid` after every edit. **Decision:** refuse to delete a bill's last
remaining line (tell the admin to cancel the bill instead) rather than allow a
zero-line, zero-amount bill to exist. (Commit `026c8bc`.)

## 9. UI/UX passes (chronological — later ones refine earlier ones)

1. **Row-click, not button columns.** Every list page (Bills, Receipts, Debt Management,
   Revenue Items, Consultants, Agents — Payers already worked this way) had per-row
   action buttons crowding the table. Converted all of them: click the row, get a detail
   modal with the read view plus whatever actions apply, gated by role. No page has a
   trailing "Actions" column anymore. (Commit `011ec75`.)
2. **Made clickable rows look clickable, separated print from view.** `cursor: pointer`
   alone wasn't a strong enough signal. Added `.row-click` (hover tint) and a trailing
   `›` chevron everywhere. Same pass: fixed default-blue `<a>` links (there was no global
   anchor style at all) and did the Notice/Bill relabel from §6. (Commit `bc21585`.)
3. **"Looks too vibe-coded/generic."** Diagnosed specifically: uniform soft-shadow
   cards, pill badges everywhere, icon-less nav, a stock gradient login hero, a
   placeholder logomark — the classic AI-dashboard tells. Iterated through an
   **Artifact preview** (not the live app) first: warm ivory instead of white,
   Fraunces + Public Sans instead of IBM Plex Sans, softer shapes. User approved the
   direction in preview, then flagged it as "too rigid" (sharp corners read as stiff,
   not warm) — softened radius/shadow/nav treatment in a second preview pass.
4. **Asked for a color palette with more range**, still on white/green. Presented three
   named options (Savanna/terracotta, Harbour/teal, Market/5-hue) as swatches; user
   picked **Harbour**. Applied teal to exactly two spots in the preview — the Outstanding
   stat card and the Internet/Mobile Banking channel segment — deliberately not spread
   further, since the point was "earn its place," not "add a third default color."
5. **User then asked "did you actually apply this, or is it still just the preview?"**
   — correct catch, all of the above had only ever touched the throwaway artifact HTML,
   never `frontend/styles.css`. Applied the full agreed system to the real app: warm
   tokens, font swap (kept IBM Plex Mono for tabular data on purpose — see
   DESIGN_BRIEF §3), softened radius/shadow/buttons/nav, teal in the same two spots.
   (Commit `4f30caf`.)
6. **"Still mostly white/cream — lean into the actual colors."** Fair: step 5 made
   *backgrounds* warm but green/brass/teal still only showed up as small accent dots,
   not in the actual structural chrome. Added real color to table headers (green tint),
   list rows (green zebra stripe at rest, brass hover on plain tables, green hover on
   clickable ones), sidebar section labels (brass instead of muted gray, solid-brass
   active state instead of a faint tint), and a 3px green top edge on content cards
   (deliberately *not* on compact stat tiles, which keep a quieter corner-dot accent so
   four in a row don't compete). (Commit `94aeb24`.)

**If picking this up fresh:** the design system is now genuinely implemented in
`frontend/styles.css`, not just described. `mobile/` was never brought onto this palette
— it's still the original green/brass-on-white IBM Plex look. That's an open item, not
an oversight to "fix" without being asked.

## 10. Documentation checkpoint

Wrote PRD/TDD/App Flow/Design Brief/Schema (commit `d442756`, **not pushed** — user
wants this local for now) by pulling facts fresh from the running codebase (grepped the
actual route list, read the actual `schema.sql`, read the actual `NAV_SECTIONS` in
`app.js`) rather than reconstructing from memory, since this session is long enough that
memory drift was a real risk.

Then this file, for the same reason — also not pushed.

## 11. Standing decisions worth not re-litigating

- **SQLite, not Postgres**, despite the schema being written Postgres-portable on
  purpose (`INTEGER PRIMARY KEY AUTOINCREMENT`, explicit `CHECK`s). Deliberate for a
  zero-ops demo; migrate if this becomes a real pilot (see TDD §8).
- **Vanilla JS, no framework, no build step**, on both `frontend/` and `mobile/`. Keep it
  that way unless there's a concrete reason (state complexity, not aesthetics) to
  introduce one.
- **One money-in path.** Every channel — POS, OTC, IB/MB, USSD, FirstMonie, manual portal
  entry — funnels through `post_payment()` in `backend/app.py`. Don't add a
  channel-specific payment-posting shortcut; extend the shared function or the
  `echannels.py` adapter instead.
- **Print and view are different actions, not the same button.** Don't collapse
  "Print Notice"/"Print Bill" back into a generic "view" — that was explicitly corrected
  once already (§6).
- **The demo database reseeds on every boot, on purpose**, for local dev and the current
  Render/Railway config. Don't "fix" this without it being an explicit ask — it's tracked
  as the top production-readiness gap (TDD §8), not an accident.

## 12. Not yet done (see TDD §8 for the full, prioritized list)

Blocking issues for any real (non-demo) use, in order: DB wiped on every
deploy/restart, webhook signatures unverified by default, unsalted-SHA-256 passwords,
one shared demo password across every account. None of these have been touched — they're
documented, not fixed, and shouldn't be assumed resolved by a future session just
because they're written down.
