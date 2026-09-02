# Screen layout notes (U1)

One short note per phase 1 screen. Full palette, type, and copy rules: `../design-system.md`.

## Dashboard (U4)
Single scroll column on phone, 2-column card grid at desktop. Header card: name, designation, department, manager, location. Below: leave balance card, attendance summary card, this-week timesheet card, pending-items card — each a tappable `Badge`-topped card linking to its page. Quick actions row (Apply Leave, Fill Timesheet, New Request) pinned under the header on phone.

## Profile (U5)
Two sections stacked: "Your information" (read-only, `gray-600` labels, `gray-900` values) and "You can update" (editable `FormControl` fields with inline save). Locked fields in the read-only section that map to an HR Request show a small "Ask HR" text link next to the value.

## Leave (U6)
Top: balance chips per leave type (color = brand-50 background, brand-700 text). Below: tabs or filter chips (All / Waiting / Approved / Sent back). List of leave cards, each with type, dates, day count, status `Badge`. Floating/primary "Ask for leave" button opens a form sheet (mobile: full-screen sheet; desktop: dialog).

## Attendance (U7)
Month calendar grid, one cell per day, colored dot per status (present/absent/half day/holiday, using the three status colors plus gray for holiday). Tapping a day opens a small drawer with check-in/check-out times. Month summary strip above the calendar (counts by status).

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
