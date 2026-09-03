# Screen layout notes (U1)

One short note per phase 1 screen. Full palette, type, and copy rules: `../design-system.md`.

## Dashboard (redesigned post-U11 -- supersedes the U4 card grid)
An action queue built on the working week, not a summary of numbers. Top: one identity line (name,
then designation, department, manager and location small beneath it). Then the **week spine** -- a
Mon..Sunday field, seven equal cells, never a scroller, each carrying an attendance dot, hours as a
bar sized against an 8h day, and leave; today marked by a rule as well as a tint; hours-this-week
and a Timesheet link along the bottom. Below left, **"Needs you"**: rows ordered server-side by
severity tier then oldest-first, each with its own verb and, for a sent-back timesheet, the
manager's reason quoted inline; out-of-week rows carry an age tag, and the list discloses "and N
more". Right rail: leave balance, attendance, documents -- reference figures, deliberately demoted,
and a rail row with no figure does not render. Quick actions last, as one divided row. Empty queue
says "Nothing needs you." and names the outstanding weekly obligation. Unread count lives on the
shell's Notifications nav item rather than on the page.

> Superseded: six same-size icon+heading+text stat cards in a 2-column grid, where a sent-back
> timesheet and a leave balance carried identical weight.

## Profile (U5)
Two sections stacked: "Your information" (read-only, `gray-600` labels, `gray-900` values) and "You can update" (editable `FormControl` fields with inline save). Locked fields in the read-only section that map to an HR Request show a small "Ask HR" text link next to the value.

## Leave (U6)
Top: balance chips per leave type (color = brand-50 background, brand-700 text). Below: tabs or filter chips (All / Waiting / Approved / Sent back). List of leave cards, each with type, dates, day count, status `Badge`. Floating/primary "Ask for leave" button opens a form sheet (mobile: full-screen sheet; desktop: dialog).

## Attendance (U7, exceptions added post-audit)
Month calendar grid, one cell per day, colored dot per status (present/absent/half day/holiday, using the three status colors plus gray for holiday). Tapping a day opens a small drawer with check-in/check-out times. Month summary strip above the calendar (counts by status).

**Exceptions strip** below the summary (R16): Absent, Half day, Late arrival, No record — each pill rendered only when its count is non-zero. A late day gets an amber ring around its status dot; a "no record" day gets a dashed cell outline. The whole block is dormant until attendance is genuinely being recorded: no check-in device is configured yet, so `get_my_attendance` reports nothing missing and the strip shows one explanatory line instead. See the runbook for the rule that keeps it from flagging every past day.

## Timesheet (U8)
Week picker (prev/next arrows + "This week" button) at top. Below: one row per timesheet entry (project, task, hours, note) in a simple list on phone, a compact table at desktop. Add-row button. Save draft (secondary) and Submit (primary) at the bottom, sticky on phone. Read-only states show a status `Badge` and, when rejected, the manager's comment in a callout box above an "Edit and resubmit" button.

## Timesheet history (U8)
Simple list of past weeks: date range, total hours, status `Badge`. Tap to view read-only detail.

## Requests (U9)
"New request" primary button opens a form (category select, subject, details, optional file). List below: request cards with category, subject, status `Badge`, and HR's note when present.

## Documents (U9)
Plain list of links grouped loosely by nothing (no folders in phase 1) — title + short description, opens in a new tab. Empty state if HR has not added any yet.

## Notifications (U10)
Bell icon in the header shows an unread count badge. Tapping opens a list (phone: full page; desktop: dropdown panel) of recent items, newest first, each linking to its source page. "Mark all read" action at the top.

## Approvals (U12, managers only)
Two sections: "Leave" and "Timesheets", each a list of pending cards (employee name, dates/week, key facts). Approve (success) and Reject (danger, outline) buttons per card; Reject opens a small comment field before confirming. Nav item only appears for users with at least one pending item or at least one report.

## Not linked / login states (U3)
Centered single-column message page, no nav chrome: icon, one sentence, and (when applicable) a "Contact HR" mailto or a "Try again" action. Same shell used for the unknown-Entra-email message via Frappe's own signup-disabled page copy.
