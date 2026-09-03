# HelixHR Portal — Design System (U1)

> Built from `/ui-ux-pro-max` recommendations plus the `/hallmark` macrostructure pass, filtered for a Frappe app: an extension of frappe-ui's own Tailwind preset, not a from-scratch system. Applied to `frontend/tailwind.config.js` in U2.

## Palette

**Correction after inspecting the installed `frappe-ui@0.1.278` package (U2):** frappe-ui already ships full `gray`/`blue`/`green`/`amber`/`red` Tailwind color families plus semantic `ink-*` / `surface-*` / `outline-*` CSS-variable classes via its `tailwind` preset (`tailwind/preset.js` → `plugin.js` → `colorPalette.js`). Do **not** invent new `--color-brand-*` custom tokens — reuse the existing families directly as Tailwind classes. This is fewer moving parts and stays consistent with every other frappe-ui component on the page.

| Role | Use these classes | Hex (light mode, from `frappe-ui/tailwind/colors.json`) | Contrast on white |
|---|---|---|---|
| Brand / primary | `bg-blue-600 text-white` (buttons), `text-blue-700` (links, small text) | `#007BE0` / `#0070CC` | blue-600 on white ~4.7:1; blue-700 ~5.6:1 |
| Brand bg | `bg-blue-50` | `#F2F9FF` | — |
| Success | `text-green-700`, `bg-green-50 text-green-700` (badge) | `#137949` / `#F2FDF4` | ~5.9:1 |
| Warning | `text-amber-700`, `bg-amber-50 text-amber-700` (badge) | `#B35309` / `#FDFAED` | ~5.1:1 |
| Danger | `text-red-600` / `bg-red-50 text-red-600` (badge) | `#CC2929` / `#FFF7F7` | ~5.4:1 |
| Body text | `text-ink-gray-9` (headings), `text-ink-gray-7` (body), `text-ink-gray-5` (muted) | frappe-ui semantic tokens | AA at every level used |
| Background / border | `bg-surface-white`, `bg-surface-gray-2`, `border-outline-gray-2` | frappe-ui semantic tokens | — |

Do not use `gray-500`/`amber-600`/`red`-on-white for small text directly — use the `-600`/`-700` shades and semantic `ink-*` tokens above, which are the ones frappe-ui itself uses for text.

### Measured correction (post-U11 `/impeccable audit`)

The "Contrast on white" column above was **estimated, and several entries were wrong**. Measured
off the rendered portal (page background is `surface-gray-1` #F8F8F8, not white, which costs
~0.25), these failed WCAG AA and are now corrected in `frontend/src/index.css`:

| Token / class | Was | Measured | Now | Ratio |
|---|---|---|---|---|
| `--ink-gray-5` (all muted text, empty states, labels) | gray-600 `#7C7C7C` | 3.93 | `#707070` | 4.66 |
| `--ink-amber-2/3` (Badge "Waiting for …") | amber-600 `#DB7706` | 3.02 | `#B35309` | 4.83 |
| `--ink-green-2/3` (Badge "Approved") | green-600 `#278F5E` | 3.70 | `#137949` | 4.62 |
| `--surface-green-3` (Approve button) | green-600 | 4.06 | `#137949` | 5.44 |
| `--surface-blue-3` (primary button hover) | blue-600 | 4.28 | `#005CA3` | 6.86 |
| `blue.500` in `tailwind.config.cjs` (primary Button) | `#0289F7` | 3.54 | `#0070CC` | 5.01 |

Two naming traps worth knowing: the compiled utilities read `--ink-*`, while frappe-ui's Tailwind
plugin source names the same values `--text-ink-*` — overriding the latter builds clean and
changes nothing on screen. And frappe-ui's solid blue `Button` is the one component that reaches
for a raw `bg-blue-500` instead of a semantic token, so it can only be corrected in the Tailwind
theme, not with a CSS variable.

**Anti-patterns to avoid** (from the style search): bright neon accents, harsh/instant animations, dark mode (not in phase 1 scope — see brief Deferred).

## Typography

**Pairing: "Corporate Trust"** — Lexend (headings) + Source Sans 3 (body). Chosen for its accessibility focus and "corporate, trustworthy, readable" mood, matching the portal's plain-words tone better than a generic dashboard/mono pairing.

```css
@import url('https://fonts.googleapis.com/css2?family=Lexend:wght@400;500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap');
```

```js
// tailwind.config.js fontFamily
fontFamily: {
  heading: ['Lexend', 'sans-serif'],
  sans: ['Source Sans 3', 'sans-serif'], // body default, matches frappe-ui's own base
}
```

Base body size 16px, line-height 1.5. Headings use `heading`, everything else (labels, buttons, table cells) stays on frappe-ui's default body font so form controls don't visually clash with frappe-ui components.

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
