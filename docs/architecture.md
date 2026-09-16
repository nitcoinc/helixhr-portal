# Architecture

How a request travels, where permissions are enforced, and what each file owns.
Read this before changing `api.py` or adding a screen. Operational steps and
gotchas are in `runbook.md`; product intent is in `../PRODUCT.md`.

## One sentence

A Vue single-page app served by a Frappe `www` page calls a handful of
whitelisted Python methods as the logged-in user; Frappe's own permission
system, plus fixtures this app ships, decides what that user may see and do.

## Request path

```
browser  GET /helixhr/anything
   -> hooks.website_route_rules maps /helixhr/<path> to the www page "helixhr"
   -> www/helixhr.py get_context: CSRF token + boot (site config) into the HTML
   -> www/helixhr.html (built by Vite from frontend/index.html) loads the bundle
   -> Vue Router (frontend/src/router.js) owns the path from here

one hard load
   -> router.beforeEach awaits lib/session.ensureBootstrap()
   -> POST helixhr.api.get_portal_bootstrap  (once, P2-R20)
      no active Employee  -> /not-linked, no shell, HR contact
      request failed      -> /unavailable, no shell, Retry
      Employee            -> lib/session state filled, shell renders

every navigation after that
   -> reads lib/session; no identity or capability request at all
   -> an unknown path      -> /:pathMatch -> not found, with a way Home

page data
   -> frappe-ui createResource / lib/api.apiRequest
   -> POST /api/method/helixhr.api.<method> with the session cookie
   -> Frappe runs the method as frappe.session.user
```

`lib/api.js` wraps frappe-ui's `frappeRequest`: a 401, an `AuthenticationError`,
or a 403 whose message contains "Login to access" redirects to `/login`; a 417
CSRF error reloads; any other 403 is an in-app error and must stay one.

## Security model

There is no app-level auth code. Three Frappe mechanisms carry it:

1. **Session.** Every call is a browser session cookie; the SPA never holds a
   token. `allow_guest` is never set, so Guest gets `PermissionError`.
2. **User Permission on Employee.** Each portal user is scoped to their own
   Employee record. With System Settings "Apply Strict User Permissions" on,
   that scope also filters every doctype that links to Employee (Leave
   Application, Timesheet, Attendance, HR Request). This is the whole
   authorization boundary; `preflight.py` checks every linked employee has one.
3. **Permlevel lock on Employee.** `fixtures/property_setter.json` moves every
   Employee field an employee may not edit to permlevel 1 (HR-only fields such
   as bank details to permlevel 2), and `patches/v1_0/apply_permission_deltas.py`
   gives the Employee role read-only at level 1 and nothing at level 2. The seven editable
   fields (mobile, personal email, addresses, emergency contact) stay at level 0.
   `api.update_my_profile` also drops any field outside those seven before it
   touches the document, so the UI is never the only guard.

4. **Scoped controller permissions on this app's own doctypes.** HelixHR
   Document Link registers both `permission_query_conditions` and
   `has_permission` in `hooks.py`, so global-plus-own-company scoping applies
   to every route (portal method, `frappe.client.get_list`, `/api/resource`,
   report view, print, export), not only to the query the browser sends. Its
   `company` field is marked `ignore_user_permissions` deliberately: a Company
   User Permission would hide the *global* links too, because strict user
   permissions refuse a document whose scoped link field is empty. The scope
   is owned by the hooks instead, so it does not depend on which User
   Permissions a site happens to have created.

   HR Request's `employee` field is marked the same way, for the same reason
   from the other direction: `employee` is still empty when `insert()` checks
   create permission (`before_insert` runs after it), so under strict user
   permissions *every* create was refused. The read boundary for HR Request is
   `if_owner` plus `HRRequest.before_insert` resolving `employee` from the
   session.

Writes the employee should not be able to make are refused server-side:

- `save_my_week` refuses a week that is not Draft or Sent Back, refuses projects
  the user is not assigned to, and is rate-limited per user.
- `act_on_approval` locks the native row (`SELECT ... FOR UPDATE` on
  `modified`), then re-checks that the caller is the approver (`reports_to`
  for timesheets, `leave_approver` for leave), then checks the record is still
  undecided and matches the caller's optional `expected_modified` token, and
  only then adds a comment or applies the transition. That order is the point:
  it used to comment first, so an unauthorized caller left a comment on
  somebody else's record.
- `events.timesheet_before_submit` refuses a submit by anyone but the approver
  or HR even if a Desk user finds another route to it, and refuses anybody
  submitting their own week (P4-R8).
- `events.leave_application_before_submit` and
  `events.attendance_request_before_submit` carry the same rule for the other
  two kinds, and the leave one also refuses a non-HR submitter on a request
  that is already with HR.
- `events.file_before_insert` refuses a public file attached to an HR Request.

## Leave approval runs the native lifecycle

`act_on_approval` on a Leave Application **submits** it (`docstatus` 1). That
is what makes HRMS write the Leave Ledger Entry, consume balance and update
attendance; setting `status = "Approved"` alone does none of it, and the row it
leaves behind (`docstatus` 0, status Approved) is a defect state that
`preflight.check_unsubmitted_approved_leave` counts and
`patches/v1_0/report_unsubmitted_approved_leave` lists for HR to resolve in
Desk. A send-back stays unsubmitted, so it consumes nothing.

**One HRMS status, two meanings, told apart by `docstatus` (P4-KTD4).** HRMS
has one word for both kinds of no, so the portal reads the pair:
`status = "Rejected"` **saved** is the recoverable send-back the employee edits
and resends, and `status = "Rejected"` **submitted** is the final no. A
submitted Rejected application writes no Leave Ledger Entry — HRMS's `on_submit`
accepts Approved and Rejected and only Approved touches the ledger — and
`docstatus` 1 is what makes the row unresendable. `api._leave_state` reports
`sent_back` for the first and `rejected` for the second; `statusBadge.js` keys
the badge on the same pair.

### Leave has a stage, not a Workflow (P4-KTD4)

P2-KTD17 still stands: Leave Application gets no Workflow, because HRMS's own
lifecycle is the correct one. "Send to HR" and "HR approves" are therefore one
Custom Field, `helixhr_stage` (Select `Manager` / `HR`, default `Manager`), read
by the queue collectors and by `_may_act_on_leave`. `leave_approver` stays the
manager throughout, so HRMS's own validation and its `submit=1` DocShare keep
working; the stage is what takes the request out of the manager's hands.

Three things make that true outside the portal too, because role Employee has
write on its own open application and HRMS shares every one of them with its
approver at `submit=1`:

- The field sits at **permlevel 1**, with `apply_permission_deltas` giving HR
  Manager read/write there and nobody else. A generic save by the employee or
  the manager silently resets it — the same lock the Employee permlevel rows
  already use.
- The portal therefore writes it with **`db_set` after its own authorization**.
  A permlevel-1 field set through `doc.save()` by a permlevel-0 session is
  reverted on the way in, so `apply_for_leave` inserts and then sets the stage,
  and `Send to HR` authorizes and then sets it.
- `events.leave_application_before_submit` refuses a non-HR submitter while the
  **stored** stage is HR, and refuses anybody submitting their own application
  (Administrator exempt, as in the other two submit hooks). The stored value is
  what counts: an in-memory one from a non-HR session was already reset, and
  reading the row is also what makes a raw `frappe.client.submit` answerable.

`Leave Type.helixhr_hr_approves` is the other half: `apply_for_leave` reads it
and inserts the application straight into stage HR, so the manager never sees
it and the employee reads "Waiting for HR" from the moment they send it.

**Submit permission path (decided, and tested):** the portal calls `doc.submit()`
with **no `ignore_permissions`**, after its own approver/HR check. The grant is
already there natively:

- Employee is a nested set, so a manager's own User Permission on their Employee
  record covers every Employee below them — and therefore their reports' Leave
  Applications.
- HRMS auto-grants the **Leave Approver** role whenever `Employee.leave_approver`
  is set through a real save, and that role carries `submit` at permlevel 0.
- An approver *outside* the reporting line instead gets the `submit=1` DocShare
  that `hrms.hr.utils.share_doc_with_approver` creates on every save.

`test_api_approvals.TestLeaveApprovalIsNative.test_the_approvers_submit_grant_is_native`
asserts that grant exists, so an upstream change that removes it fails here
rather than in production.

Two HR Settings carry rules the portal must not re-implement, and
`preflight.py` FAILs without them: `leave_approver_mandatory_in_leave_application`
(no approver, no request) and `prevent_self_leave_approval`.

**Permission deltas are a patch, not a fixture.** Frappe *discards* a doctype's
standard `DocPerm` rows once that doctype has any `Custom DocPerm` row rather
than merging them (`frappe.permissions.get_valid_perms`). Shipping Custom
DocPerm rows as fixtures therefore removed every role the fixture filter did not
name — on a fresh site that cost Leave Application and Timesheet their HR
Manager, HR User, Leave Approver and Projects User rules entirely. Widening the
filters would have frozen this machine's Frappe/ERPNext/HRMS rows into the app,
so instead `patches/v1_0/apply_permission_deltas.py` calls
`frappe.permissions.setup_custom_perms` to snapshot **each site's own** standard
rows and then applies only this app's deltas on top: the Employee permlevel 1/2
rules, `if_owner` delete on Leave Application (KTD17), `submit` on Timesheet for
role Employee, and the removal of the Employee role's unused `share` on Leave
Application. P3-KTD13 added two more on the same mechanism: role Employee loses
`create`, `write` and `delete` on **Employee Checkin** (`read` stays) and loses
`share` on **Attendance Request**. Because Frappe skips a patch whose exact
line is already in the Patch Log, `patches.txt` carries a second, dated line
for the same module so a site that migrated before phase 3 applies the new
deltas — the module is idempotent by construction, every step being "make this
row look like this". `preflight.check_custom_docperm_coverage` is the standing
guard — it now walks the patch's own `DELTAS` keys rather than a hard-coded
list, and FAILs when a doctype named there has no Custom DocPerm row at all,
which is what a patch that never ran looks like. The patch runs once, so
anything that trims these rules later shows up there.
HRMS's own Employee Self Service rules are never touched; removing document
sharing site-wide is System Settings' "Disable Document Sharing", not a
permission rule.


## Portal bootstrap, and whose calendar it is (P2-U2)

`helixhr.api.get_portal_bootstrap` is the one session-scoped read the shell
makes per hard load: the active Employee, `can_approve`, the
initial unread count, and the calendar contract. It replaced a
`hrms.api.get_current_employee_info` call in the router guard that ran on
*every* navigation, plus the shell's separate `frappe.client.get_count` for
direct reports.

**It is not authorization.** `can_approve` decides whether the Approvals nav
item is drawn and nothing else. Every domain method still resolves
`frappe.session.user` itself and is still refused by Frappe permissions —
see "Security model" above.

**Whose "today".** Server-derived dates come from `helixhr.api.user_today()`,
which reads `User.time_zone` for the authenticated user and falls back to the
site's System Settings timezone. `frappe.utils.today()` is the site's day, not
the user's, and is no longer used in `api.py` for anything user-facing. The
bootstrap returns `time_zone`, `system_time_zone`, `today`, `week_start` and
`week_end`, and `frontend/src/lib/session.js` hands the first three to
`configureCalendar()` in `lib/dates.js`.

**The two shapes.** `lib/dates.js` treats `"2026-09-03"` as a calendar value —
never parsed as an instant, never converted, so it cannot shift a day west of
UTC (it used to render as "2 Sep" in a Los Angeles browser, and `2026-01-01`
as "31 Dec 2025"). A real timestamp such as `"2026-09-03 18:47:46.417663"` is
a naive wall-clock reading in the *site's* zone, converted to the *user's*
zone for display. Week arithmetic is integer y/m/d, Monday..Sunday, matching
`helixhr.utils.get_week_bounds`. `frontend/src/lib/dates.test.js` pins all of
it across Asia/Kolkata, America/New_York and America/Los_Angeles.

## Exact-detail route convention (P2-R12)

Every queue row, notification, list item and approval opens an addressable
URL that survives refresh and browser Back. One convention, defined in
`frontend/src/router.js`:

| List | Detail | Parameter |
|---|---|---|
| `/leave` | `/leave/:name` | Leave Application name |
| `/requests` | `/requests/:name` | HR Request name |
| `/approvals` | `/approvals/:kind/:name` | `kind` is `leave`, `timesheet` or `attendance` (P3-U1) |
| `/timesheet` | `/timesheet/:weekStart` | the week's Monday, `YYYY-MM-DD` |
| `/payslips` | `/payslips/:name` | Salary Slip name (P3-U2) |
| `/attendance` | `/attendance/requests/:name` | Attendance Request name (P3-U6) |
| `/holidays` | — | the year is page state, not a parameter (P3-U3) |
| `/team` | — | the week is component state, deliberately neither a parameter nor a query (P3-U7) |
| `/directory` | — | search, department and the open person are page state (P3-U8) |
| `/notifications` | — | a row links to the target record's route above |

- The parameter is the record's real Frappe name, or for a week its Monday as
  a plain calendar date. Never an index and never an offset from "now": both
  change meaning on refresh, which is the thing P2-R12 forbids.
- `:weekStart` is constrained to `\d{4}-\d{2}-\d{2}`, so `/timesheet/history`
  stays its own route and a malformed week falls through to not-found rather
  than rendering an arbitrary week.
- Route names are stable PascalCase — `LeaveDetail`, `RequestDetail`,
  `ApprovalDetail`, `TimesheetWeek`. Link by name.
- Every detail route sets `props: true`; the page takes the id as a prop.
- **Three phase 3 screens deliberately carry no parameter**, and the reason is
  the same in each case: the convention addresses *records*, and a week, a year
  and a search are views. Team's week, Holidays' year and Directory's search,
  department and open person are component state, so a refresh returns to this
  week / this year / the unfiltered first page. Directory goes furthest on
  purpose — a person sheet has no URL at all, because an employee id in a
  shareable link is a leak with no upside.
- Attendance carries one non-record parameter, the query `?fix=<date>`, which
  opens the Fix a day sheet for that day. It is what lets the check-in sheet,
  Home and a notification point at "fix *this* day", and it is
  `router.replace`d away when the sheet closes so browser Back does not reopen
  it.
- A detail route renders the same page component as its list, which reads the
  id from its prop and asks the server for that one record
  (`get_my_leave_detail`, `get_my_request`, `get_approval_detail`,
  `get_my_week`). The list is context, not a prerequisite: the detail route
  is reachable directly, and refresh and browser Back both land on the same
  record.

Three routes are states rather than pages, all rendered by `NotLinked.vue`
with `meta.shell: false`: `/not-linked` (signed in, no Employee — shows the
site's HR contact), `/unavailable` (the bootstrap failed — Retry, and it
resumes the destination in `?retry-to=`), and the `/:pathMatch(.*)*` catch-all
(unknown URL — Home). A Guest never reaches any of them: `lib/api.js` sends
them to `/login?redirect-to=<the full portal path>`.


## Data flow per screen

| Screen | Reads | Writes | Backing records |
|---|---|---|---|
| Dashboard | `get_portal_bootstrap`, `get_dashboard` | none | Timesheet, Leave Application, HR Request, Attendance, Notification Log |
| Leave | `get_my_leave`, `get_my_leave_detail`, `get_leave_form_context`, `get_leave_day_count` | `apply_for_leave`, `withdraw_my_leave` | Leave Application, Leave Allocation, Leave Ledger Entry |
| Attendance | `get_my_attendance` (now with `checkin`), `get_my_checkins`, `get_my_attendance_requests`, `get_my_attendance_request`, `get_attendance_request_preview` | `punch_my_checkin`, `create_my_attendance_request`, `send_my_attendance_request`, `withdraw_my_attendance_request` (a non-fixable problem still opens a prefilled HR Request) | Attendance, Employee Checkin, Holiday List, Leave Application, Attendance Request, Shift Type + Shift Assignment, Workflow "Attendance Request Approval", DocShare, Notification Log |
| Payslips | `get_my_payslips`, `get_my_payslip` | none (`download_my_payslip` is a GET that returns a PDF) | Salary Slip + its earnings and deductions rows, Print Format |
| Holidays | `get_my_holidays` | none | Holiday List, Holiday List Assignment |
| Team | `get_my_team_week` | none | Employee (`reports_to`), Leave Application, Holiday List |
| Directory | `get_directory` | none | Employee |
| Timesheet | `get_my_week`, `get_my_timesheet_history`, `get_timesheet_week_start`, `get_my_projects` | `save_my_week`, `submit_my_week` | Timesheet + Timesheet Detail, Workflow "Timesheet Approval" |
| Requests | `get_my_requests`, `get_my_request` | `create_my_request`, `attach_to_my_request`, `mark_my_request_read` | HR Request, File, Notification Log |
| Documents | `get_my_documents` (`frappe.client.get_list` is scoped by the same hooks) | none | HelixHR Document Link |
| Notifications | `notification_log.get_notification_logs` | `notification_log.mark_all_as_read`, `mark_my_request_read` | Notification Log, fed by the Notification fixtures and `events.hr_request_on_update` |
| Approvals | `get_my_approvals`, `get_approval_detail` | `act_on_approval` | Leave Application, Timesheet, Attendance Request, Workflow actions, DocShare |
| Profile | `get_portal_bootstrap` header, `frappe.client.get` on own Employee | `update_my_profile` | Employee |

Every method in the first two columns without a package prefix is
`helixhr.api.<name>`. That is the point of the table: apart from Documents'
list route, the notification log and the employee's own Employee record, no
screen reaches a generic `frappe.client` route any more.

### Why so many thin methods (P2-R27)

A generic `frappe.client.insert`/`get_list`/`delete` call is shaped by the
*caller*. That is fine for a read Frappe's own permissions fully constrain,
and wrong everywhere the rule is "your own record, your own company, this
field only, this state only, at most this often". Each method above exists
because a caller-controlled request could not enforce one of:

- **ownership** — `employee` comes from the session, never from a parameter
  (`apply_for_leave`, `create_my_request`, `save_my_week`);
- **a field allow-list** — `update_my_profile` writes seven fields whatever
  else it is handed; `apply_for_leave` derives `leave_approver` and
  `half_day_date` rather than accepting them;
- **expected-state validation in one transaction** — `submit_my_week` and
  `act_on_approval` lock the row and compare `modified`/workflow state, so a
  stale second tap is refused instead of committing twice;
- **an idempotency key** — `create_my_request` returns the existing request
  for a repeated `operation_key`, and `attach_to_my_request` is idempotent by
  (request, file name, uploader);
- **bounded input** — subject/details lengths, the attendance span, history
  page sizes, upload size and type;
- **a rate bound** — see "Per-user write limits" below.

The corollary is that role Employee deliberately has *no* create or write
DocPerm on HR Request and no delete on Leave Application: the method is the
create rule, and it is stricter than a DocPerm can be.

`get_dashboard` is one round trip that assembles the header, the leave
balances, this month's attendance, the week spine (`_get_week_spine`), the
action queue (`_get_needs_you`) and this month's celebrations
(`_get_celebrations`) -- and nothing else: the counts it used to
carry alongside them (`pending`, `unread_notifications`) cost a query each
and no screen read them, the badge being fed by the bootstrap and the poller.
Each sub-part runs through `_safe`, which turns an exception into `null` for
that block so one broken source never blanks the whole page, and names itself
in `failed_sections`; `_safe`'s `title` is the caller's own, so a bootstrap
failure no longer logs itself as a dashboard failure.
The cost of that choice is that a type error looks like an empty state; the
runbook records the time this hid a real bug, so any new block needs a Python
test that asserts real data comes back, not just that the key exists.

## Timesheet approval workflow

Shipped as a Workflow fixture on Timesheet with states Draft, Pending Approval,
Pending HR, Approved, Sent Back and actions Submit, Approve, Send Back, Send to
HR, Edit. The portal never shows those words; `docs/design-system.md` maps them
("Waiting for Priya", "Sent back", "Waiting for HR").
`events.timesheet_on_update` shares a Pending Approval timesheet with the
approver's User via DocShare (`submit=1` — the Approve transition on this
workflow *is* the submit) so they can read and decide it, and removes the share
when it leaves that state. The manager's reason is written to
`helixhr_decision_reason` on the Timesheet itself and also added as a Comment
for the timeline; `get_my_week` reads the field, because the Employee role
cannot read Comment directly.

**There is no Reject on a timesheet (P4-KTD2).** A week is one Timesheet row
(`api._week_timesheet`), so a terminal state would lock that week while the
hours still have to be recorded. The workflow carries three outcomes —
Approve, Send Back, Send to HR — and no `Reject` transition on either pending
state, which is why `_allowed_actions` never offers the button: the list is
derived from the transitions, not written beside them.

**Sent Back is the state that used to be called Rejected.** P4-KTD1 gave
*Rejected* its literal meaning and moved the recoverable one to a new state,
**Sent Back** (docstatus 0, `Edit` → Draft).
`patches/v1_0/rename_sent_back_state.py` moves every docstatus-0 row on both
workflows across before `sync_fixtures` installs the new transitions, and every
server read of the literal `"Rejected"` on a workflow kind now reads `Sent
Back` — `_assert_week_is_still_sendable`, `_write_my_week`, `get_my_week`,
`get_my_timesheet_history`, `_decided_*` and `_request_projection.can_withdraw`.
Without that an employee could not resend a sent-back week at all.

**Pending HR is where a manager hands a week over, and it has no DocShare.**
`timesheet_on_update` removes approver shares outside Pending Approval, so HR's
Approve there relies on HR Manager's native `submit` on Timesheet — which
`events.timesheet_before_submit` has always assumed, and which now also refuses
an HR Manager submitting their *own* week.

**One week is one Timesheet, and the week is a range.** Every query for "this
employee's week" goes through `api._week_timesheet`, which matches
`start_date` **between** the Monday and the Sunday -- never `== monday`.
ERPNext's `Timesheet.set_dates` rewrites `start_date` to the earliest
`from_time` in the child table, so a week booked Tuesday-Friday (leave, a
holiday, or simply starting mid-week) persists with the Tuesday: matched by
equality, `get_my_week` read it back as an empty week and the next save hit
ERPNext's own `OverlapError` against the row nobody could see. For the same
reason the `weekStart` route parameter is always normalised through
`get_week_bounds` before it leaves the server. Both writers of a week
(`save_my_week`, `submit_my_week`) also take `api._lock_employee` -- a
`SELECT ... FOR UPDATE` only excludes writers that also take it, and the
lock used to sit in `submit_my_week` alone.

## Attendance requests: one step, one share, four guards (P3-U5, P3-U6, P4-U1)

Shipped as the Workflow fixture `Attendance Request Approval` on Attendance
Request, `workflow_state` as its state field, `send_email_alert` off. It was
the portal's first two-stage approval and P4-R6 collapsed it: **the reports-to
manager's Approve is the submit**, the one HRMS turns into Attendance rows.
Work From Home and On Duty need one decision, not two. HR is involved only
where a manager hands a request over, or where HR raised one itself.

| From | Action | To | Who | Notes |
|---|---|---|---|---|
| Draft | Submit | Pending Manager | Employee | self-approval allowed; it is their own request |
| Pending Manager | Approve | Approved | Employee role, conditioned on `reports_to` **and** the manager's Employee being Active | `doc_status` 1 — the submit that writes Attendance |
| Pending Manager | Send Back | Sent Back | same condition | reason on the record |
| Pending Manager | Reject | Rejected | same condition | terminal; reason on the record |
| Pending Manager | Send to HR | Pending HR | same condition | the hand-over; the manager's note is a Comment |
| Pending Manager / Pending HR | Approve / Send Back / Reject | Approved / Sent Back / Rejected | HR Manager, conditioned on the request not being the acting user's own | HR decides a hand-over, and anything still with a manager |
| Draft | Approve | Approved | HR Manager | so drafts that predate the fixture are not dead ends, and HR can raise-and-approve for somebody (P3-AE14) |
| Sent Back | Edit | Draft | Employee | the employee fixes and resends |

**Rejected is terminal, and the row is removable by its employee (P4-KTD3).**
There is deliberately **no** `Edit` transition off Rejected. But a Workflow
cannot move a document from docstatus 0 to 2, and HRMS's
`validate_request_overlap` refuses a new request over any existing one below
docstatus 2, so a terminal row left in place would block those dates for ever.
Rejected therefore joins `events.REQUEST_WITHDRAWABLE` and the portal words the
action **Remove**, not "Withdraw" — there is nothing left to withdraw from.
*Terminal* means terminal for the row, not for the dates.

**The approver's reason lives on the record, not in a Comment (P4-KTD7a).**
`helixhr_decision_reason` is a Custom Field at permlevel 1 on both Attendance
Request and Timesheet, written by `act_on_approval` at Send Back and Reject.
Deleting a document deletes its Comments, so a Comment could not survive the
removal above; a field travels into the Deleted Document snapshot Frappe keeps.
It also replaced the author-scoped comment scrape `_last_request_comment` as the
source of every employee-facing reason. A decision taken in Desk carries no
reason at all — only `act_on_approval` requires one — so
`_notify_attendance_request` falls back to "No reason was given, ask your
manager or HR for details", which is state-neutral because either outcome can
arrive from either decider.

**The state order in the fixture file is load-bearing.** Frappe backfills rows
that already exist by *state order*, not by name: docstatus 0 rows take the
first state, docstatus 1 rows the first state with `doc_status` 1, and
cancelled rows keep a null state. Draft, Pending Manager, Pending HR, Approved,
Sent Back, Rejected is the order that makes that backfill correct, so it must
never be reordered in a later edit. Every queue filter tolerates a null state
for the cancelled rows.

**The DocShare is load-bearing too, it exists in exactly one state, and it now
carries `submit` (P4-KTD5).** A manager's own User Permission is scoped to
their own Employee record and does not reach a report's Attendance Request at
all — without a share, Frappe's `get_transitions` fails on *read* before the
transition is even considered. Role Employee has no `submit` on Attendance
Request either, so once the manager's Approve became the submit the share is
the whole grant. `events.attendance_request_on_update` keeps one share,
`write=1` **and** `submit=1`, while the state is Pending Manager and `docstatus`
0, and removes it in every other state — including Pending HR, so a manager who
hands a request over cannot act on it afterwards.
`events._reconcile_share(doctype, name, employee, keep_user, submit=0)` is the
Timesheet reconcile generalised by doctype (both callers now pass `submit=1`),
and `employee_on_update` runs both doctypes through it, so a `reports_to` change
or a manager's Employee going inactive moves the share and the action rights
together (P3-R18).

**The overwrite gate moved to the moment of the submit.** HRMS's `on_submit`
rewrites existing Attendance in place, and the P3-U6 preview refuses an
overwrite at *send* time — but auto-attendance can mark a day Present between
the send and the decision, and the person now pressing submit is a line manager
who cannot read Attendance at all. So `_act_on_attendance_request` re-runs
`_attendance_request_preview` against the stored range for a non-HR Approve and
refuses when any day would be overwritten, pointing the manager at Send to HR.
HR is not gated: HR can read Attendance, and leaving the overwrite decision
with them is exactly what that sentence asks for.

**Frappe does not enforce a state's `allow_edit` on the server.** It is a Desk
form hint, and `/api/resource` PUT, `frappe.client.set_value` and
`apply_workflow` all bypass it. Four doc events carry the rules instead
(P3-KTD8), and each one is the same rule the portal enforces, restated where
every route has to pass:

- **`validate`** diffs the doctype's own fields against `get_doc_before_save()`
  and refuses any change other than `workflow_state`, `helixhr_decision_reason`
  and `shift` (HRMS fills `shift` in its own validate) once the request has left
  Draft, unless the caller is HR. The same hook refuses Submit when the employee
  has no active manager, so a raw `apply_workflow` refuses exactly where the
  portal does.
- **`on_update`** reconciles the share, then writes one Notification Log row
  per real state change.
- **`before_submit`** is a two-row table now that the manager's Approve is the
  submit: the reports-to approver from a **stored** Pending Manager, and
  `_is_hr` from Pending Manager, Pending HR or Draft. Anyone else is refused.
  Frappe has already flipped the in-memory field to Approved by then, so the
  stored state is the only evidence of where the request came from — which is
  what stops a raw `frappe.client.submit` jumping Sent Back, Rejected or Draft
  straight to Approved. The "nobody decides their own request" throw runs
  **before** the table, on both branches: it used to sit inside the HR branch
  alone, and a `reports_to` pointing at oneself now reaches the manager branch
  with only the `_approver_user` equality in the way. Administrator is exempt
  here and in the other two submit hooks — it is the migration and backfill
  account, not a person with requests of their own.
- **`on_trash`** allows a delete only for the request's own employee, matched
  by `Employee.user_id` and never by `owner` (the owner is the HR user when HR
  raised it), and only in Draft, Pending Manager, Sent Back or Rejected — the
  same states the portal's withdraw allows. Approved and Pending HR still
  refuse.

None of the four commits, so a throw in any of them rolls the transition and
the share back together.

"HR" here is `events._is_hr`: `Administrator`, or a holder of **HR Manager** or
**System Manager**. HR User is deliberately outside it.

**The portal acts on both steps now, and the queue tells them apart.** A
Pending HR request is in the portal — in the HR queue, tagged, with the sender
and their note (see *Who may act*, below) — rather than being a Desk-only
action as it was in P3. `events._approver_user` is still the single source for
who the manager is; it requires the manager's Employee to be Active, which is
the same assumption the DocShare makes.

**Notifications to the employee are code, not a fixture.**
`_notify_attendance_request` writes one Notification Log row per state change
addressed to the *employee's* `user_id`, because `owner` is the HR login
whenever HR raised the request and Attendance Request carries no user field.
`attendance_request_subject` has one plain sentence per state — "…is with
Priya", "…is with HR", "…counts", "…was sent back", "…was rejected" — and the
two negative ones carry `helixhr_decision_reason` as the body. Nobody is
notified about their own action. The *HR-facing* email is the opposite choice:
it is a fixture Notification on channel Email, recipients by role (P4-KTD9),
because Frappe's Notification DocType already sends to a role and there was
nothing to write.


## Who may act: one table

Four outcomes, three kinds, two roles, and one place that decides which of them
are legal right now. `api._allowed_actions(doc, user)` is that place:
`get_approval_detail` returns its answer as `actions` and `act_on_approval`
refuses anything absent from it, so there is no second copy of the rules on the
screen or in the act. `Approvals.vue` renders exactly `detail.actions` — a
button the server would refuse is not drawn, rather than drawn and disabled.

| Kind | State | Line manager | HR Manager |
|---|---|---|---|
| Leave | Open, stage Manager | Approve · Send back · Reject · Send to HR | Approve · Send back · Reject |
| Leave | Open, stage HR | — | Approve · Send back · Reject |
| Timesheet | Pending Approval | Approve · Send back · Send to HR | Approve · Send back |
| Timesheet | Pending HR | — | Approve · Send back |
| Attendance | Pending Manager | Approve · Send back · Reject · Send to HR | Approve · Send back · Reject |
| Attendance | Pending HR | — | Approve · Send back · Reject |

"Line manager" is the reports-to user with an **Active** Employee record
(`events._approver_user`). A manager who is also HR gets the union, minus Send
to HR once a request is already with HR — there is nowhere left to send it.
Own requests: nothing, at any step, on any route (P4-R8); the list is empty for
the requester and the three `before_submit` hooks refuse the raw routes.
Administrator is exempt everywhere, being the migration account.

**For the two workflow kinds this table is a description, not the
implementation (P4-KTD6).** `_workflow_allowed_actions` calls
`frappe.model.workflow.get_transitions(doc)` and filters it to the four action
names, so the roles and conditions `apply_workflow` will enforce are the only
rule there is: editing a transition in Desk moves the button row and the
server's answer together. Timesheets reach three outcomes and never Reject
because that workflow has no Reject transition (P4-KTD2), not because a rule
here says so. Only Leave, which has no Workflow, uses the explicit table —
`_leave_allowed_actions` is the table above, as code.

Reason and note follow the outcome: Send back and Reject require a reason
(`_REASON_REQUIRED`), written to `helixhr_decision_reason` on the two workflow
kinds and to a Comment on leave, *before* the transition so the employee's
notification carries it. Send to HR takes an optional note. Every outcome is
still refused if the record moved since the approver read it (the
`expected_modified` / `expected_state` contract).

### The fourth kind: routed requests

`HR Request` joined the queue as a fourth kind, and it does not fit the
manager/HR shape above at all -- it has no line manager and no HR-stage
escalation. Instead it has a **routed role**, stamped onto the record at
insert from `HelixHR Request Category.route_to_role` and never re-resolved
(re-pointing a category changes where the *next* request goes, never a
request already filed). A worker holding that role picks the request up
(`picked_up_by`, recorded, so a second worker is told it is already taken),
then moves it through `Need info` / `Done` / `Reject` like any workflow kind
-- `HR Request Handling`'s own transitions are the rule, exactly the way
`get_transitions` already drives Timesheet and Attendance.

Two things about this kind are genuinely different from the other three:

- **The employee's `Reply` is not a workflow transition.** Role `Employee`
  has no `write` on `HR Request` at all, so `apply_workflow`'s `doc.save()`
  is unreachable for the requester. `reply_to_my_request` is a whitelisted
  method that checks ownership and the *stored* status itself, then moves
  the state with `db_set` after authorizing -- a bounded, documented
  exception to "the workflow is the rule table," scoped to the one
  transition the requester owns.
- **A worker can be `IT Team`, not only HR.** `IT Team` is a portal-only role
  (`desk_access: 0`, kept out of `utils.DESK_ROLES`) that works only the
  categories routed to it and reads nothing else -- enforced by a
  `get_permission_query_conditions` / `has_permission` pair on `HR Request`
  scoped to the caller's own company, the stored `routed_to_role`, and (for
  HR Manager / System Manager) the caller's own company rather than every
  company. Because a routed-role holder is not `_is_hr()`, the queue's HR
  gate is "is HR **or** holds a routed role with anything queued" --
  `_holds_routed_role` in `api.py` -- and `get_portal_bootstrap.can_approve`
  follows the same rule so the nav item and the server's own answer can
  never disagree.

### One queue, tagged

An HR Manager gets **one** oldest-first list, not a second backlog:
everything in the HR stage across the three kinds, plus anything waiting for
them as a line manager of their own reports. HR rows carry `for_hr`, the sender
and their note; the screen draws an "HR" chip and "Sent by … · '…'".

That scoping is deliberate and it is the non-obvious half. HR Manager has
native read on every Timesheet and Attendance Request in the company, so the
permission-scoped collectors would answer with the whole company's backlog;
`_line_manager_filter` narrows an HR caller's line-manager half by `reports_to`
explicitly. Leave needs no equivalent — HRMS already scopes it by
`leave_approver` — and `_leave_names_in_hr_stage` is what drops stage-HR rows
out of the manager's half.

The mirror image of that is a trap worth knowing: **an HR Manager must not have
a User Permission on their own Employee record.** It beats HR Manager's own read
inside `get_transitions`, which throws per row, and the Approvals page then
shows HR nothing but their own records — an empty queue rather than a permission
error. `preflight.check_hr_manager_self_scope` WARNs about exactly that, and
`check_employee_user_permissions` exempts HR Manager logins through the shared
`_hr_manager_users()` helper so the two checks can never disagree.

HR Managers with an Active Employee record also **land in the portal** now:
`utils.DESK_ROLES` is `HR User`, `System Manager`, `Administrator` and no longer
HR Manager (P4-KTD8). A `default_workspace` pinned on the User still wins over
the hook, by Frappe's own precedence — `preflight.check_portal_landing` FAILs
and names those users. `IT Team` never reaches Desk at all: `desk_access: 0`
is stated verbatim on the fixture rather than defaulted, because `Role.on_update`
promotes any `desk_access: 1` holder to System User the moment the role is
saved, which is both a Desk door and a billable seat this role must not open.


## Punch derivation, and why the portal method is the only create route

`punch_my_checkin(latitude, longitude, expected_log_type)` takes **no
timestamp and no employee**, which is the entire reason it exists rather than
HRMS's `add_log_based_on_employee_field` (that one trusts a caller-supplied
employee *and* a caller-supplied time). In order:

1. rate limit, then the employee from the session;
2. `expected_log_type` must be `IN` or `OUT`;
3. coordinates cast to float, both finite, latitude within 90, longitude within
   180, and not both zero;
4. HR Settings' mobile check-in flag, or the punch is refused as not set up;
5. `_lock_employee` — the same `SELECT … FOR UPDATE` on the Employee row that
   `submit_my_week` takes, so two taps that arrive together cannot both read
   "no punch yet";
6. server time, and the shift window HRMS resolves for that instant
   (`get_actual_start_end_datetime_of_shift`, grace periods included, default
   shift considered). No window, no punch;
7. the last punch **inside that window** — not inside a calendar day. A night
   shift spans two dates and a traveller's local day is a third answer again,
   and whatever HRMS would attach this punch to is what decides which punches
   are "the shift so far";
8. under 60 seconds since that punch, the existing row is **returned** rather
   than refused, which is what makes a retry after a timeout safe;
9. the type is derived (`IN` after nothing or after an `OUT`, otherwise `OUT`)
   and compared with `expected_log_type`, which is a staleness token and never
   an instruction: a strip left open on a phone since this morning is refused
   with "reload", not obeyed;
10. insert with server time and `device_id = "HelixHR Portal"`.

That insert passes `ignore_permissions=True`, and it has to: role Employee lost
`create`, `write` and `delete` on Employee Checkin in
`patches/v1_0/apply_permission_deltas.py` (P3-KTD13). With its shipped rights
an employee could insert a backdated punch with any coordinates, and edit or
delete punches until the nightly job linked them. `read` stays, so the day
sheet still lists them. **The method is the create rule**, exactly as
`create_my_request` is for HR Request — and the rule is stricter than a DocPerm
can express, which is the argument in "Why so many thin methods" applied to a
punch. HR keeps every Desk and device path.

Coordinates never travel back to a browser: `get_my_checkins` answers with
`has_location` as a boolean. Erasure is `helixhr/tasks.py` on a daily schedule
plus an immediate scrub when an Employee's status becomes Left, both including
the `tabVersion` rows (P3-R28); the period is a site config key, and
`docs/deployment.md` owns the operator half.

## Payslips answer one uniform not-found

`get_my_payslip` and `download_my_payslip` do **not** follow the leave
pattern of "not found" for a missing name and "permission denied" for somebody
else's. A Salary Slip's name embeds the employee id, so the pair of answers
would be an existence oracle: ask for `Sal Slip/HR-EMP-00042/00003` and the
difference between the two errors tells you whether employee 42 was paid that
month. Missing, foreign, draft and cancelled all answer `"That payslip isn't
here."` as a `DoesNotExistError`. Only after ownership is established does a
Withheld slip get its own sentence, because by then the caller is provably the
owner.

`download_my_payslip` is a **GET** so a phone browser saves the file itself
rather than a fetch buffering it, renders the doctype's own default print
format, and corrects Frappe's PDF response afterwards through
`frappe.local.response_headers` (`Content-Disposition: attachment` and
`Cache-Control: no-store`; Frappe writes `inline` and no cache directive). It
also sits behind `frappe.concurrent_limit()` on top of the per-user rate
bound, because PDF rendering spawns wkhtmltopdf and is CPU-bound.

## Projections, not permissions: Team and Directory

Both read Employee-linked data the caller has no Frappe-level right to list,
and both answer with a **server projection over an explicit field allow-list**
rather than by loosening a permission:

- `get_directory` reads active Employees in the caller's *own* company with
  `name, employee_name, designation, department, reports_to, company_email`,
  resolves manager names in one extra query, and emits `email` only when
  `company_email` is non-empty. `user_id` is never selected, so a login
  identifier cannot leave the server through the directory. Search is floored
  at two characters and capped at 60, and the page is 50.
- `get_my_team_week` derives the manager's active direct reports server-side
  and reads their overlapping Leave Applications with `employee, leave_type,
  from_date, to_date, half_day, half_day_date, status, docstatus`.
  `description` is never selected — a leave reason is not a manager's to read
  (P3-R21) — and `status`/`docstatus` are collapsed into one `waiting` flag
  before they leave the server. A caller with no active reports gets a
  `PermissionError`, so the page's `has_reports` gate is not the boundary.

Both read with `ignore_permissions=True` and both are safe *because* the scope
is a server-derived filter rather than a caller-supplied one. The reason it is
done this way rather than by granting the Employee role `report` on Employee is
P2-R26: the generic Employee list stays denied to employees (User Permissions
scope it to themselves), and `test_fixtures.py`'s strict-permission matrix
asserts that `frappe.client.get_list("Employee")` still returns only the
caller. Widening the DocPerm would have opened every Employee field to every
employee to serve six of them.

## Reminders: beside HRMS, not instead of it

HRMS already emails birthdays and work anniversaries, from a subject, header and
Jinja file hardcoded in `hrms/controllers/employee_reminders.py`, gated only by
two HR Settings checkboxes. There is no Email Template record behind it, so the
copy cannot be changed without editing HRMS — which the next `bench update`
overwrites, and which P4-R20 forbids outright.

So `helixhr/reminders.py` registers its **own** daily job in
`scheduler_events.daily` and runs beside HRMS's. Frappe merges scheduler hooks
across installed apps and offers no way to remove another app's job, which is
the whole shape of this design: HelixHR cannot switch HRMS off, so it has to be
switchable itself and the contradiction has to be refused somewhere.

- **The switch is two Custom Fields on HR Settings**,
  `helixhr_birthday_template` and `helixhr_anniversary_template`, each a Link to
  Email Template, sitting in HRMS's own *Reminders* section. Empty means HelixHR
  sends nothing for that event. `reminders.EVENTS` is the one table pairing each
  picker with the HRMS checkbox that would send the stock email for the same
  event, and both the save-time refusal and the preflight line quote from it, so
  they name the same two fields the form does.
- **Two senders for one event is refused where HR creates it.**
  `events.hr_settings_validate` throws on a save that picks a template while the
  matching HRMS checkbox is still ticked. `preflight.check_celebration_reminders`
  FAILs on the same contradiction as the backstop for routes that never reach
  `validate` — a fixture import, a raw `db_set`, a restored site — because
  preflight alone would leave a morning of duplicate mail between HR's save and
  the next operator run.
- **Who is celebrating, and who hears about it, is HRMS's answer** (P4-KTD12).
  `get_employees_having_an_event_today`, `get_all_employee_emails`,
  `get_employee_email` and `get_sender_email` are imported, never
  re-implemented, so eligibility cannot drift from HRMS's. If HRMS renames one
  the module fails to import — loudly, in the scheduler log and in
  `tests/test_reminders.py` — which is the trade that was made on purpose
  against a quiet second implementation.
- **The Jinja context is a small documented contract** (P4-KTD13): `persons`,
  `names`, `count`, `company`, `logo_url`, `date`, `portal_url`, and nothing
  else. It is what HR writes templates against, so it lives in
  `docs/deployment.md` rather than only in the code.
- **The two default templates are seeded by a patch, not shipped as fixtures**
  (P4-KTD11). Fixtures re-import on every migrate and would overwrite HR's
  edits; `patches/v1_0/seed_celebration_templates.py` inserts them only if
  absent and never sets them on HR Settings — switching on is HR's act. It is
  also called from `after_install`, because `bench new-site --install-app` marks
  every patch complete without running it, exactly as `apply_permission_deltas`
  is.

Home's "Celebrating this month" card is the cheap half of the same feature and
shares none of the machinery: `api._get_celebrations` is a projection in the
same posture as `get_directory` (P4-KTD14). `Employee.date_of_birth` and
`date_of_joining` are at permlevel 1 by property setter and unreadable to the
Employee role by design, so the read runs server-side, company-scoped, and
projects only `day`, `month`, `is_today` and — for an anniversary — `years`.
No year of birth and no age is ever on the wire, which is why `lib/dates.js`
grew `formatDayMonth`: there is no date string to format. It is a section of
`get_dashboard`, so it rides Home's one request and its per-section failure
isolation.


## Attendance and the dormant device

No check-in device exists yet. `get_my_attendance` only flags a day as missing
when it falls on or after the employee's first-ever submitted Attendance record,
is before today, is a working day on their holiday list, and is not on leave.
With no records the strip shows a single placeholder line. Nothing changes when
a device arrives; the first record starts the clock.

Phase 3 gave the portal a punch source of its own, so the strip is no longer
dormant on a site that configures a shift — but the rule above is unchanged,
and one exemption was added to it. `get_my_attendance` selects
`Attendance.attendance_request` and carries it as `by_request` per day, and the
`absent`, `half_day` and `late` exception counts skip any day that flag is set
on (P3-R19). A day HR marked by confirming an attendance request has already
been fixed; counting it as an exception would send the employee back to HR
about the day they just had corrected. A half-day Work From Home request is the
case that makes this concrete — HRMS writes a `Half Day` row for it — and a
`missing` day cannot be affected either way, because a request-marked day has
an Attendance row by definition.

## Response headers, uploads and write limits (P2-U9)

**Security headers.** Frappe version-16 sets none of its own, so
`helixhr.utils.set_security_headers` is registered as an `after_request` hook
and adds `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
`Permissions-Policy` and `Content-Security-Policy: frame-ancestors 'none'` to
every response the site serves — plus `Strict-Transport-Security`, but only
when the request arrived over HTTPS, so a plain-HTTP dev bench cannot pin
`localhost` in a developer's browser. Every header is set with `setdefault`:
a reverse proxy that already sets a stricter value keeps it.

**Upload policy.** `helixhr.utils.validate_portal_upload` is the single rule:
private, at most 10MB, and one of PDF, PNG, JPEG, DOCX or XLSX — checked by
extension *and* by leading signature, with the two OOXML types opened as zip
containers so a `.docm` renamed to `.docx` (it carries `vbaProject.bin`) and a
truncated archive are both refused. It is called from
`api.attach_to_my_request`, which is the portal's only upload path, and again
from `events.file_before_insert`, which is the chokepoint every other path
goes through. The same hook function forces `Content-Disposition: attachment`
on `/private/files/...` responses for files attached to an HR Request, so an
uploaded document can never render in the site's own origin.

**Per-user write limits.** `helixhr.utils.RATE_LIMIT_POLICY` is one table read
by three places: `rate_limit_per_user` enforces it, `preflight.check_rate_limits`
refuses a site that has loosened it through the optional `helixhr_rate_limits`
site config, and the runbook quotes it. The buckets are keyed by session user
and site, not by IP — one office behind one address would otherwise share a
bucket.

The limiter is **off on a site with `allow_tests`**, because the limits and
the test suites are otherwise mutually exclusive: the Python suite creates far
more than ten HR Requests as one user in one run, and a second Playwright pass
inside the same minute re-trips the timesheet bound. That is safe only because
`preflight.check_test_mode` FAILs a site with `allow_tests` on and the deploy
gate exits non-zero. `TestPerUserRateLimits` forces the limiter back on with
`frappe.flags.helixhr_enforce_rate_limits` and proves the eleventh request in
an hour is refused, so the bypass is never the thing under test.

## `helixhr/api.py` is one module on purpose

It is about 4,600 lines after phase 3 (it was about 2,600 when this section was
written). Phase 2's plan carried a 1,500-line review threshold
(KTD4) that would have triggered a split into per-domain modules; that
threshold was reviewed once the file had actually grown and **dropped**
(decision taken 2026-09-05, after P2-U9). This is the recorded outcome, not
an outstanding to-do — nothing here is pending.

The reasoning, so it does not get re-litigated every time the file grows:

- Every whitelisted method's **dotted path is public API**. `helixhr.api.get_my_leave`
  is a string in `frontend/src/`, in `helixhr/tests/`, and potentially in any
  external caller. Moving the function renames the endpoint, so a split is not
  an internal refactor — it is an API migration that needs `helixhr/api.py` kept
  as a re-export shim (or `override_whitelisted_methods`) plus a pass over every
  `helixhr.api.<name>` string in the repo. The shim then reintroduces the single
  file the split was meant to remove.
- **Line count is a poor proxy for the risk here.** This is the app's one
  authorization boundary, and its value is that a reviewer can read every
  whitelisted entry point in one place and check that each resolves its
  employee from the session. Splitting it across eight files makes that audit
  harder, not easier.
- The file is already sectioned by domain and the sections share almost
  nothing but `get_current_employee`, `_as_date` and the rate limiter, so the
  navigability a split would buy is largely there already.

If it is ever split, the seams are the existing domain sections: dashboard,
leave, attendance (check-in and attendance requests included), payslips,
holidays, timesheet, approvals, requests, documents, directory, team, session.
Split
it because a concrete test seam cannot stay isolated — the other half of
KTD4 — not because of a line count.

## Frontend structure

- `App.vue` mounts `AppShell` for every route except the three state routes
  (`meta.shell: false`). The shell is a desktop sidebar at 1024px and up, and
  an app bar plus a five-item bottom tab bar below that, with a "More" dialog
  for the rest. Approvals appears only when the bootstrap says `can_approve`
  (now true for a routed-role holder with anything queued, not only HR);
  Settings appears only on `can_configure` (HR); Organisation only on
  `can_see_organisation` (HR Manager / System Manager, company-scoped,
  read-only). Every nav gate is a bootstrap boolean, never a role list --
  the frontend carries no role names of its own, and the server enforces the
  read or write independently of what the nav happens to show.
- `lib/session.js` owns the portal bootstrap (`ensureBootstrap`, at most once
  per hard load; `retryBootstrap` only on an explicit user retry), the
  `idle`/`loading`/`ready`/`not-linked`/`unavailable` status the router and
  `NotLinked.vue` branch on, and `signOut` (an explicit POST to `logout`, then
  a hard redirect to `/login`).
- `lib/dates.js` is the local-calendar module — see "Portal bootstrap, and
  whose calendar it is" above. Nothing else in the frontend may parse a Frappe
  date or compute a week boundary.
- `lib/unread.js` is a module-scope resource for the notification badge. It
  makes no first fetch at all -- the bootstrap already carries the count -- and
  the 60s poll it does own exists only while the document is visible: hiding
  the tab clears the interval, and the hidden -> visible transition costs
  exactly one catch-up read however many of `visibilitychange` and `focus`
  the platform delivers.
- Three small pure modules joined the `lib/` layer in phase 3, each with its
  own vitest file: `lib/statusBadge.js` holds the status word table
  `StatusBadge.vue` renders (a `<script setup>` component cannot export, so the
  mapping had to move out to be testable at all); `lib/money.js` formats one
  amount in one currency and returns an empty string rather than an unlabelled
  number, because nothing in the portal may sum money across currencies; and
  `lib/geolocation.js` is the only caller of the Geolocation API — one
  high-accuracy 10s attempt, one low-accuracy 5s retry, never on page load,
  with `insecure` and `unsupported` short-circuited before the API is touched
  so the sheet never gives browser-setting advice for a problem no setting
  fixes.
- `lib/dialogA11y.js` names frappe-ui's unlabelled dialog close button, once
  for the whole app, and `lib/featherIcons.js` is the stub the Feather icon
  set is aliased to. Both are there because the alternative was editing
  `node_modules`; both have a guard next to them that fails if the assumption
  they rest on stops being true.
- `index.css` is the design token layer. frappe-ui hard-codes `blue` as its
  primary palette, so `tailwind.config.cjs` retunes the `blue` scale to the
  brand green; read `blue` as "brand" throughout. The accent yellow may only
  appear on the deep green field (it is 1.2:1 on paper).
- Every page is one file in `src/pages/`; shared pieces live in
  `src/components/`. Copy is written in the file it appears in, not in a
  translation layer, because the vocabulary rule is a product decision, not a
  locale.

## Fixtures

`hooks.py` lists them with filters scoped to this app's own records so
`bench export-fixtures` never captures another app's rows. Permission rows are
deliberately **not** among them — see "Permission deltas are a patch, not a
fixture" above. Fixtures are installed by `bench migrate`;
`preflight.check_fixtures` confirms every one the app cannot work without --
three Workflows (`HR Request Handling` since phase 5), their states and
action masters, and the request/leave/timesheet/attendance HR-facing
Notifications. `preflight.check_retired_request_notifications` is the
mirror image: the two request Notifications phase 5 retired
(`HelixHR New Request For HR`, `HelixHR Request Status Changed`) must never
come back enabled, on a site that had already installed them before the
routing release.

## Tests

- **Python** (`helixhr/tests/`, `bench run-tests --app helixhr`): one file per
  API area, all `IntegrationTestCase`. Fixture users come from `tests/utils.py`.
  Cleanup must be scoped to the fixture employees; a site-wide delete once
  emptied a dev database. Method rollback between tests is not reliable here;
  never assert against an assumed-empty baseline.
- **Vitest** (`frontend/src/**/*.test.js`): pure functions only, currently the
  error-message mapping.
- **Playwright** (`frontend/tests/e2e/`): `setup` logs the four fixture users
  (`employee`, `manager`, `hr`, and `it` since phase 5's routed-role queue)
  in once and stores state; `employee` and `manager` reuse it on desktop
  Chromium; `employee-mobile-webkit` re-runs the critical flows on iOS's only engine
  under a coarse pointer; `baseline` exists only when `BASELINE_MODE` is set
  and runs the pinned performance protocol. `navigation.spec.ts` travels by
  clicking, not by URL, because a portal with no navigation once passed every
  URL-driven spec. `hardening.spec.ts` owns the P2-U9 gates that are cheap
  enough to run every pass: lazy route chunks, the visibility-aware poll,
  built-asset policy, no service worker, the security headers, and the two
  accessibility items.

## Extending

Adding a screen: a page in `src/pages/`, a route in `router.js`, a `NAV`
entry in `AppShell.vue`, a whitelisted method in `api.py` if `frappe.client`
cannot express the read within the user's permissions, a Python test for that
method that asserts real data, and one clicked navigation in the e2e suite.

Adding a setting: prefer site config read in `www/helixhr.py`'s `boot` (as
`helixhr_hr_contact` is) over a new Single doctype, and add a line to
`preflight.py` so the value is checked on every deploy. This is the rule for
a **scalar deploy-time flag** -- a threshold, a toggle, an address.

It is not the rule for something HR edits at runtime. This app's standing
position is that Desk owns HR administration and the portal does not
re-implement HRMS rules -- the routed-requests plan (phase 5) partly reverses
that, deliberately and within bounds: HR now configures request categories
and routing (`HelixHR Request Category`), the wording of the portal's own
notifications (`HelixHR Message Template`, plain `{token}` substitution --
**never** `frappe.render_template`, since a template evaluated as code would
let HR's own text become remote code execution the moment it renders), and a
**named, deliberately short** field set on three HRMS masters (Leave Type,
Holiday List, Shift Type -- `helixhr/utils.py`'s `*_EDITABLE_FIELDS`
constants, not every field the doctype has). Everything else that changes
rarely -- payroll runs, salary structures, onboarding, recruitment, company
and department masters, role assignment -- stays in Desk; this plan does not
touch it. A new HR-editable surface of this shape gets its own `save_*`
method in `api.py` (explicit permission check, an allow-listed field set,
`doc.save()` so the underlying doctype's own `validate` still runs -- never
`db_set`), not a site config value and not a new Single.
