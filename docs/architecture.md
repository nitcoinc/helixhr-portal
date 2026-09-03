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

every navigation
   -> router.beforeEach calls hrms.api.get_current_employee_info
      no active Employee  -> /not-linked, no shell
      Employee            -> lib/session.setEmployee(), shell renders

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
   as bank details to permlevel 2), and `fixtures/custom_docperm.json` gives the
   Employee role read-only at level 1 and nothing at level 2. The seven editable
   fields (mobile, personal email, addresses, emergency contact) stay at level 0.
   `api.update_my_profile` also drops any field outside those seven before it
   touches the document, so the UI is never the only guard.

Writes the employee should not be able to make are refused server-side:

- `save_my_week` refuses a week that is not Draft or Rejected, refuses projects
  the user is not assigned to, and is rate-limited per user.
- `act_on_approval` re-checks that the caller is the approver (`reports_to`
  for timesheets, `leave_approver` for leave) before applying a workflow action.
- `events.timesheet_before_submit` refuses a submit by anyone but the approver
  even if a Desk user finds another route to it.
- `events.file_before_insert` refuses a public file attached to an HR Request.

## Data flow per screen

| Screen | Reads | Writes | Backing records |
|---|---|---|---|
| Dashboard | `api.get_dashboard` | none | Timesheet, Leave Application, HR Request, Attendance, Notification Log |
| Leave | `hrms.api.get_leave_balance_map`, `get_leave_applications`, `get_leave_types`, `get_leave_approval_details` | `frappe.client.insert`, `frappe.client.delete` (withdraw, own docs only) | Leave Application, Leave Allocation |
| Attendance | `api.get_my_attendance`, `frappe.client.get_list` on Employee Checkin | none | Attendance, Employee Checkin, Holiday List, Leave Application |
| Timesheet | `api.get_my_week`, `api.get_my_projects` | `api.save_my_week`, `frappe.model.workflow.apply_workflow` (send for approval) | Timesheet + Timesheet Detail, Workflow "Timesheet Approval" |
| Requests | `frappe.client.get_list` | `frappe.client.insert`, file upload | HR Request (this app's doctype), File |
| Documents | `frappe.client.get_list` | none | HelixHR Document Link |
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

- `App.vue` mounts `AppShell` for every route except `/not-linked`
  (`meta.shell: false`). The shell is a desktop sidebar at 1024px and up, and
  an app bar plus a five-item bottom tab bar below that, with a "More" dialog
  for the rest. Approvals appears only when `session.reportCount > 0`.
- `lib/session.js` holds the Employee the router guard fetched, so the shell
  does not repeat the request, and owns `signOut` (an explicit POST to
  `logout`, then a hard redirect to `/login`).
- `lib/unread.js` is a module-scope resource for the notification badge; it
  must be fetched by hand because frappe-ui only auto-fetches inside a
  component's `onMounted`.
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
`bench export-fixtures` never captures another app's rows. Two Custom DocPerm
entries need distinct `prefix` values or the second export overwrites the first.
Fixtures are installed by `bench migrate`; `preflight.check_fixtures` confirms
the four that the app cannot work without.

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
