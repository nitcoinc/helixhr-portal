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

- `save_my_week` refuses a week that is not Draft or Rejected, refuses projects
  the user is not assigned to, and is rate-limited per user.
- `act_on_approval` locks the native row (`SELECT ... FOR UPDATE` on
  `modified`), then re-checks that the caller is the approver (`reports_to`
  for timesheets, `leave_approver` for leave), then checks the record is still
  undecided and matches the caller's optional `expected_modified` token, and
  only then adds a comment or applies the transition. That order is the point:
  it used to comment first, so an unauthorized caller left a comment on
  somebody else's record.
- `events.timesheet_before_submit` refuses a submit by anyone but the approver
  even if a Desk user finds another route to it.
- `events.file_before_insert` refuses a public file attached to an HR Request.

## Leave approval runs the native lifecycle

`act_on_approval` on a Leave Application **submits** it (`docstatus` 1). That
is what makes HRMS write the Leave Ledger Entry, consume balance and update
attendance; setting `status = "Approved"` alone does none of it, and the row it
leaves behind (`docstatus` 0, status Approved) is a defect state that
`preflight.check_unsubmitted_approved_leave` counts and
`patches/v1_0/report_unsubmitted_approved_leave` lists for HR to resolve in
Desk. A rejection stays unsubmitted, so it consumes nothing.

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
Application. `preflight.check_custom_docperm_coverage` is the standing guard —
the patch runs once, so anything that trims these rules later shows up there.
HRMS's own Employee Self Service rules are never touched; removing document
sharing site-wide is System Settings' "Disable Document Sharing", not a
permission rule.


## Portal bootstrap, and whose calendar it is (P2-U2)

`helixhr.api.get_portal_bootstrap` is the one session-scoped read the shell
makes per hard load: the active Employee, `report_count`/`can_approve`, the
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
| `/approvals` | `/approvals/:kind/:name` | `kind` is `leave` or `timesheet` |
| `/timesheet` | `/timesheet/:weekStart` | the week's Monday, `YYYY-MM-DD` |
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
- The detail routes currently resolve to the list page they belong to. The
  unit that builds each real detail screen swaps the component in and changes
  nothing else.

Three routes are states rather than pages, all rendered by `NotLinked.vue`
with `meta.shell: false`: `/not-linked` (signed in, no Employee — shows the
site's HR contact), `/unavailable` (the bootstrap failed — Retry, and it
resumes the destination in `?retry-to=`), and the `/:pathMatch(.*)*` catch-all
(unknown URL — Home). A Guest never reaches any of them: `lib/api.js` sends
them to `/login?redirect-to=<the full portal path>`.


## Data flow per screen

| Screen | Reads | Writes | Backing records |
|---|---|---|---|
| Dashboard | `api.get_dashboard` | none | Timesheet, Leave Application, HR Request, Attendance, Notification Log |
| Leave | `hrms.api.get_leave_balance_map`, `get_leave_applications`, `get_leave_types`, `get_leave_approval_details` | `frappe.client.insert`, `frappe.client.delete` (withdraw, own docs only) | Leave Application, Leave Allocation |
| Attendance | `api.get_my_attendance`, `frappe.client.get_list` on Employee Checkin | none | Attendance, Employee Checkin, Holiday List, Leave Application |
| Timesheet | `api.get_my_week`, `api.get_my_projects` | `api.save_my_week`, `frappe.model.workflow.apply_workflow` (send for approval) | Timesheet + Timesheet Detail, Workflow "Timesheet Approval" |
| Requests | `frappe.client.get_list` | `frappe.client.insert`, file upload | HR Request (this app's doctype), File |
| Documents | `api.get_my_documents` (scoped server-side; `frappe.client.get_list` is scoped by the same hooks) | none | HelixHR Document Link |
| Notifications | `notification_log.get_notification_logs` | `notification_log.mark_all_as_read` | Notification Log, fed by the four Notification fixtures |
| Approvals | `frappe.client.get_list` on Timesheet, `hrms.api.get_leave_applications` | `api.act_on_approval` | Workflow actions, DocShare for the approver |
| Profile | `api.get_dashboard` header, `frappe.client.get` on own Employee | `api.update_my_profile` | Employee |

`get_dashboard` is one round trip that assembles the header, the week spine
(`_get_week_spine`), the action queue (`_get_needs_you`), reference counts and
the unread count. Each sub-part runs through `_safe`, which turns an exception
into `null` for that block so one broken source never blanks the whole page.
The cost of that choice is that a type error looks like an empty state; the
runbook records the time this hid a real bug, so any new block needs a Python
test that asserts real data comes back, not just that the key exists.

## Timesheet approval workflow

Shipped as a Workflow fixture on Timesheet with states Draft, Pending Approval,
Approved, Rejected and actions Submit, Approve, Reject, Edit. The portal never
shows those words; `docs/design-system.md` maps them ("Waiting for Priya",
"Sent back"). `events.timesheet_on_update` shares a Pending Approval timesheet
with the approver's User via DocShare so they can read it, and removes the
share when it leaves that state. The manager's rejection reason is a Comment on
the Timesheet, resolved server-side in `get_my_week` because the Employee role
cannot read Comment directly.

## Attendance and the dormant device

No check-in device exists yet. `get_my_attendance` only flags a day as missing
when it falls on or after the employee's first-ever submitted Attendance record,
is before today, is a working day on their holiday list, and is not on leave.
With no records the strip shows a single placeholder line. Nothing changes when
a device arrives; the first record starts the clock.

## Frontend structure

- `App.vue` mounts `AppShell` for every route except the three state routes
  (`meta.shell: false`). The shell is a desktop sidebar at 1024px and up, and
  an app bar plus a five-item bottom tab bar below that, with a "More" dialog
  for the rest. Approvals appears only when `session.reportCount > 0`.
- `lib/session.js` owns the portal bootstrap (`ensureBootstrap`, at most once
  per hard load; `retryBootstrap` only on an explicit user retry), the
  `idle`/`loading`/`ready`/`not-linked`/`unavailable` status the router and
  `NotLinked.vue` branch on, and `signOut` (an explicit POST to `logout`, then
  a hard redirect to `/login`).
- `lib/dates.js` is the local-calendar module — see "Portal bootstrap, and
  whose calendar it is" above. Nothing else in the frontend may parse a Frappe
  date or compute a week boundary.
- `lib/unread.js` is a module-scope resource for the notification badge; it
  must be fetched by hand because frappe-ui only auto-fetches inside a
  component's `onMounted`. The bootstrap already carries the initial count
  (`session.unread`); folding the first poll into it is P2-U4's job.
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
`preflight.check_fixtures` confirms the four that the app cannot work without.

## Tests

- **Python** (`helixhr/tests/`, `bench run-tests --app helixhr`): one file per
  API area, all `IntegrationTestCase`. Fixture users come from `tests/utils.py`.
  Cleanup must be scoped to the fixture employees; a site-wide delete once
  emptied a dev database. Method rollback between tests is not reliable here;
  never assert against an assumed-empty baseline.
- **Vitest** (`frontend/src/**/*.test.js`): pure functions only, currently the
  error-message mapping.
- **Playwright** (`frontend/tests/e2e/`): three projects, `setup` logs the two
  fixture users in once and stores state, `employee` and `manager` reuse it.
  `navigation.spec.ts` travels by clicking, not by URL, because a portal with
  no navigation once passed every URL-driven spec.

## Extending

Adding a screen: a page in `src/pages/`, a route in `router.js`, a `NAV`
entry in `AppShell.vue`, a whitelisted method in `api.py` if `frappe.client`
cannot express the read within the user's permissions, a Python test for that
method that asserts real data, and one clicked navigation in the e2e suite.

Adding a setting: prefer site config read in `www/helixhr.py`'s `boot` (as
`helixhr_hr_contact` is) over a new Single doctype, and add a line to
`preflight.py` so the value is checked on every deploy.
