# Screen layout notes

**Source of truth: the approved redesign canvas**, exported as PNGs to `.impeccable/review/redesign/`
(390×844 phone, 1440×900 desktop, 2× DPR). This file is that canvas written down, so the target
survives without the link; the artboards themselves settle anything this text leaves ambiguous. The
palette, type roles, copy rules and the shared patterns below live in `../design-system.md`.

Each note names the unit that builds it. A screen not yet built to its artboard says so — do not
read the difference as the canvas having changed.

## The shared patterns (built in P2-U3)

Everything below is assembled from these six. They are the whole vocabulary; a screen that needs a
seventh needs a conversation first.

| Pattern | Class | Shape | Where it goes |
|---|---|---|---|
| **Field block** | `.surface-field` + `.elev-2` | deep field, 12px, white ink | The **one** anchored region per page. Signal yellow is legal only inside it. |
| **Resting card** | `.surface-card` + `.elev-1` | paper-white, 8px, hairline | Every list row. A row that opens a record carries a trailing chevron. |
| **Label** | `.label` | 11px / 700 / uppercase / +10% | The only grouping device: a small word above a run of cards. Never a box, never a second surface. |
| **Date tile** | `.date-tile` | 56px, month over a bold day | Any row that is *about a date*, so leave, past weeks and attendance scan down one left edge. |
| **Bottom sheet / inline panel** | frappe-ui `Dialog` | phone: bottom sheet with a handle; ≥768px: bounded dialog | Every form and every detail. One component, two shapes, shaped by `index.css`. |
| **Status badge** | `StatusBadge.vue` | tinted pill, plain-language word | Every leave / timesheet / request status. The word carries the meaning; the tint is redundant. |

Plus three rules that are not patterns but hold everywhere: the muted ink floor is `#70675E`,
**every** button is 44px under a coarse pointer including small secondary ones, and hours, balances,
counts and day numbers always take `.tabular`.

**Async regions.** Every resource-backed region is an `AsyncState.vue` with a *sized* skeleton, a
task-specific empty state that names its next action, a retryable unavailable panel, and a separate
forbidden panel with no Retry. A failed request must never render as an empty list (P2-AE8).

**Page widths.** Content is capped at `max-w-5xl` inside the shell's `<main>`, at every width. The
1440px artboards show exactly that: a 256px side nav, then a 1024px column with 80px of air around
it, not a form stretched across the window.

---

## Dashboard (P2-U4 changes its data, not its look)

**Not redrawn.** It is already the source of every pattern above, and the canvas says so explicitly.

An action queue built on the working week, not a summary of numbers. Top: one identity line (name,
then designation, department, manager and location small beneath it). Then the **week spine** — a
Mon–Sun field, seven equal cells, never a scroller, each carrying an attendance dot, hours as a bar
sized against an 8h day, and leave; today marked by a rule as well as a tint; hours-this-week and a
Timesheet link along the bottom. Below left, **"Needs you"**: rows ordered server-side by urgency
tier then oldest-first, each with its own verb and, for a sent-back timesheet or an HR reply, the
sentence quoted inline; out-of-week rows carry an age tag, and the list discloses "and N more".
Every row carries a stable record identity (its list key) and opens **that** record — the sent-back
week by its Monday, the answered request, the exact decision a manager owes. Under it, a quieter
**"Waiting on others"** run of rows under a `.label`: leave sitting with a manager is still visible
but is not work, so it does not pad a queue called "Needs you" (P2-U4). Right rail:
leave balance, attendance, documents — reference figures, deliberately demoted, and a rail row with
no figure does not render. Quick actions last, as one divided row. Empty queue says "Nothing needs
you." and names the outstanding weekly obligation. Unread count lives on the shell's Notifications
nav item rather than on the page.

The whole body is **one** async region (P2-U3). Every element on it reads from the same
`get_dashboard` response, so painting them before it arrives is what produced the U0 baseline's
0.8431 CLS; the skeleton and the page are alternative subtrees of one region, and nothing that has
been laid out ever moves.

> Superseded: six same-size icon+heading+text stat cards in a 2-column grid, where a sent-back
> timesheet and a leave balance carried identical weight.

## Profile · phone (P2-U3 — **built**)

Identity in the field block: initials monogram in signal yellow, name, then
`designation · department` and `Reports to X · location` beneath it. "YOUR INFORMATION" label over
one resting card of read-only rows, hairline-divided, value right-aligned, with **Ask HR inline on
the row it is about** — on the rows an employee would plausibly need corrected, and nowhere else.
"YOU CAN UPDATE" label over one card of editable fields. **One Save bar for the whole form**, not a
Save button per field: it appears only once something has actually changed, says "N unsaved
changes", offers Discard and Save, and sits above the tab bar inside the safe area (`.action-bar`).

Designation, department, branch and the manager's name come from `get_dashboard`, not from the
Employee document: `frappe.client.get` strips permlevel-1 fields, and the P2-U1 fixtures put all
four behind permlevel 1. "Work email" is the sign-in address from the bootstrap for the same reason.

*Deviations from the artboard, recorded:*

- The page title is the documented 26px `type-page-title` role. The artboard draws it nearer 32px.
  The role wins — the canvas's own rule is that it introduces no new type role.
- The information card carries eight rows (Employee ID, Joined, Work email, Manager, Location,
  Designation, Department, Status); the artboard draws four. The artboard dropped the others to fit
  a 390x844 frame during its own fit review, not because an employee does not need them — these are
  the fields HR corrects most often, and each one absent is a question asked by email instead. The
  cost is that "YOU CAN UPDATE" starts below the fold on a phone, which is acceptable for a screen
  people open to *read* far more often than to edit. Revisit if editing turns out to be the common
  errand.

## Leave · phone, ask sheet, desktop (P2-U5; field block, tiles and badges built in P2-U3)

Balances in the **field block**, one row per type with a used/left bar and the figure always printed
next to it, so the bar is a second reading rather than the only one. Below, leave grouped
**Coming up / Past** with `.label`, replacing the filter pills — each row a resting card led by a
**date tile**, with type, duration, and a status badge that names the approver ("Waiting for
Priya"). A sent-back leave quotes the manager's reason inline in a `.surface-alert` block with
"Edit and resend". The ask sheet shows the balance on each type chip, server-derived working days,
and the approver's name before sending. Desktop opens the selected leave in an inline detail panel
at the same URL.

*Built so far (P2-U3):* the field block, date tiles, status badges and async states. The Coming
up / Past grouping, the inline rejection block and the desktop detail panel are P2-U5's.

## Attendance · phone + day sheet (P2-U5; field block and sheet built in P2-U3)

Month counts in the **field block** with the status dot beside each word, and the R16 exceptions
strip inside it — dormant by design until a check-in device exists, when it resolves to one
explanatory line instead of a wall of red. Monday-first grid below in a resting card, one cell per
day, a status dot per day, an amber ring for a late arrival and a dashed outline for a day with no
record. Legend under the grid. Tapping a day opens the **day sheet**: check-in/out times, the late
badge, and "Report a problem with this day", which prefills an HR Request.

*Built so far (P2-U3):* the field block, and the day panel as a real sheet — it used to be a bare
`fixed bottom-0` div that sat *under* the tab bar, could not be closed with Escape, trapped no focus
and restored none. The Monday-first grid, the legend and "Report a problem" are P2-U5's.

## Timesheet · phone, day-first (P2-U6)

The week spine **is** the day picker: tap a day, and only that day's rows show. Hours move in 0.25
steps through −/+ steppers rather than a text field. "Copy Wednesday" per day. Week total and
workflow status live on the spine. Sticky Save / Submit week above the tab bar.

Chosen over the project-first alternate, which stays on the canvas for reference only.

*Built so far (P2-U3):* the sticky bar clears the tab bar and the safe area, the status badge is the
shared one, and the grid is an async region. The day-first interaction is P2-U6's.

## Timesheet · desktop grid (P2-U6)

Project × day grid, day-total bars beneath, weekend columns dimmed, a per-row note, "Copy last
week", and the approver named next to Submit.

## Past weeks · phone (P2-U6)

Grouped by month with `.label`. Each row is a resting card with the week's range, a bar of hours
against 40h, and the status badge; a sent-back week quotes the manager's reason inline. **Each row
opens that week**, not the current one.

*Not yet true:* rows still link to `/timesheet`. The `/timesheet/:weekStart` route exists (P2-U2)
but `Timesheet.vue` does not read the parameter, so P2-U6 wires both ends at once.

## Requests · phone, detail, new sheet, desktop (P2-U8)

Conversation rows: category as a `.label`, subject, "Sent …", status badge, trailing chevron. HR's
reply is an **attributed bubble** with an initials monogram and an attachment chip, not a "HR:"
prefix. Grouped **Needs you / Open / Closed**. The detail view is a timeline: Sent → Picked up →
Replied. A partial failure is told truthfully — "request sent, file failed", with Retry upload.
The new-request sheet offers the category as four explained tiles. Desktop is list + detail.

*Built so far (P2-U3):* resting cards, category labels, status badges, the reply as an inset block,
async states. The grouping, the timeline detail, the tiles and the upload-retry contract are P2-U8's.

## Documents · phone, desktop (P2-U8)

Type icon, title, description, host name. Grouped **For everyone / \<company\>** with `.label`.
Search above the list, and an "Ask HR" line under it. Desktop is a three-column grid.

*Built so far (P2-U3):* the grouping, the type icon, the host name and the chevron. Search and the
desktop grid are P2-U8's.

## Approvals · phone, desktop (P2-U7)

**One mixed queue**, oldest first, each row led by the employee's initials. On a phone the item
expands in place with a 7-day hours strip. On a desktop the full timesheet — rows, day totals, note
— is visible *before* Approve becomes available. "Send back" requires a reason inline.

*Built so far (P2-U3):* resting cards, the async states, and the copy — the manager's action is
"Send back", the word the employee already sees on the row, rather than Frappe's "Reject". The
single queue, the initials, the hours strip and the evidence-before-approve rule are P2-U7's.

## Notifications · phone (P2-U4 — **built**)

Grouped **Today / Earlier** with `.label`. One resting card per row: an icon tile per kind — a
**filled field-green tile with a signal-yellow glyph while unread**, a grey tile once read — the
subject (semibold while unread), a one-line quote of the reply where there is one, the time, and a
trailing chevron. Under a TODAY heading the row prints the time alone ("16:02"); an Earlier row
keeps its day ("Yesterday, 10:42"). Opening a row marks that one row read, moves the shell's count
in the same interaction, and opens the record — the list is *not* reloaded to find that out, because
`get_notification_logs` is served with a 60s HTTP cache and would hand back the pre-read answer.

*Deviations from the artboard, recorded:*

- The footer reads "Showing your 50 most recent." rather than "Showing the last 30 days". The
  endpoint bounds by count, not by age, and a line that says otherwise is a line that is wrong the
  first time somebody has a quiet month.
- A **timesheet** notification opens **Past weeks**, not the exact week. A week is addressed by its
  Monday (`/timesheet/:weekStart`) and a Notification Log carries the Timesheet's record id, not its
  start date, so the exact link is not derivable from what the row holds. Past weeks is the list
  that contains it; P2-U6 wires that row to the week. Leave and request notifications do open the
  exact record.

## Not linked / login states (Phase 1 U3, revised in P2-U2)

Centered single-column message page, no nav chrome. Three states that used to look like one, each
with its own words and one next step: an unlinked account gets the site's HR contact, a service
failure gets Retry and resumes the page that was asked for, an unknown route gets a way Home.
