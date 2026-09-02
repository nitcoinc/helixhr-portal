# HelixHR Portal — Design System (U1)

> Built from `/ui-ux-pro-max` recommendations plus the `/hallmark` macrostructure pass, filtered for a Frappe app: an extension of frappe-ui's own Tailwind preset, not a from-scratch system. Applied to `frontend/tailwind.config.js` in U2.

## Palette

Base neutrals reuse frappe-ui's own gray scale (do not redefine gray — only add these tokens). One brand color, three status colors.

| Role | Token | Hex | Use | Contrast on white |
|---|---|---|---|---|
| Brand / primary | `--color-brand-600` | `#2563EB` | Primary buttons, active nav, links (large text/icons) | ~4.6:1 (AA large text, AA non-text) |
| Brand / primary text | `--color-brand-700` | `#1D4ED8` | Body-size links, small text on white | ~6.3:1 (AA normal text) |
| Brand / primary bg | `--color-brand-50` | `#EFF6FF` | Selected row, info banner background | — |
| Success | `--color-success-700` | `#15803D` | "Approved" text, success icon | ~4.9:1 |
| Success bg | `--color-success-50` | `#F0FDF4` | Approved badge background | — |
| Warning | `--color-warning-700` | `#B45309` | "Waiting" / pending text | ~4.8:1 |
| Warning bg | `--color-warning-50` | `#FFFBEB` | Pending badge background | — |
| Danger | `--color-danger-600` | `#DC2626` | "Rejected" text, destructive actions | ~4.8:1 |
| Danger bg | `--color-danger-50` | `#FEF2F2` | Rejected badge background | — |

Body text stays on frappe-ui's own `gray-900` (headings) / `gray-700` (body) / `gray-600` (muted, ~7.5:1 — do not use `gray-500`, it is borderline at ~4.6:1 and only safe for large text or icons).

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

## Pre-delivery checklist (carried into U11)

- [ ] No emojis as icons anywhere
- [ ] `cursor-pointer` on every clickable element
- [ ] Hover/focus states, 150–300ms transitions
- [ ] Text contrast 4.5:1 minimum on every screen (per table above)
- [ ] Visible focus ring on every interactive element (keyboard nav)
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive at 360px, 375px, 768px, 1024px, 1440px, no horizontal scroll
