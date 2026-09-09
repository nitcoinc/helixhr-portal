# Screen layout notes

**Source of truth: the approved design canvases.** Two of them now:

| Canvas | Covers | Link |
|---|---|---|
| HelixHR Portal Redesign | Dashboard, Profile, Leave, Attendance, Timesheet, Requests, Documents, Approvals, Notifications | the canvas link recorded in `docs/plans/2026-09-04-2319-feat-portal-experience-hardening-plan.md` |
| HelixHR Phase 3 Screens | Payslips, Holidays, check-in, Fix a day, Approvals' attendance kind, Team, Directory | https://claude.ai/code/artifact/b358706d-0ba2-413f-b677-1a0a341017ea |

**The link is the target of record.** `.impeccable/review/` is gitignored, so the PNG exports under
it — `redesign/`, and the `phase3/` export P3-U1 was asked to make — are a local convenience that
no clone and no CI run has. Where this text and a local export disagree, the canvas wins; where
this text and the canvas disagree, the *recorded deviation* below is the decision, and the canvas
is the thing that was traded away. (At the time of writing, `.impeccable/review/phase3/` does not
exist in this working tree, which is exactly why the notes below quote what the artboards show
rather than pointing at a file.)

390×844 phone, 1440×900 desktop, 2× DPR in both canvases. The palette, type roles, copy rules and
the shared patterns below live in `../design-system.md`.

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

**"Celebrating this month" joins the right rail (P4-U5).** One card between the attendance figure
and Documents: up to two labelled groups, `Birthdays` and `Work anniversaries`, each row the
directory's own initials monogram, the person's name, and `9 Sep` — with `· 3 years` on an
anniversary, the one number the card prints, because that is a fact about the job and not about the
person. Today's rows sort first and carry a small `Today` chip. **No photos and no birth year**: the
server projects `day`, `month`, `is_today` and `years` and nothing else, so there is no date string
on the wire to reconstruct an age from, and `formatDayMonth` exists for exactly that reason.

Nothing in the card is clickable. A birthday is something to read on the way past; there is no
detail view behind a name, and a rail of rows that look like controls and do nothing is a rail of
dead tab stops. A group with no people is not rendered, and the whole card is hidden in a month
where nobody is celebrating — but **only when the read genuinely returned nothing**. A failed
celebrations read shows the `AsyncState` retry panel instead, because a failed request rendered as
its empty state is the bug that component exists to prevent (P2-AE8), and "nobody has a birthday"
and "we could not tell" are not the same sentence.

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

**The button row is four wide now, and it is the server's list, not the screen's (P4-U4).** The
screen renders exactly `detail.actions` from `get_approval_detail`, in one fixed order — primary
**Approve**, secondary **Send back**, tertiary **Reject** in the destructive tone, and **Send to
HR** as a quiet link-style button, because handing a request over is a routing act and not a
decision. An outcome the server would refuse is **not rendered**, never rendered-and-disabled: a
greyed-out Reject invites the question "why not?" on a screen that has no room to answer it, and it
is also how a button row and a server drift apart. So a timesheet shows three buttons and an
attendance request four, and neither the screen nor a reviewer has to know why (P4-KTD6, P4-KTD2).

**One reason surface, two outcomes.** Send back and Reject share the field, and opening it from
either arms that action and names it in the heading ("Send back with a reason" / "Reject with a
reason"). Switching from one to the other while the sheet is open **clears the typed reason and its
error and relabels the field**, so a sentence written to send something back can never be submitted
as a terminal rejection. Send to HR opens a smaller, plainly optional note field inline instead —
it is not a reason, and a required-looking field would say otherwise.

**HR work is tagged, not separated.** An HR Manager gets one oldest-first queue mixing their own
reports' work with everything handed to HR; the HR rows carry an "HR" chip beside the name and a
second line, "Sent by Priya · 'needs policy check'", and the detail head repeats both. Two backlogs
to poll would be the alternative, and an HR Manager who is also a line manager would have to poll
both. HR has no Send to HR button anywhere — there is nowhere further to send.

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

## Payslips · phone, detail, desktop (P3-U2 — **built**)

One component serves `/payslips` and `/payslips/:name`. **Field block**: `LATEST PAYSLIP`, the
newest slip's net pay as the display figure in its own currency with no decimals, the period
beneath it, and a signal-yellow **Download PDF** anchor — the page's only use of the accent, and it
is inside the field, as the rule requires. Below, one `.label` run per year of resting cards, each
led by a **date tile**, carrying period, `Gross`, `Deductions` and the net figure in `.tabular`,
with a trailing chevron. Desktop opens the selected slip in a 24rem aside at the same URL; a phone
opens the same `PayslipBreakdown` in a bottom sheet. The breakdown is days paid, unpaid leave,
earnings rows to `Gross pay`, deduction rows to `Total deductions`, `Net pay` on a rule, then the
PDF link — or, for a withheld slip, the on-hold sentence instead.

Amounts are always formatted with the row's **own** `currency` and are never summed across rows;
`lib/money.js` returns an empty string rather than an unlabelled number when a currency is missing.
The PDF is a real GET link the browser follows, never a fetched blob, which is what lets a phone
save or open the file itself.

*Deviations from the artboard, recorded:*

- **The date tile is inverted here**: year over month (`2026` / `AUG`), where every other tile in
  the portal is month over day. It follows the artboard, and it is right for a row that is about a
  *month* rather than a day — but it does mean `.date-tile` carries two semantics across screens,
  which is worth knowing before a third one appears.
- **Years are chips plus count paging, not the artboard's "Show 2025" link.** The list is bounded
  at 20 with a "Show N more" carrying the true remainder (the Leave and Past-weeks rule), and the
  year chips only render when the employee actually has slips in more than one year.
- The breakdown says **`Unpaid leave`** where the artboard says "LEAVE WITHOUT PAY", and
  `Gross pay` / `Total deductions` where it says "Gross" / "Total deductions". "Leave without pay"
  is HRMS's field label, not a sentence anybody says.
- Deductions print with a real minus sign (`−1,234`), which the artboard does not show. A column of
  positive numbers under a heading called Deductions relies on the heading alone to say they come
  off.
- `Withheld` and `Revised` are **mutually exclusive** pills in the breakdown; the artboard draws
  neither. A slip is either on hold or a re-run, and the list row carries the fuller
  "Withheld, ask HR" because there is room for it there.

## Holidays · one column at every width (P3-U3 — **built**)

**Field block**: `NEXT HOLIDAY`, its name, its date, and the countdown in a signal-yellow pill —
`Today`, `Tomorrow` or `in N days`. Below, `COMING UP` and `EARLIER THIS YEAR` runs of resting
cards, each led by a date tile, with the holiday's name, the weekday and the date, and a `Half day`
pill where HRMS says so. Year chips when there is more than one year to show. A footnote naming the
list the days came from and stating that weekly days off are not listed.

The whole page is **one** async region, so the field block, the chips, both groups and the footnote
arrive together — the same rule the Dashboard follows, for the same CLS reason. All date arithmetic
is the server's: `known: false` (no Holiday List Assignment for the employee or their company)
renders the Attendance page's "we can't tell yet, ask HR" wording as the empty state rather than an
empty year.

*Deviations from the artboard, recorded:*

- **No "falls on a weekly off" row.** The artboard draws Diwali as "Sunday · falls on a weekly
  off". `get_my_holidays` excludes weekly-off rows outright (R10), so such a row cannot reach the
  page. The information the artboard was carrying — that some holidays land on days you were off
  anyway — is not lost, it is just not a row: the footnote says weekly offs are not listed.
- The footnote does **not name the days** ("Weekly offs (Sat, Sun) are not listed"). The payload
  carries the holiday rows, not the weekly-off pattern, so naming Saturday and Sunday would be a
  guess that is wrong for anyone on a different week.
- The countdown is a signal pill reading `in 23 days`, not the artboard's large yellow `23` with
  `days` beside it. A display figure next to the holiday's name would compete with it; the pill is
  one reading.
- There is no separate desktop layout. The page is a single `max-w-xl` column at every width —
  there is one list of at most a couple of dozen dates, and a second column would have nothing in
  it.

## Attendance · Today strip and the check-in sheet (P3-U4 — **built**)

The field block gains a second and third band above the existing month counts. **Today**: the
next punch as a signal-yellow `Check in` / `Check out` button — the type comes from the server,
never derived in the browser — plus a line reading `Checked in at 09:14 · location captured` (with
a pin glyph) or `No punches yet in this shift.` When check-in is not available the button is absent
and the server's own `reason` sentence is printed verbatim, so "not set up for you" and "opens at
09:00" are one code path. **Exceptions** keeps its P2-U3 shape and now skips days an approved
request marked.

Tapping the button opens the **check-in sheet**, and opening the sheet *is* the tap that asks the
browser for a location — nothing asks on page load. Four states: `locating` and `confirm` render as
one region (the same button, disabled while the fix is taken, so the sheet cannot reflow under the
thumb); `blocked` gives a per-cause title and instruction plus `Try again` where trying again can
help and a `Fix a day` link always; `range` shows HRMS's own distance sentence verbatim and never
the allow-location advice, because location worked perfectly and the employee is simply somewhere
else. The day sheet lists punches with a pin where the punch carried a location, and a day with
punches but no Attendance row reads **"Checked in, attendance not marked yet"** rather than "No
record".

*Deviations from the artboard, recorded:*

- **The confirmation sheet does not name a place.** The artboard reads "Accurate to about 20 m ·
  Nitco office, Hyderabad". Turning coordinates into a place name needs a reverse-geocoding
  service — a new dependency, a request to a third party carrying an employee's location, and a
  new residency question. The sheet shows the accuracy and nothing else. Accuracy itself is shown
  but not stored: Employee Checkin has no field for it.
- **The notice text is not the artboard's.** The artboard says the location "is not shown to your
  team"; the shipped sentence says "so HR and your manager can see where you checked in from". The
  artboard was simply wrong about who can read it — Employee is a nested set, so every manager up
  the chain can read a report's punches in Desk. A disclosure that describes an access model the
  site does not have is worse than no disclosure, so the accurate sentence won. It is the only
  disclosure kept (no consent record), which is why it is quoted in full in `docs/deployment.md`.
- The sheet title is `Check in` / `Check out`, not "Check in now?" with a `Tuesday 9 Sep · 09:14`
  subtitle. The server stamps the time, not the screen, and printing a clock reading the punch will
  not use is a small lie; the strip above already says what day it is.
- The secondary button is **Cancel**, not "Not now", and the blocked state's primary is **Try
  again** rather than the artboard's "How to allow location". The how-to is two lines of text in
  the sheet; a button that only reveals instructions is a click in front of a sentence.
- **Five blocked causes, not one.** Denied, unavailable, timeout, unsupported and insecure each get
  their own title and body, and the last two do not offer Try again because retrying cannot help.
  The artboard draws the denied case only.

## Fix a day · sheet and stepper (P3-U6 — **built**)

One `Dialog`, two modes on one surface: the **ask** (no `name`) and **one request** (`name` set).
The ask is `WHAT HAPPENED` reason pills, `From` / `To` dates, a `Half day` checkbox, a
server-derived preview block, the approver line `Goes to {manager} for approval.`, an optional
explanation, and a sticky `Send to {first name}`. The preview block is the whole point of the sheet
being server-backed: it names how many days would be marked, how many are skipped as a holiday, a
weekly off or approved leave, and — when any day already carries real attendance — says so and
disables Send with a pointer to HR (P3-KTD14). Send is two calls on purpose, create then send, so a
failed send leaves a draft the employee can retry rather than losing what they typed.

**`StepStrip`** is the request's own progress: three pills, `Sent · {manager} · Counted`, with
done in green, the current step in amber, and a decided-against request tinted red at the step that
decided it, carrying the word — `Sent back` or `Rejected` — in place of the step's own label. It
appears on the request view and on each row of the Attendance page's `Your requests` column.

**A fourth `HR` pill appears only when the request is or was with HR (P4-KTD15).** An attendance
request is approved in one step by the reports-to manager (P4-R6), so HR is not a stage every
request walks through and a permanent HR pill would draw a step most requests never reach. The
strip stays because it is how an employee reads *where* a request is; the single-step change makes
it shorter, not less useful. A plain status line was considered and declined for that reason.

A rejected request is the one place the sheet offers **Remove** rather than `Withdraw`, under the
sentence `This one is final. Remove it to ask for the same days again.` — there is nothing left to
withdraw from, and removing the row is only what frees the dates (P4-KTD3).

*Deviations from the artboard, recorded:*

- **The reason choices are pills carrying HRMS's own words, not the artboard's explained tiles.**
  The artboard draws "Worked from home · Counts as a normal working day" and "On duty elsewhere ·
  Client site, travel or offsite work" as two icon tiles; the sheet renders the reason strings the
  server sends (`Work From Home`, `On Duty`). This is the one place in the portal where an employee
  reads a Frappe field option verbatim, and it is a gap rather than a decision — the two explaining
  sentences exist one level up, on the day sheet's `Fix a day` and `Report a problem` buttons.
  Closing it is a label map in the sheet; the value sent to the server must stay HRMS's.
- **A preview line the artboard does not have.** Nothing on the canvas tells the employee what
  sending would do. It is the feature's main safety property, so it is on the surface that sends.
- Half day is a **checkbox**, not the artboard's toggle switch. The portal has no switch component
  and adding one for a single field would be a seventh pattern.
- The title is `Fix {date}` without the artboard's "There is no attendance record for this day"
  subtitle; the day sheet the employee just came from already said it, and the preview says it
  again in numbers.
- The explanation label reads `Anything your manager should know (optional)`, not "TELL YOUR
  MANAGER". It is genuinely optional, and a label that does not say so reads as a required field.
- The primary button is `Send to {first name}`, not "Send request". The name is the useful half.
- **`StepStrip` cannot show an HR send-back at the HR step.** A decided-against request always
  rests at step 2 (the manager's), because `workflow_state` records which state a request is *in*
  and not which one it left. HR's send-back or reject is still identifiable from the reason quoted
  beside the strip. The upgrade path is one more field on the request projection, and it is noted
  in the component.
- **The retrospective HR pill is not wired up.** `StepStrip` takes a `viaHr` prop and nothing hands
  it in, so a request that went to HR and came back shows no HR pill: `Pending HR` is gone the
  moment HR decides, and the live state is all the component has. Same upgrade path as above — one
  boolean on the request projection, from the `Version` rows.

## Approvals · the attendance kind (P3-U6 — **built**)

A third kind in the same mixed queue, oldest first, with the same row shape: initials tile, the
employee's name, `{reason} · {dates}` as the summary line, `N days` as the amount. The evidence is
the explanation quoted, then one line per requested day saying **what the calendar already shows**
for it (`nothing recorded`, `holiday`, `weekly off`, or the status word), and a warning where the
employee has no holiday list so working days cannot be told. The primary button reads **Approve N
days** again (P4-U4) — the manager's Approve is the submit that writes the Attendance now, so the
label may claim the decision and carry the quantity; `Send back` and `Reject` still require their
reason on the same surface as the evidence.

The `if timesheet else leave` branches that used to run this page and its server helpers were
replaced by an explicit per-kind map first, with a named fallback, so an unknown kind renders as
itself instead of silently inheriting timesheet copy. Pending HR requests **are** in the queue as
of P4 — in the HR half of it, tagged, where P3 kept them out of the portal entirely and left HR's
decision to Desk. The rule that replaced the one they were keeping out for is narrower and applies
to everybody: nobody decides their own request, at any step, on any route (P4-R8).

*Deviations from the artboard, recorded:*

- **The primary button says `Approve N days`, as the artboard does.** P3 overrode the comp with
  `Send to HR`, because the manager's decision was not final and the label must not claim it is.
  P4-R6 made it final, so the override is retired and the comp is right again. The artboard's
  explanatory sentence underneath it — the one promising a further HR check before the day counted
  — is no longer true of any request, and nothing replaced it: what the button does is what it
  says.
- **`Send to HR` survives as a fourth, quiet button rather than as the primary one.** It is the
  hand-over a manager reaches for when a request needs a policy call, and it is the one control on
  this screen the artboard has no equivalent for.
- **The overwrite refusal is a sentence, not a disabled button.** A day inside the range that has
  picked up real attendance since the employee sent the request makes Approve fail with "Some of
  those days now have attendance; send this to HR instead" — the manager cannot read Attendance and
  cannot judge an overwrite, and HR can (P4-KTD5). The refusal names the button that *is* available,
  which is why it arrives as an error rather than as a pre-emptive greyed-out control.
- The day evidence is a `<dl>` of one line per day, not the artboard's single
  `That day today · No record · not on leave` chip. A request can span up to a month, and the days
  are not all alike — one holiday inside a range is the thing a manager needs to see.

## Team · desktop grid, phone day list (P3-U7 — **built**)

**Field block**: `OUT TODAY`, then either a headline naming who is out (`Priya and Sam`,
`Priya, Sam and 2 others`) with one line each, or `Everyone on your team is in today.` — and, when
a decision is waiting, a signal-yellow link into Approvals reading `N requests still waiting —
decide`. Week navigation sits **outside** the async region so the arrows keep working while a week
loads or fails.

Two layouts from one payload. **Desktop**: a `.surface-card` scrolling horizontally in its own
container, a Mon–Sun header, one row per active report with an initials avatar, and leave drawn as
bars clipped to the week — amber for waiting, green for approved, labelled with the leave type and
`half day` where it applies. **Phone**: the same week read downwards, one card per day with a date
tile, a `Weekend` or `Holiday` pill where it applies, and either the people out that day or
`Nobody booked off.` Footnote: the legend, then the scope sentence and `Reasons for leave aren't
shown here.`

The payload never carries a leave `description`, and `status`/`docstatus` are collapsed into one
`waiting` flag before they leave the server (P3-R21).

*Deviations from the artboard, recorded:*

- **Every day of the week gets a row on the phone, not only the days somebody is out.** The
  artboard shows three rows and then "Nobody else is out this week." A week with two people out
  reads as a week when the empty days are visible; a list of only the busy days is a list you have
  to reconstruct the week from.
- **Holiday dimming is per person on desktop and per week on the phone.** R20 asked for "the
  manager's holidays"; the desktop grid dims each row from *that report's own* holiday list, which
  is the only correct answer for a team split across two lists, while the phone's `Holiday` pill
  and the desktop column headers use the manager's. Two rules on one screen, recorded because it is
  a difference somebody will notice before they find the reason.
- There is no `6 people` count beside the page title. The count that matters is who is *out*, and
  the field block leads with it; the footnote carries the "N more people are not shown" remainder
  when the report list is paged.
- The waiting badge reads `waiting` or `waiting on you` on a bar's label rather than the artboard's
  `Waiting for you` pill in the row. The bar already carries the amber tint; a pill the width of
  the row would compete with the bar it sits on.

## Directory · phone sheet, desktop cards (P3-U8 — **built**)

Search first: a labelled field (`Search`, placeholder `Name, role or department`) that queries the
**server**, debounced, floored at two characters. Department chips on desktop only, `Everyone`
plus one per department with its count. Rows grouped per department under a `.label`, each a
resting card with a green initials monogram, the name, and the role — `Role not published` where
HRMS has none. Desktop adds department, `Reports to {manager}` and a `mailto:` link inline; a phone
row opens a bottom sheet with the same facts and an `Email {first name}` action, or
`No work email published. Ask HR if you need to reach them.`

Work email comes from `company_email` only and the key is absent when it is empty, so a login
address cannot reach the directory. There are no photos and no phone numbers, by scope.

*Deviations from the artboard, recorded:*

- **No "Today · On leave · back Fri" row in the person sheet.** The artboard draws one. Leave is
  not in the directory's field allow-list (R22) and putting it there would publish everybody's
  absence to everybody — the manager's view of that is what Team is for.
- **The search field carries a visible label**, where the artboard shows a magnifier and a
  placeholder. Same rule, and the same override, as Documents: "visible labels always" outranks the
  comp.
- **A person has no URL.** The open person is component state, so a refresh closes the sheet. This
  is deliberate rather than an omission: an employee id in a shareable link is a leak with no
  upside. The consequence is that "Reports to" is a link only when the manager is on the fetched
  page, and on desktop following a manager rewrites the search box instead of navigating.
- **This is the one screen with no field block.** Every other new screen has its one anchored
  region; a lookup tool has nothing to anchor — the search field is the thing you came for, and a
  deep green band above it would push it down the page. Recorded as an exception to "one anchored
  region per page" rather than a licence for the next screen.

## Not linked / login states (Phase 1 U3, revised in P2-U2)

Centered single-column message page, no nav chrome. Three states that used to look like one, each
with its own words and one next step: an unlinked account gets the site's HR contact, a service
failure gets Retry and resumes the page that was asked for, an unknown route gets a way Home.
