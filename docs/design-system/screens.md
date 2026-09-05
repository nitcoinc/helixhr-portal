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

## The action bar sits at the bottom, always

`.action-bar` is `position: sticky`, which pins an element only while it would
otherwise scroll out of view. On a page shorter than the viewport — an empty
timesheet week, a profile with one pending change — there is nothing to stick
against, so the bar sat mid-screen with dead paper beneath it. Three things
make it reach the bottom instead: the shell's inner column carries
`min-h-screen` below `lg:` (above `lg:` the outer wrapper is already a flex
container), `main:has(.action-bar)` becomes a column whose page root fills it,
and the bar takes `margin-top: auto`. That last one needs `!important`:
a page root usually carries Tailwind's `space-y-*`, whose
`> :not([hidden]) ~ :not([hidden])` selector outranks a plain class and would
put a fixed margin back.

Measured at 390x844 the bar's bottom edge lands at 756px with the tab bar at
788px; at 1440x900 it lands at 860px, the foot of the viewport.

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

## Leave · phone, ask sheet, desktop (P2-U5 — **built**)

Balances in the **field block**, one row per type with a used/left bar and the figure always printed
next to it, so the bar is a second reading rather than the only one. Below, leave grouped
**Coming up / Past** with `.label`, replacing the filter pills — each row a resting card led by a
**date tile**, with type, duration, and a status badge that names the approver ("Waiting for
Priya"). A sent-back leave quotes the manager's reason inline in a `.surface-alert` block with
"Edit and resend". The ask sheet shows the balance on each type chip, server-derived working days,
and the approver's name before sending. Desktop opens the selected leave in an inline detail panel
at the same URL (`/leave/:name`).

The whole screen reads from one session-scoped response, `helixhr.api.get_my_leave` — balances,
rows, the approver's *name*, the lifecycle state and the manager's reason. It replaced three browser
calls, one of which was a generic `frappe.client.get_list` against **User**, issued on every page
load to render one word.

**The lifecycle, row by row** (P2-R10). Three of these are `docstatus` 0 and look alike in the
database; they are three different sentences on screen:

| State | What it is | Badge | What the employee can do |
|---|---|---|---|
| open | `docstatus` 0, Open | "Waiting for \<approver\>" | Withdraw (confirmed) |
| sent_back | `docstatus` 0, Rejected | "Sent back" | Edit and resend · Withdraw |
| waiting_for_hr | `docstatus` 0, **Approved** — the P2-U1 legacy defect row | "Waiting for HR" (resting grey) | **Nothing** |
| approved | `docstatus` 1, Approved | "Approved" | Ask HR to cancel (a prefilled HR Request) |

"Waiting for HR" is not a Leave Application status; it is passed to `StatusBadge` as an unmapped
value on purpose, which renders it verbatim in resting grey. It must never read as "Approved" — the
row never consumed balance.

*Deviations from the artboard, recorded:*

- **The phone detail is a full-width panel, not a bottom sheet.** At `lg:` it is the 384px column
  the artboard draws; below that it replaces the list and carries "Back to leave". The canvas draws
  no phone leave-detail sheet at all, P2-R6 allows a full-height treatment where space is
  constrained, and it is the only shape that keeps the detail as **one** block of markup. The
  alternative was writing the same 60 lines twice, once for the aside and once for a sheet slot,
  which is a drift waiting to happen.
- The row's meta line reads "14 Sep – 16 Sep · 3 days · sent 5 Sep" where the artboard has
  "Mon – Wed · 3 days · sent 3 Sep". `lib/dates.js` is the only calendar module and it renders
  dates, not weekday names; adding a weekday formatter for one line is not worth a seventh way to
  spell a date.
- The sent-back row's second control is **Withdraw**, not the artboard's "Dismiss". The only real
  operation on a rejected record is removing it, and "Dismiss" reads as though it merely hides it.
- The whole card is the link (a stretched link on the type, plus the trailing chevron) rather than
  a separate "Details" link beside Withdraw — two interactive elements inside one row that both
  open the same record is a duplicate, and nesting them is invalid markup.
- The detail's "Days" line prints the stored count without "(Sat – Sun skipped)". The skipped set
  is a property of the request *being composed* — it comes back from `get_leave_day_count` — and is
  not stored on the record, so it lives in the ask sheet and nowhere else.
- "Show N more" carries the true remainder from a count query rather than the artboard's fixed
  "Show 6 more"; the first page is bounded at 20 (P2-R22).
- The ask sheet's sticky footer is `sticky bottom-0` inside the sheet's own scroll container rather
  than the page's `.action-bar`, which is positioned against the tab bar and would sit in the wrong
  place inside an overlay.

## Attendance · phone + day sheet (P2-U5 — **built**)

Month counts in the **field block** with the status dot beside each word, and the R16 exceptions
strip inside it — dormant by design until a check-in device exists, when it resolves to one
explanatory line instead of a wall of red. **Monday-first** grid below in a resting card, one cell
per day, a status dot per day, an amber ring for a late arrival and a dashed outline for a day with
no record. Legend under the grid. Tapping a day opens the **day sheet**: check-in/out times, the
late badge, and "Report a problem with this day", which prefills an HR Request with the date and the
status already written into its subject.

The grid ran **Sunday**-first until P2-U5, which made it the one surface in the portal that
disagreed with the week spine, `helixhr.utils.get_week_bounds` and `lib/dates.js` about which column
a date belongs in.

`get_my_attendance` bounds its span at 366 days and refuses a reversed one before reading a row, and
resolves the employee's holiday list **once** per request rather than once per question asked of it.
The day sheet reads `helixhr.api.get_my_checkins`, which states the employee filter and a row cap;
it used to be `frappe.client.get_list` on Employee Checkin with neither.

*Deviations from the artboard, recorded:*

- The check-in row carries a plain "Late" badge, not the artboard's "42 min late". Minutes need a
  shift start time; no Shift Type is configured, and the Attendance record carries a `late_entry`
  flag, not a lateness. The badge tells the truth the record actually holds.
- The field block keeps the four month counts as *status words with dots* plus the P2-U3 exceptions
  strip, rather than the artboard's four large figures. The strip is R16's and was built in P2-U3;
  replacing it with four numbers would drop the late/no-record distinction the strip exists for.
- The day sheet's title is "3 Sep 2026" rather than "Tuesday, 1 Sep" — the same weekday-name
  constraint as Leave, above.
- "Report a problem with this day" has no leading (!) glyph: `lib/icons.js` carries no alert glyph
  and P2-U5 does not add one.

## Timesheet · phone, day-first (P2-U6 — **built**)

The week spine **is** the day picker: tap a day, and only that day's rows show. Hours move in 0.25
steps through −/+ steppers rather than a text field. "Copy Wednesday" per day. Week total and
workflow status live on the spine. Sticky Save / Submit week above the tab bar.

Chosen over the project-first alternate, which stays on the canvas for reference only.

**One model, two layouts.** A *line* is a project + task + note carrying an hours map keyed by
calendar date. The phone renders the selected day's slice of it; the desktop grid renders it whole.
Neither layout owns any state, so the two cannot drift — and the wire format stays what the server
already stores, one row per project/task/note *per day*.

**Save and submit are one server call.** `helixhr.api.submit_my_week(week_start, rows,
expected_modified)` writes the week and applies the workflow transition inside one transaction,
after locking the employee row. The browser used to save, swallow the failure, and submit the
*previously saved* rows anyway (P2-AE4). The same method reopens a sent-back week (Rejected →
Draft) before writing it, because Rejected is not an editable state for an Employee — that reopen
used to be a button called "Edit and resubmit" that performed only the reopen and left the fix
neither saved nor sent. `expected_modified` is the `modified` the screen was rendered from: a
second tap, or a week edited in another tab, is refused rather than transitioning twice.

*Deviations from the artboard, recorded:*

- **The row's project, task and note are the controls**, drawn as borderless selects and a
  borderless input rather than the artboard's plain text. The artboard shows a filled row and no
  way to change it; an edit mode would have been a second state to design and a second place for
  the model to live.
- The week reads "15 Jun – 21 Jun 2026", not "1 – 7 Sep". `lib/dates.js` is the only calendar
  module and its range formatter prints the year — the same constraint recorded on Leave.
- A day with nothing on it prints `0h` rather than the artboard's `–h` over a hollow ring. The ring
  is the Dashboard spine's *attendance* vocabulary; this spine picks days and counts hours, and
  borrowing the ring here would say something about attendance that it does not know.
- Each row carries a trailing `×` to take it off the selected day. The artboard has no remove
  control at all — stepping to zero leaves a row that reads as "booked, zero hours".
- "Copy last week" appears on the phone too, as one full-width button under the day's rows. The
  artboard puts it only on the desktop toolbar, which would leave the phone with the per-day copy
  and no way to start a week from the last one.
- The status badge does **not** name the approver ("Waiting for manager", not "Waiting for
  Priya"). The approver is named once, in the sentence next to Submit, where there is room for it.

## Timesheet · desktop grid (P2-U6 — **built**)

Project × day grid, day-total bars beneath, weekend columns dimmed, a per-row note, "Copy last
week", and the approver named next to Submit. It is a real `<table>` with row headers, inside its
own horizontal scroll container, so the page itself never scrolls sideways.

Two projects on one day — the case the grid exists for — used to be refused outright: ERPNext's
Timesheet rejects time logs whose windows overlap, and every row was written starting at 09:00, so
the second row on a day threw `OverlapError`. A day's rows are now laid end to end from midnight.
The portal books durations and never shows a clock time; the child table stores a window, and this
is what makes one honest.

*Deviations from the artboard, recorded:*

- The note cell is an always-editable field, not a pencil that opens one. A pencil that opens an
  input is two controls for one job.
- The footer says "Goes to Priya Raman for approval." and the save state, but not the artboard's
  "Friday is still empty." Nothing in HRMS says which days an employee owes; 40 hours is context on
  the spine, never a rule, and a nudge built on a guess is a nudge that is wrong for anyone
  part-time.

## Past weeks · phone (P2-U6 — **built**)

Grouped by month with `.label`. Each row is a resting card with the week's range, a bar of hours
against 40h, and the status badge; a sent-back week quotes the manager's reason inline. **Each row
opens that week** by its Monday (`/timesheet/:weekStart`), not the current one.

`get_my_timesheet_history` serves one bounded page (12, up to 52) with the total, and batches every
sent-back week's reason into one Comment query. It replaced a `frappe.client.get_list` asking for
`limit_page_length: 0` — every week the employee had ever filed, to render a dozen — which also
could not show the reason at all, because the Employee Self Service role cannot read Comment. Each
row carries the **Monday** of its week rather than the record's `start_date`: ERPNext recomputes
`start_date` from the earliest time log, so a week whose Monday is empty starts on a Tuesday.

*Deviations from the artboard, recorded:*

- The footer button reads "Show 12 more" from the true remainder rather than the artboard's
  "Show July"; the page is bounded by count, not by month, the same rule as Leave.
- "Avg 38.2 h" is the average of the weeks currently loaded, and says so by moving when more are
  loaded. An all-time average would need a second aggregate query for a decorative figure.

## Requests · phone, detail, new sheet, desktop (P2-U8 — **built**)

Conversation rows: category as a `.label`, subject, "Sent … · picked up …", status badge, trailing
chevron. HR's reply is an **attributed bubble** with an initials monogram, not a "HR:" prefix.
Grouped **Needs you / Open / Closed** — and "Needs you" is *not* a status: it is an unread
Notification Log row about that request, the same read state the shell badge and Notifications use
(P2-KTD6). A reply you have read stops needing you while the request stays Done.

The detail view is a timeline (Sent → Picked up by HR → Replied), then what the employee wrote with
its attachment chips, then HR's reply with its own. A partial failure is told truthfully on the
record it is about — "Your request was sent; only the file failed." with **Retry upload**. The
new-request sheet offers the category as four explained tiles and states the attachment rule before
the file picker. Desktop lists left and shows the record right, at the same URL (`/requests/:name`,
KTD5).

The three timeline dates are the record's own: `picked_up_on`, `replied_on` and `closed_on` are
stamped by `HRRequest.before_save` inside the save that causes them. Reconstructing them from
Version rows was the alternative and is worse twice over — Version stores a JSON diff the Employee
role cannot read, and parsing one per row is the N+1 P2-R22 exists to prevent.

*Deviations from the artboard, recorded:*

- **A step with no date is not drawn.** A request that existed before P2-U8 has no stamps, so its
  timeline says "Sent" alone. An undated dot would claim something happened without being able to
  say when.
- **"Marked as read just now" appears when *this view* cleared the obligation** — opening the
  request from the list, from Home, or from a bookmark. Reaching it from **Notifications** does not
  show the line, because P2-U4's notification row already marked itself read on the way out and the
  detail found nothing left to clear. The caption is a receipt for an action, not a description of
  a state.
- **The failed upload lives in memory.** "Retry upload" holds the bytes the browser already has;
  after a reload the chip is gone and the honest path is to attach the file to the request again.
  No server can hold a file that never arrived.
- **"Ask a follow-up" opens the new-request sheet**, not a reply box. HR Request has no threading,
  and adding one here would be a second conversation model.
- The attachment chip uses the `requests` (document) glyph rather than a paperclip: `lib/icons.js`
  carries no paperclip and P2-U8's file list does not include it. Worth one line in a later unit.
- **The page header stays on the phone detail view**, where the artboard replaces it with the back
  link alone. Leave (P2-U5) already ships that way, and one screen dropping its title and primary
  action while its sibling keeps them is a bigger inconsistency than the extra 56px.

## Documents · phone, desktop (P2-U8 — **built**)

Type icon (document for a `.pdf` address, folder for any other link — derived from the URL, not
stored), title, description, host name. Grouped **For everyone / \<company\>** with `.label`.
A search field and an "Ask HR" line above the list. Desktop is a three-column grid.

The rows come from `helixhr.api.get_my_documents`, which resolves the employee and their company
from the session. The page used to send its own `or_filters` to `frappe.client.get_list`: the
server-side scope that makes the answer safe landed in P2-U1, but as long as the *question* came
from the browser the page still read as though the filter were the boundary.

**Grouping and search ship whatever the row count is**, because the two groups are the permission
model P2-R19 enforces, made visible.

*Deviations from the artboard, recorded:*

- The search field carries a **visible label**, where the artboard shows a placeholder and a glyph.
  "Visible labels always, never placeholder-as-label" is a design-system rule (`design-system.md`,
  component conventions) and it outranks the comp.
- A search that matches nothing gets its **own** line ("Nothing here matches … Ask HR if it should
  be."), not the empty state. An empty catalogue and a narrow search are different facts and P2-R2
  forbids showing one as the other.

## Approvals · phone, desktop (P2-U7 — **built**)

**One mixed queue**, leave and timesheets together, oldest first, each row led by the employee's
initials in a green tile, with the amount (`38.5 h`, `3 days`) and the age (`2 d`) on the right. The
row is a button, not a link, because it both selects and collapses; the selected record is in the URL
either way (`/approvals/:kind/:name`, KTD5).

On a **phone** the row expands in place: a seven-day hours strip (bars scaled against an 8-hour day,
with the number printed under each bar so the bar is never the only reading), then one line per
project/task with its total, the week total, the employee's note, and the two actions.

On a **desktop** the queue keeps the left column and the selected item's full evidence sits in a
36rem panel on the right: a project × day table with a Day-total row, the employee's note quoted, and
only then the reason field and the two buttons. Approve does not exist on the page until that panel
has loaded — the decision is unavailable until the thing being decided is on screen (P2-AE6).

"Send back" requires its reason on the same surface as the evidence, not behind a dialog: the
employee reads that sentence, so it is written next to what it is about. Below the queue, **Decided
this week** lists the last few outcomes with a `StatusBadge`.

*Deviations from the artboard, recorded:*

- The primary button carries the quantity at **both** widths ("Approve 38.5 h", "Approve 3 days"),
  where the artboard shows it on desktop only. One control, one label; a decision that consumes 38.5
  hours or three days of somebody's balance says so on the control that does it.
- **Decided this week** is best-effort for timesheets. A Timesheet's DocShare is removed the moment
  it is decided (P2-U7 scenario 8), so a decided week is only still visible to a manager who can read
  it another way — the nested-set User Permission over their own reports. A leave approver who is
  nobody's manager sees their leave decisions there and nothing else, which is correct: the group is
  a receipt for work this user did, not a record they own.
- Leave evidence adds a **"Left after this"** figure (the balance HRMS reports on the application).
  It is the one number an approver otherwise has to leave the page to find.
- The phone expansion does not reproduce the desktop grid. Seven columns of project × day do not fit
  360px, so the strip carries the shape of the week and the project lines carry the totals; the same
  server projection feeds both.

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
- ~~A **timesheet** notification opens **Past weeks**, not the exact week.~~ **Closed in P2-U6.**
  A week is addressed by its Monday and a Notification Log carries the Timesheet's record id, so
  P2-U6 added `helixhr.api.get_timesheet_week_start` — one indexed, session-scoped read, issued
  only when a timesheet row is actually opened. Every notification kind now opens its exact record.
  Past weeks remains the fallback for a record that no longer resolves.

## Not linked / login states (Phase 1 U3, revised in P2-U2)

Centered single-column message page, no nav chrome. Three states that used to look like one, each
with its own words and one next step: an unlinked account gets the site's HR contact, a service
failure gets Retry and resumes the page that was asked for, an unknown route gets a way Home.
