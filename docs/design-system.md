# HelixHR Portal — Design System

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

### Focus ring: two colours, because the portal has two grounds

Keyboard focus is a **3px `#1A1714` ring** on paper (14.92) and switches to **`#FFD24A`** inside
`.bg-field` / `.bg-field-deep` (8.35). A single brand-green ring measured 7.61 on paper but **1.32
against the field** — invisible on the side nav and the phone tab bar, which is the one place
navigation actually lives. It is done with two `outline-color` rules rather than a layered ring
because frappe-ui's own `focus-visible` utilities set `box-shadow`, which silently overrode a halo.

### Where the brand hue lives

`blue` in `tailwind.config.cjs` **is the Signal green**, and so are frappe-ui's `--ink-blue-*`,
`--surface-blue-*` and `--outline-blue-*` CSS variables — those are a *separate* set from the
Tailwind scale, drive Badge blue, Button subtle/outline/ghost and every `text-ink-blue-*` link, and
leaked the old blue into a green portal until they were retuned too. frappe-ui's `Button` and `Badge` hard-code
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

Three of those roles now exist as classes rather than only as rows in this table — `.type-display`,
`.type-page-title` and `.type-section` in `frontend/src/index.css`. They existed here and nowhere in
the code until P2-U3, so each page picked its own Tailwind size and the page title was `text-2xl` on
one screen and `text-xl` on the next. Body and small are the inherited defaults.

**Archivo is self-hosted** (P2-U3, P2-R24). Two variable woff2 subsets (latin, latin-ext, 400–800,
~35KB each) live in `frontend/src/assets/fonts/` next to the SIL Open Font License they ship under;
Vite hashes them and serves them from the app's own asset path. The `@import` from
fonts.googleapis.com is gone: it cost two cross-origin requests on the critical path of every cold
load, from hosts a deployment behind a corporate proxy has no reason to be able to reach. One
variable file per subset covers the whole six-role scale, so this is cheaper than the five static
weights the old import pulled.

> Superseded: the original Lexend + Source Sans 3 pairing. It set headings and body 4px apart at one
> weight, so hierarchy was carried by position alone — one of the three measured causes of the
> "flat" verdict, alongside zero elevation and an accent used only at small sizes.

## Spacing, radius, density

Standard density (8–64px spacing scale) — this is a form- and list-heavy app, not a marketing page, but phones are the primary device so it must not feel cramped. Reuse frappe-ui's own spacing tokens; do not introduce a second scale. Touch targets: minimum 44×44px on every tappable element (buttons, nav items, table row actions), including small secondary ones, 8px+ between adjacent tappable elements.

**A stretched link is still a 24px target on paper.** The list rows on Leave and Requests make the
whole card tappable with `after:absolute after:inset-0` on the row's one link, which is the correct
pattern — but the *link element's own* box stays as tall as its text, and that is what an automated
target-size check measures. Give it a real box:
`-my-2 inline-flex min-h-11 items-center`. The negative margin returns exactly what `min-h-11`
added, so the row's density does not change. The same idiom is used on the inline links in
Timesheet and WeekSpine.

**Radius is not a Tailwind class in this app.** frappe-ui's preset *redefines* the scale — `rounded`
is 8px, `rounded-md` 10px, `rounded-lg` 12px, `rounded-xl` 16px — so "use `rounded-lg`" meant
different things depending on whose Tailwind you had in your head, and the portal shipped with
`rounded-xl` on the dashboard rail and the week spine against `rounded-lg` on every list row with no
rule saying which was right. P2-U3 replaced the instruction with a surface, and writes the radius out
in pixels inside it.

## Surfaces

Five, named for what they mean, declared once in `frontend/src/index.css`. Picking a surface is the
whole of "how should this look"; there is no second decision to make about radius, border or
elevation.

| Class | Radius | Elevation | What it is |
|---|---|---|---|
| `.surface-field` | 12px | `elev-2` | The **one** anchored region per page: the week spine on Timesheet, balances on Leave, month counts on Attendance, identity on Profile. Signal yellow is legal only inside it. |
| `.surface-card` | 8px | `elev-1` | A resting card or list row. |
| `.surface-inset` | 8px | none | A quoted block *inside* a card — an HR reply, a manager's reason. It is inside something, not on top of it. |
| `.surface-alert` | 8px | none | A destructive or blocking callout. |
| `.dialog-content` | 16px top on a phone, 12px on a desktop | frappe-ui's | Overlays. frappe-ui owns the element; `index.css` owns its shape. |

Three more patterns that are not surfaces but belong to the same vocabulary: `.label` (the 11px
group label, and the *only* grouping device — never a box, never a second surface), `.date-tile`
(56px, month over a bold day number, on any row that is about a date), and `.action-bar` (a page's
sticky primary actions, sitting above the phone tab bar and inside the safe-area inset).

## Overlays: one component, two shapes

Mobile forms and details are bottom sheets; at 768px and up the same overlay is a bounded dialog
(R6). This is **frappe-ui's `Dialog` plus CSS**, not a HelixHR sheet component: `Dialog` wraps
reka-ui's dialog, which is what supplies the focus trap, the Escape key, `aria-modal` and focus
restoration on close, and reimplementing those badly is the usual way an app fails WCAG 2.2 in an
overlay. `index.css` pins the panel to the bottom edge on a phone, gives it a grab handle, caps it
at `92dvh`, and clears `env(safe-area-inset-bottom)`.

frappe-ui's own header renders the close control as a ghost `Button` containing nothing but an
`<svg>`, so it has no accessible name. It is named at runtime by
`frontend/src/lib/dialogA11y.js`, which the app shell starts once — not by overriding Dialog's
`body-header` slot, because that slot also carries reka-ui's `DialogTitle`, and the dialog's own
`aria-labelledby` points at the id that component registers. Trading a labelled close button for an
unnamed dialog is not a fix.

One value in there is load-bearing rather than cosmetic: `.dialog-overlay { z-index: 50 }`. The
bottom tab bar is `z-10` and therefore its own stacking context, and reka-ui portals the overlay to
the end of `<body>` with `z-index: auto` — without a value a sheet renders *behind* the tab bar it
is supposed to cover.

## Component conventions (frappe-ui)

- Use frappe-ui's `Button`, `FormControl` and `Dialog` as-is. Do not restyle their internals — only the theme tokens above change their color. **Reaching for another frappe-ui component is a build decision as well as a design one:** `tailwind.config.cjs` scans a named list of frappe-ui components rather than all of them (scanning all of them cost 167,821 bytes of CSS against U0's 162,906-byte budget), so a new one has to be added to `FRAPPE_UI_IN_USE` or it renders unstyled. `frontend/src/lib/frappeUiComponents.test.js` fails until it is.
- Never pass `icon`, `iconLeft` or `iconRight` to a frappe-ui component. Those render `FeatherIcon`, and the Feather set is aliased away in `vite.config.js` (96KB of glyphs this portal does not draw). Add the path to `lib/icons.js` and use `Icon.vue`, which is the design system's rule anyway.
- Status badges use `StatusBadge.vue`, not frappe-ui's `Badge`. It takes a raw Frappe status plus the document kind (`leave` / `timesheet` / `request` / `attendance`) and answers with this product's word — the same status value means different things on different documents, and five pages used to hold five drifting copies of that mapping. The word carries the meaning; the tint is a redundant second channel, so nothing here relies on colour alone. The table itself lives in `frontend/src/lib/statusBadge.js` with a vitest file beside it (a `<script setup>` component cannot export, so the mapping had to leave the component to be testable), and `Cancelled` is keyed on `docstatus === 2` ahead of the table — an attendance request cancelled after approval still stores the state `Approved`, and reading it as "Counted" would be a lie.
- Icons: SVG only (Lucide, matching frappe-ui's own icon set). No emoji as icons anywhere, including empty states.
- Primary action: one filled `Button` (brand color) per screen. Secondary actions are `Button` with `variant="ghost"` or a plain text link.
- Motion: 150–300ms transitions only, on hover/focus/state-change. No decorative animation. Respect `prefers-reduced-motion`.
- Loading, empty and failure: every resource-backed region is an `AsyncState.vue`. It distinguishes five states — pending (a **sized** skeleton), unavailable (a retry panel), forbidden (its own words, no Retry), empty (the task and its next step), ready. A failed request must never render as an empty list; `v-else-if="rows.length === 0"` is true when a request 500s, which is how an outage used to read as "You have no requests yet" (P2-AE8).
- Skeletons reserve the room the answer needs, and a page keeps every element that depends on one response *inside* one region. That is not a detail: unsized skeletons on the Dashboard measured CLS 0.8431 on the U0 baseline, an order of magnitude over R23's 0.1.
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
before the shell, pages ran the full window width. Approvals appears for a user with at least
one direct report, an HR Manager working the HR queue (P4-R13), or -- since phase 5 -- any
holder of a role a request category routes to (`can_work_requests`; `IT Team` is the first).
Settings (`can_configure`) and Organisation (`can_see_organisation`) are phase-5 additions,
both HR-only and both surfaced from the More sheet rather than a bottom-tab slot, since neither
is a destination most sessions visit.
`NotLinked` is the one route rendered without the shell
(`meta.shell === false`).

Revised in P2-U3: the tab bar stays at five destinations, but **More** now lights up when the route
you are standing on lives behind it and its sheet marks that row with `aria-current`, so five unlit
tabs never claim you are nowhere. `<main>`'s bottom padding clears the tab bar *and*
`env(safe-area-inset-bottom)`, and the unread badge is seeded from the bootstrap count so it is
right on the first painted frame rather than a round trip later.

## Copy rules (plain words, no Frappe terms)

| Never say | Say instead |
|---|---|
| Create Leave Application | Ask for leave |
| Docstatus / Submitted | Waiting for [manager] |
| Workflow state: Pending Approval | Waiting for [manager] |
| Workflow state: Approved | Approved |
| Workflow state: Sent Back | Sent back |
| Cancel / Amend | Edit and resubmit |
| Reject (the workflow action that means "sent back") | Send back |
| DocType | (never shown) |
| Employee Self Service role | (never shown) |
| No data | Nothing here yet — [action hint] |
| Workflow state: Pending Manager (Attendance Request) | Waiting for [manager] |
| Workflow state: Pending HR | Waiting for HR |
| Leave Application `helixhr_stage` = HR | Waiting for HR |
| Workflow state: Approved (Attendance Request) | Counted |
| Workflow state: Sent Back (Attendance Request, Timesheet) | Sent back |
| Workflow state: Rejected (Attendance Request) | Rejected |
| Leave status Rejected at docstatus 0 | Sent back |
| Leave status Rejected at docstatus 1 | Rejected |
| Withdraw (a rejected attendance request) | Remove |
| Attendance status: Work From Home | Work from home (calendar legend, day sheet, Approvals) |
| Attendance Request reason | shown as HRMS spells it — `Work From Home`, `On Duty` — see below |
| Create Attendance Request | Fix a day |
| Create HR Request about a day | Report a problem |
| Salary Slip status: Withheld | Withheld, ask HR |
| Salary Slip with `amended_from` set | Revised |
| Employee Checkin | Check in / Check out (a *punch* in prose, never on screen) |
| Salary Slip | Payslip |
| Holiday List | your holiday list |

The phase 3 words, and why each one is the word (P3-U9):

- **Waiting for HR** means a manager handed the request over: it is with the HR
  queue and not with a person. Only `Pending Manager` ever substitutes a
  person's name; `Pending HR` never names an individual, because HR is a queue,
  not somebody the employee can chase. Leave says the same words from
  `helixhr_stage`, so all three kinds read alike (P4-R5, P4-R7).
- **Counted** is the end state, not "Approved". What an employee wants to know
  is whether the day counts as a working day; "Approved" is a status about a
  document. It is also the last step on `StepStrip`, so the word appears
  before it is true, as the name of the thing being waited for.
- **Sent back** is the same word leave and timesheets already use, extended to
  requests, with the reason quoted next to it.

The phase 4 words, and the one ban that was lifted (P4-R21):

- **Reject** and **Rejected** are now in the portal, and that is a change of
  policy, not a slip. Until P4 every no was recoverable — "Send back" was the
  `Reject` action of each lifecycle and the employee could always edit and
  resend — so a word that sounds final would have been a lie, and this table
  banned it outright. P4 gives approvers a genuinely final outcome (P4-R4): a
  rejected leave request is submitted at status Rejected and cannot be resent,
  and a rejected attendance request has no `Edit` transition at all. The
  employee is owed the true word for that, and *Sent back* is no longer it. So
  the ban is now narrower and sharper: **never say "Rejected" for something the
  employee can still fix.** The two are told apart by `docstatus` on leave and
  by two separate workflow states on an attendance request, and
  `statusBadge.js` is the only place that mapping lives. No new colour was
  added for it — Rejected shares the measured sent-back (ink, surface) pair,
  and the word carries the difference.
- **Send back** is the button, and the word, for the recoverable no. It was
  already the portal's word for the `Reject` action; now that a button labelled
  **Reject** exists and means Reject, the two are distinct controls sharing one
  reason sheet, and the sheet relabels itself and clears the typed text when an
  approver switches from one to the other — a sentence written to send
  something back must never be submitted as a terminal rejection.
- **Approve** is the manager's primary button on an attendance request, where
  P3 said "Send to HR". The manager's decision *is* final now: their Approve is
  the submit that writes the Attendance (P4-R6), so the label carries the
  quantity again — "Approve 3 days".
- **Send to HR** is still a label, but for a different control: a quiet
  tertiary button that hands a request over, with an optional note. It is a
  routing act, not a decision, and it is worded and weighted as one. HR never
  sees it — there is nowhere further to send.
- **Remove**, not "Withdraw", on a rejected attendance request. Withdrawing
  implies taking back something still in flight; this row has been decided, and
  deleting it is only what frees the dates to be requested again (P4-KTD3).
- **The HR chip** plus "Sent by Priya · 'needs policy check'" is what marks HR
  work in the queue: a word and a sentence, not a colour.

Under Approvals, one rule that is a product decision rather than a copy one:
**there is no Reject on a timesheet** (P4-KTD2). A week is one Timesheet row, so
a terminal state would lock a week whose hours still have to be recorded.
Timesheets get Approve, Send back and Send to HR, and the button row is drawn
from the server's `actions` list, so nobody can add the fourth by editing the
screen.
- **Work from home** is the sentence-case word everywhere the portal *describes*
  a day: the calendar legend, the day sheet and the manager's Approvals row.
  `Work From Home` in title case is a data value — an HRMS field option, an
  Attendance status and a legend key.
  **The Fix a day sheet is the exception, and it is a known gap.** Its two
  reason pills render the strings the server sends
  (`get_my_attendance_requests().reasons`, falling back to `Work From Home` and
  `On Duty`), so an employee reads HRMS's own title-case options with no
  explanatory line — where the canvas draws "Worked from home · Counts as a
  normal working day" and "On duty elsewhere · Client site, travel or offsite
  work". The two sentences that *were* written live one level up, on the day
  sheet's two buttons. Recorded as a deviation in
  `docs/design-system/screens.md`; the fix is a label map in the sheet, not a
  server change, because the reason value must stay HRMS's.
- **Fix a day** and **Report a problem** are two buttons on the day sheet, and
  the split is the product decision behind P3-KTD10: a work-from-home or
  on-duty day is a request the manager and HR can approve into real attendance,
  and anything else — a missed punch, a wrong time — is a conversation with HR.
  Each carries one sentence saying which is which, so the employee is not asked
  to know the difference.
- **Withheld** is HRMS's own status word, kept because HR uses it and an
  employee will hear it from HR; it is always followed by "ask HR", and the row
  has no PDF, because a withheld slip is not a document to take away.
- **Revised** is the portal's word for a slip with `amended_from` set. "Amended"
  is Frappe vocabulary and "Corrected" would imply the first one was wrong when
  it may simply have been re-run.

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
- [x] No remote font request — Archivo is self-hosted (P2-U3, P2-R24)
- [x] Cold-load CLS at or under R23's 0.1 — measured 0.8431 on the U0 baseline, 0 after P2-U9, on
      the seeded U0 fixture set at the pinned 360px/4× CPU profile
- [x] Every dialog's close control has an accessible name, and every list row link is a 44px
      target in its own right — `frontend/tests/e2e/hardening.spec.ts`
- [x] Installable without an offline cache: a web manifest carrying the Signal field colour, and
      no service worker (`frontend/public/manifest.webmanifest`)
- [x] One surface, one radius rule, one status vocabulary and one async-state region across every
      route — checked deterministically by `frontend/tests/e2e/visual-foundation.spec.ts` at 320,
      360, 768, 1024 and 1440px, at 200% text, under a coarse pointer and under reduced motion
