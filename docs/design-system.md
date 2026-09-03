# HelixHR Portal — Design System (U1)

> Built from `/ui-ux-pro-max` recommendations plus the `/hallmark` macrostructure pass, filtered for a Frappe app: an extension of frappe-ui's own Tailwind preset, not a from-scratch system. Applied to `frontend/tailwind.config.js` in U2.

## Palette — Signal

The portal's direction, chosen from four studies and resolved against WCAG AA before any of it was
built. **The thesis: one deep field carries the week and the app's edge; everything else rests on
warm paper. Colour is structural — it says where you are, it never decorates.**

Layout variant in use: **B, Framed** — the side nav (and, on a phone, the app bar and tab bar) take
the field, so every page has an identity, not just the dashboard.

| Role | Token / class | Hex | Measured |
|---|---|---|---|
| Page ground | `--surface-gray-1`, `bg-paper` | `#EFEAE4` | — |
| Card surface | `--surface-white` | `#FFFDFB` | — |
| Field | `bg-field`, `blue-800` | `#143D33` | white on it **12.03** |
| Field deep | `bg-field-deep`, `blue-900` | `#0E2C25` | white on it **14.94** |
| Signal yellow | `bg-signal`, `text-signal` | `#FFD24A` | on field **8.35** |
| Primary action | `blue-500` (frappe-ui Button) | `#1E6F53` | white on it **6.08** |
| Primary hover | `--surface-blue-3` | `#1A6349` | white on it **7.18** |
| Link / brand text | `blue-700` | `#14523F` | **8.96** on surface, **7.61** on paper |
| Heading ink | `--ink-gray-9` | `#1A1714` | **14.92** on paper |
| Body ink | `--ink-gray-6/7` | `#514A43` | **8.59** on surface |
| Muted ink | `--ink-gray-4/5` | `#70675E` | **4.63** on paper, **5.46** on surface |
| Hairline | `--outline-gray-2` | `#E2DAD1` | — |
| Waiting | `--ink-amber-2/3` on `--surface-amber-1` | `#8A5A00` on `#FDEFC9` | **5.18** |
| Approved | `--ink-green-2/3` on `--surface-green-2` | `#1E6F53` on `#DCEFE5` | **5.07** |
| Sent back | `--ink-red-3/4` on `--surface-red-2` | `#A8351A` on `#F6E2DC` | **5.28** |

### Two rules that are not negotiable

1. **The signal yellow never touches paper.** It measures **1.21:1** on the page ground — invisible.
   It exists only inside the field (8.35:1). On light surfaces the warm accent is `#8A5A00`. This is
   the single rule that keeps Signal from turning into a highlighter.
2. **Muted ink is `#70675E`, not lighter.** The obvious next step up measured 3.89:1 on paper and
   would fail AA on every empty state and form label — the mistake this document already shipped once.

### Where the brand hue lives

`blue` in `tailwind.config.cjs` **is the Signal green**. frappe-ui's `Button` and `Badge` hard-code
`blue` as their primary theme (and `Button` reaches for a raw `bg-blue-500`), so retuning that scale
is the only way to re-brand those components without forking the library. Read `blue` as "brand"
everywhere in this app. The neutral, ink and status tokens are retuned in `frontend/src/index.css`;
between them, all twelve pages re-skin without touching their markup.

Anti-patterns: gradients, glass, decorative shadow, and any second accent hue. Signal has one
accent and it lives on the field.

## Typography

**Archivo, one family, six roles.** Product UI rarely needs a display/body pairing — it needs one
well-cut grotesque with real weight range so labels, data and headings stay related.

| Role | Size / weight |
|---|---|
| Display figure | 40px / 800 / -3% tracking |
| Page heading | 26px / 700 / -2% |
| Section heading | 18px / 700 |
| Body | 15px / 400 |
| Small, secondary | 13px / 400 |
| Label | 11px / 700 / +10% tracking, uppercase |

Digits that line up in columns take `.tabular` (`font-variant-numeric: tabular-nums`).

> Superseded: the original Lexend + Source Sans 3 pairing. It set headings and body 4px apart at one
> weight, so hierarchy was carried by position alone — one of the three measured causes of the
> "flat" verdict, alongside zero elevation and an accent used only at small sizes.

## Spacing, radius, density

Standard density (8–64px spacing scale) — this is a form- and list-heavy app, not a marketing page, but phones are the primary device so it must not feel cramped. Reuse frappe-ui's own spacing and radius tokens; do not introduce a second scale. Card radius: frappe-ui default (`rounded-lg`). Touch targets: minimum 44×44px on every tappable element (buttons, nav items, table row actions), 8px+ between adjacent tappable elements.

## Component conventions (frappe-ui)

- Use frappe-ui's `Button`, `Badge`, `FormControl`, `Dialog`, `ListView` / `Table`, `Avatar`, and `Tabs` components as-is. Do not restyle their internals — only the theme tokens above change their color.
- Status badges (leave/timesheet/request state) use `Badge` with `theme` mapped to the three status colors above: pending → warning, approved/done → success, rejected → danger, draft/open → gray.
- Icons: SVG only (Lucide, matching frappe-ui's own icon set). No emoji as icons anywhere, including empty states.
- Primary action: one filled `Button` (brand color) per screen. Secondary actions are `Button` with `variant="ghost"` or a plain text link.
- Motion: 150–300ms transitions only, on hover/focus/state-change. No decorative animation. Respect `prefers-reduced-motion`.
- Loading: frappe-ui's built-in loading/skeleton states on every `createResource`/`useList` call — never a blank screen while data loads.
- Forms: visible labels always (frappe-ui `FormControl` labels, never placeholder-as-label). Errors render inline under the field in danger-700, plus a plain-language summary at the top of the form if more than one field errors.

## Layout

Mobile-first. Primary layout is a bottom tab bar (max 5 items: Home, Leave, Timesheet, Requests, More) on phone widths (<768px); a left side nav replaces it at desktop widths (≥1024px). No horizontal scroll anywhere — wide content (tables, week grid) scrolls inside its own container.

Breakpoints: 375px (phone), 768px (tablet), 1024px (desktop), 1440px (wide desktop). Test at 360px minimum per the plan's Verification Contract.

**Implemented in `frontend/src/components/AppShell.vue` (post-U11).** This section described the
shell from U1 onward, but nothing built it until after U11 -- see `docs/runbook.md`, "The app
shell never existed until after U11". As shipped: a 256px left side nav at `lg:` and up (brand,
identity block linking to Profile, nav list with the unread count on Notifications, sign out),
a slim brand+bell app bar below `lg:`, and a five-slot bottom tab bar (Home, Leave, Timesheet,
Requests, More) where More opens a sheet with the rest. Page content is capped at `max-w-5xl`;
before the shell, pages ran the full window width. Approvals appears only for a user with at
least one direct report. `NotLinked` is the one route rendered without the shell
(`meta.shell === false`).

## Copy rules (plain words, no Frappe terms)

| Never say | Say instead |
|---|---|
| Create Leave Application | Ask for leave |
| Docstatus / Submitted | Waiting for [manager] |
| Workflow state: Pending Approval | Waiting for [manager] |
| Workflow state: Approved | Approved |
| Workflow state: Rejected | Sent back |
| Cancel / Amend | Edit and resubmit |
| DocType | (never shown) |
| Employee Self Service role | (never shown) |
| No data | Nothing here yet — [action hint] |

Every empty state names the next action ("You have no leave requests yet. Ask for leave to get started.").

## The ten plain leave-error sentences (R15, used by `errorMap.js` in U6)

Frappe/HRMS leave validation messages, matched by pattern, mapped to a plain sentence. Anything not matched falls back to Frappe's message with HTML stripped.

| # | Frappe message pattern (regex-ish) | Plain sentence |
|---|---|---|
| 1 | `Insufficient leave balance for Leave Type` | "You do not have enough {leave type} for these dates." |
| 2 | `Leave application .* already exists` / overlap | "You already have a leave request that overlaps these dates." |
| 3 | `Application period cannot be across two allocation records` | "These dates cross two leave years. Split the request into two." |
| 4 | `Application period cannot be outside leave allocation period` | "These dates are outside your current leave year." |
| 5 | `There is no leave period` | "There is no leave calendar set up for these dates. Ask HR." |
| 6 | `Total leave days is 0` | "This request has zero days. Check your start and end dates." |
| 7 | `Half day date should be` | "The half-day date must fall within your leave dates." |
| 8 | `To Date cannot be less than From Date` / `To Date .* before .* From Date` | "The end date must be on or after the start date." |
| 9 | `cannot be before .*[Jj]oining [Dd]ate` | "You can't request leave from before your joining date." |
| 10 | `Employee .* not active` / `not associated with any Leave Approver` | "There's a setup issue with your leave approver. Ask HR." |

## Screen layout notes

See `docs/design-system/screens.md` for one short layout note per screen.

## Pre-delivery checklist

Verified by measurement, not by eye — see `frontend/test-results/audit/` and the runbook's
`/impeccable` section for how to re-run the probe.

- [x] No emojis as icons anywhere — inlined Lucide paths via `lib/icons.js`
- [x] `cursor-pointer` on every clickable element
- [x] Hover/focus states, 150–300ms transitions
- [x] Text contrast 4.5:1 minimum on every screen — 45 measured failures, now 0 (the 3 remaining
      probe hits are `disabled` controls, which WCAG 1.4.3 exempts)
- [x] Visible focus ring on every interactive element — was Chrome's default `outline: auto 1px`
      near-black; now a 2px brand-blue ring with offset
- [x] `prefers-reduced-motion` respected, with skeletons pinned to a resting tint rather than
      frozen mid-pulse by the blanket rule
- [x] Responsive at 360px, 768px, 1440px, no horizontal scroll at any width
- [x] Touch targets ≥44px under `pointer: coarse` (frappe-ui's controls are 28px by default)
- [x] Tabular figures on aligned numeric columns
- [x] Browser surfaces themed: focus ring, selection, caret, scrollbar
- [x] Dates formatted for people, not machines (`lib/dates.js`) — the notifications list was
      rendering `2026-09-03 18:47:46.417663`
