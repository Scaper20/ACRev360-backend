# ACRev360 — Design Brief

The visual and interaction system for the web portal (`frontend/`). Source of truth is
`frontend/styles.css`; this document explains the *why* behind it so future changes stay
coherent instead of drifting back toward a generic admin-template look.

---

## 1. Positioning

ACRev360 is a government revenue authority's system of record — not a startup SaaS
dashboard. The design should read as **institutional but not cold**: an official
document you'd trust, not a template you'd recognise from a hundred other admin panels.
Concretely, that means avoiding the defaults that make AI-generated interfaces look
alike — uniform soft-shadow cards, pill badges on every status, an icon-less nav, a
stock gradient hero — in favour of choices specific to this product: a real council
identity, a document-adjacent feel (KAC's actual printed forms are a legitimate visual
reference), and color that's actually used, not just implied by a tint.

## 2. Color

Base palette is white/cream **and green** — green is the brand color, cream is the
ground it sits on, brass and teal are accents used deliberately, not decoratively.

```css
/* Green — the brand color, used in nav, buttons, table headers, card accents */
--green-900: #06281D   /* sidebar, deepest ink-on-dark */
--green-800: #0B3D2E   /* login brand panel, headings-on-dark */
--green-700: #13543F   /* primary buttons, card top accent, links */
--green-600: #1C6B51   /* hover states, "ok" status */
--green-100: #E7EFE3   /* table header background, active-row tint, "ok" tag background */

/* Brass — secondary accent: sidebar section labels, active nav, one stat accent */
--brass:     #C08B2C
--brass-lt:  #EFE0BC   /* row hover, "brass" tag background */

/* Teal — tertiary accent, used in exactly two places: don't spread it further
   without a reason as specific as these two */
--teal:      #2B6E7A   /* the Outstanding stat card's accent dot;
                           the Internet/Mobile Banking segment of the channel flow strip —
                           chosen because that channel is genuinely "digital/bank-mediated"
                           in a way POS/agent-banking aren't */
--teal-lt:   #DCEAEC

/* Ground */
--canvas:    #F6EFDF   /* page background — warm ivory, never plain white */
--surface:   #FFFDF6   /* card/table background — warm off-white */
--surface-2: #FBF5E6   /* input fields, secondary surfaces */
--ink:       #241D12   /* body text — warm near-black, not pure #000 */
--ink-60:    #4A3F2C   /* secondary text */
--ink-40:    #8C7F63   /* muted/caption text */
--line:      #E6DABA   /* borders */
--line-soft: #EFE6CE   /* table row dividers */

/* Semantic (separate from the accent palette — never repurpose these for decoration) */
--ok:    #1C6B51 / --ok-bg: #E7EFE3
--warn:  #8A5A00 / --warn-bg: #F6E8C8
--danger:#9A3324 / --danger-bg: #F5E3DB
```

**Rule of thumb:** if you're reaching for a new color, ask whether green, brass, or teal
already carries that meaning elsewhere. Two accent colors (brass, teal) is the ceiling
for this system — a third would dilute what each one signals.

## 3. Typography

Three faces, three jobs — don't blur them:

| Face | Role | Where |
|---|---|---|
| **Fraunces** (serif, 500/600) | Display — carries personality | Big numbers on stat cards, page/modal/document titles, the "ACRev360" wordmark, the login headline |
| **Public Sans** (sans, 400/500/600) | UI chrome | Body text, labels, buttons, nav, table cells — everything functional |
| **IBM Plex Mono** | Tabular data | Any column of numbers or reference codes (`bill_ref`, `payer_ref`, amounts) — set via `.num`/`.mono`, always with `font-variant-numeric: tabular-nums` so figures actually align |

Public Sans was chosen partly for what it signals: it's the US government's own design
system typeface, which reads as *civic* rather than *startup*. IBM Plex Mono is kept
specifically for data because a real monospace face is what makes columns of currency
actually scannable — don't swap it for a "cleaner" sans just for consistency.

## 4. Shape & elevation

- **Radius scale:** `--radius: 14px` (cards, modals, the document viewer),
  `--radius-sm: 9px` (buttons, inputs, small chips). Not uniform on purpose — bigger
  surfaces get more room to round, small controls stay tighter.
- **Shadow is for things that float**, not for every card: `--shadow` (a soft, warm-tinted
  double shadow) is reserved for cards/panels that sit above the page; modals and the
  document viewer get a stronger, more saturated shadow because they're actually
  overlaying content. Don't add shadow to inline/flat elements (tags, table rows) — a
  hairline border or background tint does that job instead.
- **Primary actions are pills** (`border-radius: 999px` on `.btn-primary`/`.btn-brass`);
  everything else (`.btn-ghost`, `.btn-sm`) uses the small radius. This is a deliberate
  hierarchy signal, not an aesthetic default — a screen with five pill buttons in a row
  has lost the hierarchy.

## 5. Color in structure, not just accents

The lesson from this system's own design process: a warm *background* isn't the same as
an interface that actually *uses* its palette. Concretely, this build puts real color
into the structural chrome, not just decorative dots:

- Table headers carry a green tint (`--green-100`), not a flat cream background.
- List rows carry a faint green zebra tint at rest (`rgba(28,107,81,.045)`), brass on
  hover for plain tables, green on hover for clickable rows — three distinct states, not
  one.
- Sidebar section labels are brass; the active nav item is a **solid** brass fill, not a
  tint.
- Content cards (anything wrapping a table or chart) get a 3px green top edge. Compact
  stat tiles deliberately don't — they keep a quieter corner dot so four of them in a row
  don't compete with each other.

If a future change makes a screen feel flat again, the fix is almost never "add more
cream" — it's "which of green/brass/teal should actually be present here."

## 6. Components

- **Stat cards** — label (caption, `--ink-40`) / value (Fraunces, large) / delta
  (`--ink-60`), with a small corner dot signaling category: green = default, brass =
  primary metric for the page, teal = informational/secondary.
- **Tags/badges** — solid-tint pills (`background` + matching `color` from the same
  semantic pair), never bare text for status.
- **Lists are click-through, not button rows** — see [APP_FLOW.md](APP_FLOW.md) §3. A
  clickable row gets `.row-click` (hover tint) and a trailing `.chev` (`›`) so
  interactivity is visible without needing to hover first.
- **Print vs. view are visually and functionally separate** — a bill's own detail modal
  is the "view" surface (native UI, editable inline for admins); "Print Notice"/"Print
  Bill" are explicit, separately-labeled actions that open a print-formatted preview in
  an overlay, never the default click target.
- **Nav** — grouped under uppercase brass section labels; active item is a solid brass
  chip, not a block; hover is a teal wash.

## 7. Voice

Short, direct, no filler. Errors say what happened and (where relevant) what to do about
it — no "Oops!", no exclamation points. Buttons are verbs ("Issue Harmonized Bill",
"Escalate", "Change Rate"), not vague labels ("Submit", "Go"). Empty states name what's
missing rather than apologising for it ("No open debt cases", not "Nothing here yet!").

## 8. Responsive behavior

Below 640px: the sidebar becomes an off-canvas drawer (hamburger toggle, slide-in over a
backdrop, auto-closes on navigation) rather than disappearing with no replacement. The
topbar collapses to a single row (subtitle and role caption hide first). Tables scroll
horizontally inside their own `.table-wrap` rather than breaking the page layout.

## 9. What's deliberately not here yet

- No custom iconography — nav items and buttons are text-only. A considered icon set
  (matching the Fraunces/Public Sans pairing, not a generic stroke-icon library) is the
  next-highest-leverage visual change if this goes further.
- No real brand mark — the "RA" logomark is a placeholder monogram, not a designed seal.
- Mobile (`mobile/`) has not been brought onto this palette/type system — it still runs
  the original green/brass-on-white IBM Plex look.
