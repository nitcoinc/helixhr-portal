# HelixHR runbook

Operational steps for a human, and every environment gotcha found so far, in the order they
were found. Start with `README.md` for install, configure, verify and release; come here when
something does not behave. The go-live checklist is at the bottom.

## Install the app on a bench

The app is not published anywhere yet. Until it is, copy the source in and register it:

```bash
# on the bench host, inside the backend container (or a real bench checkout)
cp -r /path/to/HelixHR-Fronend/helixhr apps/helixhr/helixhr
cp /path/to/HelixHR-Fronend/pyproject.toml /path/to/HelixHR-Fronend/README.md /path/to/HelixHR-Fronend/LICENSE apps/helixhr/
uv pip install -e apps/helixhr --python env/bin/python
bench --site <site> install-app helixhr
```

If the site's Frappe process was already running before you added the app, **restart it** —
a long-running gunicorn worker does not pick up a newly added Python package on its own.
Also make sure the `helixhr` app directory exists inside **every** container that serves the
site (backend, and separately the frontend/nginx container if it is a distinct container from
the same image) — each has its own filesystem copy of `apps/`, even when they share the
`sites` data volume. If static assets 404 after install, check for a per-container missing
`assets/helixhr -> apps/helixhr/helixhr/public` symlink (`ls -la <bench>/assets/`) and create
it if absent, matching the symlinks already there for `frappe`/`erpnext`/`hrms`.

After changing the frontend, **clear the site's cache** (`bench --site <site> clear-cache`) --
website pages are cached, so a rebuilt `www/helixhr.html` can otherwise keep serving the old
one.

Build the frontend before or after installing the app (order doesn't matter, but the page
404s / 500s until it's built at least once):

```bash
cd frontend
yarn build
```

**Windows note:** building on Windows can hang indefinitely — `frappe-ui`'s Vite plugin walks
up the directory tree looking for a bench (`sites/` + `apps/` siblings) using a Unix-only root
check (`while (currentDir !== '/')`), which never terminates on a Windows drive root (`C:\`).
Build on Linux/macOS, or inside a Linux container, instead.

## Sign-in: local login first, Entra ID later

Phase 1 goes live with Frappe's own username/password login. No Entra ID app registration
exists yet, and the OAuth round trip cannot be verified without a real host, so the portal
does not depend on it. Two consequences for a local-login site:

- **System Settings → Disable Username/Password Login must stay off.** Turning it on with no
  enabled Social Login Key locks every user out, including Administrator over the web.
  `helixhr.preflight.run` fails on exactly that combination.
- **System Settings → Enable Password Policy** is the only strength check, so turn it on.

Portal users are ordinary Frappe Users: create the Employee first, then the User from the
Employee form (or set `user_id`), with the **Employee Self Service** role and "Create User
Permission" checked. HR can reset a password from Desk. When Entra ID is ready, follow the
section below; nothing in the app changes.

## Microsoft Entra ID login (when a real host exists)

1. In the Azure portal, register a new App registration.
   - Platform: **Web**.
   - Redirect URI: `https://<your-site>/api/method/frappe.integrations.oauth2_logins.login_via_office365`
   - Token configuration → add optional claim → ID token → **email**.
   - Certificates & secrets → create a client secret. Copy it now; it is not shown again.
2. In Frappe Desk: **Social Login Key** → new → provider **Office 365**.
   - Client ID = the app registration's Application (client) ID.
   - Client Secret = the secret from step 1.
   - Enable the key.
3. **Website Settings → Disable Signup**: turn **on**. Without this, an unknown Entra email
   gets a new Website User instead of the "contact HR" message the brief requires (D2/D3 in
   the brief; R2 in the plan).
4. **System Settings → Disable Username/Password Login**: turn on for **production only**.
   Leave password login on for dev/test sites so local and automated testing doesn't need
   Entra.
5. Behind a reverse proxy (nginx, the frontend container here), make sure
   `X-Forwarded-Proto` is set correctly so Frappe marks the session cookie `Secure`.

### Known upstream issue: `redirect-to` can be lost on the OAuth round trip

Frappe has an open bug (frappe/frappe#27672) where `redirect-to` sent to `/login` can come
back empty after the Entra round trip. **Verify this on your own site before relying on it**:

```
https://<site>/login?redirect-to=/helixhr
```

Sign in and confirm you land on `/helixhr`, not the bare Desk/home page. If it fails, set
**Website Settings → Home Page** to `/helixhr` as the fallback (KTD4) — every user then lands
on the portal after login by default, `redirect-to` or not.

_Verified so far: password-login `redirect-to` works correctly on this environment's dev site
(confirmed both by hand and by the Playwright suite below). Entra isn't configured on the dev
VM, so the OAuth round trip itself is **not yet verified** -- do this before go-live._

## Test users, and the "how does a Guest get logged out" surprise

`helixhr/tests/utils.py` creates three users, on demand, from Python tests or via a
whitelisted HTTP method for Playwright (see below):

- `employee@helixhr.test` — reports to the manager below.
- `manager@helixhr.test`.
- `no-employee@helixhr.test` — a logged-in user with no Employee record, for the "not linked"
  page (R3).

All three log in with the password in `helixhr/tests/utils.py`'s `TEST_PASSWORD` constant.
**It is deliberately not `"password"`** -- any site with System Settings' password policy on
(the dev VM does; a barebones fresh test site may not) rejects that as a top-10 common
password.

Each employee is created with `create_user_permission=1`, which gives them a **User
Permission** scoping them to their own Employee record — this is the entire authorization
boundary (brief D4, plan KTD5). A user without it can see every employee's data.

**A logged-out visitor ("Guest") calling any of this app's whitelisted methods gets a `403
PermissionError`, not a `401 AuthenticationError`.** Frappe has no separate "not authenticated"
state — Guest is just another session — so `allow_guest=False` (the default, and what every
method here uses) denies Guest with a `PermissionError` whose message contains "Login to
access". `frontend/src/lib/api.js` matches on that phrase (in addition to a real
`AuthenticationError`) to redirect to `/login`, specifically so it does **not** also redirect a
logged-in user who is simply forbidden from a specific action (e.g. approving someone else's
timesheet, R25/R26) — that case must stay an in-app error, not a forced logout.

## Fixtures need a bit of seed data a headless install skips

`bench new-site --install-app erpnext` never runs the interactive setup wizard, so a fresh
site has **no Gender records and no "Transit" Warehouse Type** even though creating an
Employee or a Company needs them. `helixhr/tests/utils.py` creates both on demand
(`ensure_test_gender`, the `Warehouse Type` check in `ensure_test_company`) rather than
assuming they exist. If a future fixture hits a similar `MandatoryError` or
`LinkValidationError` for some other setup-wizard-seeded record, the fix is the same shape:
create it once, idempotently, in this file — don't reach for erpnext's own test-suite helpers
(`erpnext.setup.doctype.employee.test_employee.make_employee`) to sidestep it. That module's
*import* runs `erpnext.tests.utils.ERPNextTestSuite`'s bootstrap as a side effect, which tries
to create fiscal years and other master data unconditionally -- harmless on a pristine site,
but it throws on any site (any dev site that's been used at all) that already has real
Company/Fiscal Year records that overlap with what it tries to create.

## Multi-site gotchas found while wiring up this VM (read before adding another site)

This dev VM's nginx (`helixhr-platform`'s image) is built for **one fixed site**: it hardcodes
`try_files /10.10.16.26/public/$uri ...` and `X-Frappe-Site-Name 10.10.16.26` regardless of
what `Host` header a client sends. A second site (`test.localhost`, used for the Python
integration test suite -- see below) is real, but it is **not reachable over HTTP through this
nginx at all** (see the lock-wait section below for a separate, real problem with running its
tests over SSH on this VM). Two consequences:

- **Python tests** (`bench --site <name> run-tests`) don't need HTTP host resolution at all --
  they select the site directly. In principle a dedicated site (`test.localhost` here) is fine
  for these; in practice, running them over SSH on **this specific VM** hits a separate, real
  problem -- see the next section.
- **Playwright** (real browser, needs real HTTP) runs against the dev site (`10.10.16.26`)
  instead, using the three clearly-named test users above. `bench serve --port <n>` (Frappe's
  own dev server, which *does* do Host-header-based multi-site routing) was tried as a way to
  reach `test.localhost` over HTTP, but never resolved the header correctly in this
  environment for a still-unconfirmed reason -- not worth chasing further given the dev site
  works fine for this purpose. A from-scratch CI environment (one site, created fresh per run,
  matching `frappe/hrms`'s own CI) won't have this problem: with only one site, Frappe's
  `default_site` fallback picks it regardless of the `Host` header.

**Do not set a `Host` header override on a Playwright *browser* context** (`use.extraHTTPHeaders`
in `playwright.config.ts`). It breaks `page.goto()` outright with
`net::ERR_INVALID_ARGUMENT` -- confirmed by testing a bare Playwright script with and without
it, isolating it from every other variable (hostname vs IP, Docker networking, `--ipc=host`).
An **API-only** request context (`request.newContext()`, used by `auth.setup.ts` to log in) is
unaffected and can set `Host` freely.

## A `werkzeug.test.Client(application)` call inside a test can hang every test after it (U4)

**Correction after finding the real cause (see below) -- the first version of this note wrongly
blamed the dev VM.** `bench --site <site> run-tests --app helixhr` intermittently hit
`frappe.exceptions.QueryTimeoutError: (1205, 'Lock wait timeout exceeded')` on the **first**
insert of a brand-new row into some table (seen on Department, Designation, and plain User in
turn, as the fixture code changed), taking ~50s per hang. It reproduced on the dev VM even
after dropping and recreating the test site from scratch, which looked at the time like a
VM/environment problem worth routing around by trusting CI instead.

It also reproduced (same symptom, same ~50s stall, and the same "a second, otherwise-idle DB
connection is holding something the real insert waits on" shape visible in
`SHOW FULL PROCESSLIST`) on a completely fresh, from-scratch local Docker dev bench built for
U5 (`frappe_docker`-style, KTD14 -- `apps.json`-driven `bench get-app` for `erpnext`/`hrms` on
`version-16`, this repo bind-mounted as `apps/helixhr`), independently confirming this was never
about that one VM.

**Then the identical hang happened on a clean GitHub Actions runner**, on the real U4 code, one
test after `test_guest_is_refused` -- which called `werkzeug.test.Client(application).get(...)`
(a real nested WSGI request) from inside an `IntegrationTestCase` test to prove a whitelisted
method rejects Guest. That nested request leaves a database connection open that the test
framework's per-test rollback doesn't clean up, and it then blocks the next test's first insert
into any table. This is a real, general Frappe-testing gotcha, not specific to that VM -- it
would reproduce on any environment, including a fresh local Docker bench.

**The fix: don't make a real HTTP request from inside a Python test to prove a permission
check.** Frappe's own dispatch layer (`frappe.handler.is_whitelisted`) checks Guest access with
a plain set membership test -- a whitelisted method is only Guest-reachable if it's in the
module-level `frappe.guest_methods` set. Assert that directly instead:

```python
self.assertNotIn(get_dashboard, frappe.guest_methods)  # also: assertIn(get_dashboard, frappe.whitelisted)
```

No nested request, no leaked connection, and it tests the exact mechanism that actually runs in
production. Real HTTP-level Guest coverage (an actual 403, an actual redirect) belongs in
Playwright, not a Python unit test -- `login-dashboard.spec.ts`'s `guest` describe already
covers that end to end.

`helixhr/tests/test_install.py` still uses `Client(application)` for two tests (proving the
built page serves real HTML) and has not caused an observed hang -- no evidence it's unsafe by
itself. But **avoid adding a new `Client(application)` call inside any `IntegrationTestCase`
test without a specific reason**; if the built-page check needs extending, prefer checking
`helixhr.www.helixhr` directly where the plan's own notes allow it, and treat any new
mysterious lock-wait timeout as this class of bug first, before assuming it's environmental.

## `IntegrationTestCase` does not roll back between test *methods* here (U6)

Confirmed while writing U6's `test_leave_flow.py` on the from-scratch local Docker dev bench:
writes made by one test method are still visible to the *next* test method in the same
`TestCase`, within one `bench run-tests` invocation -- not just across test files, across
methods in the same class. A test that inserts a document and a later test that expects a
clean slate (or checks a count starting from zero) will see the earlier method's data.

- **Never assert against an assumed-empty baseline** ("this field is None", "this count is
  0"). Read the actual value at the top of the test first and compare against *that*.
- **Give each test method its own data window** where the assertion depends on nothing else
  existing yet -- a distinct Leave Type, a distinct date range, etc. -- rather than relying on
  isolation the framework isn't providing here.
- A stray `frappe.db.commit()` inside a test makes this worse, not better: it doesn't create
  isolation, it just guarantees the write survives into a *different* `bench run-tests`
  invocation too (this actually happened while debugging U6 -- a leftover Leave Application
  and Leave Allocation had to be deleted by hand from `test_site` afterwards). Don't call
  `frappe.db.commit()` inside a test or its `setUp`/`tearDown` unless the test is specifically
  about commit behavior -- same-connection visibility does not need it.
- Calling a `@frappe.whitelist()` test-setup helper (like `setup_playwright_fixtures`) over
  real HTTP against `test_site` has the same effect -- it's a real, separate commit outside
  any test's scope, and will linger and can affect an unrelated Python test's assumptions
  (seen directly: `test_leave_balances_matches_hrms_api_with_no_allocation` started failing
  after an interactive `curl`-driven Playwright-fixture setup left a real Leave Allocation
  behind). If you run that helper by hand while developing, clean up what it created
  afterwards, or expect to on a long-lived local `test_site`.

## Leave Application needs more than an Allocation row to actually work (U6)

A few HRMS behaviors that aren't obvious from the Leave Application doctype alone, found
while writing `test_leave_flow.py` and `LeaveForm.vue`:

- **`leave_approver` is not auto-filled from `Employee.leave_approver` server-side.**
  `validate_leave_approver` checks the field on the Leave Application document itself. The
  portal fetches `hrms.api.get_leave_approval_details(employee)` and sends its
  `leave_approver` explicitly on insert (`LeaveForm.vue`); a Python test inserting a Leave
  Application directly must do the same.
- **A Leave Allocation only counts once it's submitted** (`docstatus = 1`) --
  `get_allocation_based_on_application_dates` filters on `docstatus == 1`, so a freshly
  inserted-but-not-submitted allocation makes every Leave Application look like it's "outside
  leave allocation period" even with a matching date range. `helixhr.tests.utils.
  ensure_leave_allocation` inserts and submits in one step.
- **Leave Application is itself submittable** (`is_submittable=1`), and the balance-consuming
  Leave Ledger Entry is only created `on_submit`, not on insert. An application sitting at
  `status="Open"`, `docstatus=0` (the normal pre-approval state this app's portal creates)
  does not yet consume any balance -- only a submitted (`status="Approved"`, `docstatus=1`)
  application does. A test that wants to prove a *second* application gets refused for
  insufficient balance has to insert, set `status = "Approved"`, then `submit()` the first one
  (standing in for the approver's action) before the balance check will actually see it as
  consumed. `submit()` also then requires a Holiday List reachable for the employee or their
  company on that date (see next point) -- `insert()` alone does not.
- **A headless site has no Holiday List either** (same class of gap as Gender and Warehouse
  Type, already documented above) -- `hrms.utils.holiday_list.get_holiday_list_for_employee`
  throws `No Holiday List was found for Employee ... or their company ...` the first time
  anything needs one (confirmed: triggered by `submit()` above, not by insert/apply).
  `helixhr.tests.utils.ensure_holiday_list_assignment(company)` creates a Holiday List and a
  submitted Holiday List Assignment (`applicable_for="Company"`) covering the current year.
- **Role "Employee" has no `delete` permission on Leave Application by default** in this
  HRMS version's base DocPerm, so withdraw (R14, KTD17 -- `frappe.delete_doc` on a pending
  Leave Application) is refused with a plain `PermissionError` out of the box. Fixed with one
  Custom DocPerm rule (role `Employee`, permlevel 0, `delete=1`, `if_owner=1` so it only ever
  applies to the caller's own documents) -- applied by
  `helixhr/patches/v1_0/apply_permission_deltas.py`. It is a **second** rule alongside the
  standard Employee one, not `if_owner` set on the standard rule: `if_owner` on the base rule
  moves read/write/report into the owner-only bucket too, and an employee then cannot see a
  leave request HR filed for them.
- **Never ship `Custom DocPerm` as a fixture.** `frappe.permissions.get_valid_perms` discards
  *every* standard DocPerm for a doctype that has at least one Custom DocPerm row, so a
  fixture carrying one role wipes out all the others on a fresh site -- Leave Application and
  Timesheet lost HR Manager, HR User, Leave Approver and Projects User this way, and it was
  invisible on this dev machine because HRMS's Employee Self Service User Type had already
  copied the standard rows in through `frappe.permissions.add_permission`. Apply deltas in a
  patch on top of `setup_custom_perms` instead (P2-U1).

## Employee gets locked/HR-only fields from more than one place (U5 follow-up)

`helixhr/fixtures/property_setter.json`'s permlevel pass only queried the `DocField` doctype,
which covers fields defined in Employee's own doctype JSON. HRMS adds roughly fifteen more
fields to Employee as `Custom Field` records -- `leave_approver`, `employment_type`, `grade`,
`default_shift`, `expense_approver`, `shift_request_approver`, `payroll_cost_center`, the two
health-insurance fields, and others -- a **separate doctype** from `DocField`, easy to miss
with a query scoped to just the one. Found only because U6 needed `leave_approver` and it
turned out to still be writable by the ESS role. If a future unit needs another Employee
field and it doesn't show up under `DocField`, check `Custom Field` (`dt=Employee`) too.

## Playwright

Three identities log in once via `POST /api/method/login` (not the UI) and save
`storageState` for reuse across specs — see `frontend/tests/e2e/auth.setup.ts`. The
employee/manager `storageState` files back the `employee`/`manager` projects in
`playwright.config.ts`; the "no active Employee" scenario logs in fresh inline instead (it
isn't one of the two projects' identities).

Create the fixtures over HTTP before running Playwright against a site that doesn't already
have them (enable `allow_tests` first, same gate `bench run-tests` itself needs):

```bash
bench --site <site> set-config allow_tests true
# log in as Administrator, then:
curl -b <cookie-jar> -X POST https://<site>/api/method/helixhr.tests.utils.setup_playwright_fixtures
```

Run the suite (values below match this dev VM; adjust `BASE_URL`/`SITE_HOST` elsewhere):

```bash
cd frontend
BASE_URL=http://<frontend-host>:8080 SITE_HOST=<site> \
  TEST_USER_PASSWORD='<value of TEST_PASSWORD in helixhr/tests/utils.py>' \
  yarn test:e2e
```

**ROOT-CAUSED (U11): every JS/CSS asset 404s on a from-scratch CI bench, so the Vue app never
mounts.** CI's e2e job failed non-flakily across three separate pushes/reruns -- every real page
load blank (white screen), including the guest-redirect case first flagged back in U4. Chasing it
by reading text assertion diffs alone went nowhere; what actually cracked it was adding
`screenshot: 'only-on-failure'` to `playwright.config.ts` plus an `actions/upload-artifact` step
on the e2e job (`if: failure()`, diagnostic only, never affects pass/fail) so a failing run's
screenshots and traces survive the runner instead of being discarded on exit. The very first
downloaded trace showed the smoking gun: `page.goto('/helixhr')` returns the HTML shell fine
(200), but every asset under `/assets/helixhr/helixhr/assets/*` -- `index-*.js`, `index-*.css`,
etc. -- 404s. No JS ever runs, so nothing mounts and no client-side router logic (including the
guest-redirect check) ever fires. Confirmed directly: `sites/assets/<app>` is a **symlink to
`apps/<app>/public`**, created by `bench build`, and it's what the dev server actually serves
static assets from. `yarn build` (the CI "Build frontend" step) writes straight into
`apps/helixhr/public/helixhr/` -- it never touches `sites/assets/` at all. Reproduced locally by
deleting this bench's own `sites/assets/helixhr` symlink (404s appeared immediately) and fixed by
running `bench build --app helixhr` (recreated the symlink, 404s gone). CI's workflow had no step
that ever created this symlink; fixed by adding a `bench build --app helixhr` step between "New
site, install apps" and "Start bench" in the `e2e` job.

**Why local verification kept looking clean while CI kept failing**: this dev bench's
`sites/assets/helixhr` symlink was created once, early in this project's very first `bench init`,
and it lives at the *bench* level, not the *site* level -- so it survives every `bench new-site
test_site` / `bench drop-site test_site` cycle done since. Testing against a "freshly dropped and
recreated `test_site`" on this bench therefore never actually exercised the missing-symlink case
that a truly from-scratch bench (every CI run, `bench init` from nothing) hits every time. The
only environment that matched CI's real failure mode was a from-scratch `bench init`, which this
local session never did mid-investigation -- only `bench new-site`/`drop-site` on an
already-initialized bench. Lesson: "fresh site" and "fresh bench" are not the same reset, and only
the latter reproduces a bench-level asset-linking bug.

### Full local Playwright runs need low parallelism, and a data reset between runs (U11)

`bench start`'s Werkzeug dev server (used both here and in CI) isn't built for the kind of
concurrency `fullyParallel: true` throws at it. On this local Docker bench, running the full
suite with its default worker count against an already-warm site produced transient failures
(login-dashboard heading not visible, leave-type dropdown not populated) that vanished when the
same suite was rerun with `--workers=1` — this is contention/timing on the single dev-server
process, not an application bug. CI gets away with the default parallelism because it always
runs against a freshly created site with no accumulated load; a long-lived local dev/test site
does not have that luxury.

Separately, `timesheet-approval.spec.ts` says up front that it's designed to run once per site
(it drives a real reject → resubmit → approve cycle against "this week", and a second run finds
that week's Timesheet already `Approved`, which is correct behavior, not a bug, but reads to
Playwright as "combobox is disabled" because the page correctly hides the edit form). Reset
before every local rerun of the full suite:

```python
# bench --site <site> console
import frappe
emp = frappe.db.get_value("Employee", {"user_id": "employee@helixhr.test"}, "name")
for ts in frappe.get_all("Timesheet", filters={"employee": emp}, pluck="name"):
    frappe.db.set_value("Timesheet", ts, "docstatus", 2, update_modified=False)
    frappe.db.delete("Timesheet Detail", {"parent": ts})
    frappe.db.delete("Timesheet", {"name": ts})
for la in frappe.get_all("Leave Application", filters={"employee": emp}, pluck="name"):
    frappe.db.set_value("Leave Application", la, "docstatus", 2, update_modified=False)
    frappe.db.delete("Leave Application", {"name": la})
frappe.db.commit()
```

Use raw `frappe.db.set_value`/`delete` here, not `doc.cancel()` / `frappe.delete_doc()` -- see
the next section for why a plain `doc.cancel()` from a bare `bench console` session can itself
throw.

### A bare `bench console` session can crash on `doc.cancel()`/`doc.save()` -- upstream Frappe bug, not ours (U11)

Cancelling or saving a document that has a Notification alert on it (this app ships
`HelixHR Timesheet Status Changed` on `Timesheet`, `["dt": "Notification"]` fixture) from a bare
`bench --site <site> console` session can raise:

```
UnboundLocalError: cannot access local variable 'value' where it is not associated with a value
```

from deep inside `frappe/locale.py:get_locale_value` (`return value or frappe.db.get_default(key)`
-- `value` is only assigned when `lang and lang != "en"`, so a session where `frappe.local.lang`
is `None` or exactly `"en"` hits an unbound local, not a project bug -- every frame in the
traceback is inside `apps/frappe`, none in `helixhr`). A real HTTP request (the browser sessions
Playwright drives, and presumably production traffic) sets `frappe.local.lang` from the request
context and does not hit this; a bare console session never goes through that request setup, so
`frappe.local.lang` stays `None`. Confirmed this only reproduces via direct console
`doc.cancel()`/`doc.save()` calls, never through the app's own `act_on_approval` /
`apply_workflow` code paths exercised by Playwright or the Python test suite -- so no HelixHR
code change here. Work around it in the console with raw `frappe.db.set_value` (see above)
instead of the full `Document.cancel()`/`.save()` lifecycle when cleaning up test data by hand.

## The app shell never existed until after U11, and the e2e suite could not see that

U3's plan step 1 ends "...load the shell with the bottom nav (phone) or side nav (desktop) from
U1". That shell was never built. `frontend/src/App.vue` shipped as `<div><router-view /></div>`
and stayed that way through U4-U12: eleven working pages with no way to reach ten of them except
by typing the URL. The dashboard's three quick-action buttons were the only links on the entire
portal.

**Why every unit passed anyway.** Every e2e spec written before this navigates with
`page.goto('/helixhr/leave')` and friends. Not one of them clicked a nav item, so "does this
portal have navigation at all" was never asserted, and a portal with zero navigation produced a
fully green suite. `tests/e2e/navigation.spec.ts` now covers the gap: it reaches every page by
clicking, checks `aria-current` follows the route, checks Approvals is hidden from a
non-manager and present for a manager, exercises the phone tab bar and its More sheet, and
checks `/not-linked` still renders with no nav chrome.

**Lesson for future units.** A route-level test that starts with `goto(url)` proves the page
renders. It proves nothing about whether a user can get there. At least one test per navigation
surface has to travel the way a person would.

### Three real bugs the shell work uncovered

- `createResource({ auto: true })` at *module* scope never fetches. frappe-ui hangs auto-fetch
  off the owning component's `onMounted`, and a module-scope resource has no owning component.
  The shared unread-notification count sat at zero with five unread rows in the API until
  `unreadCount.fetch()` was called explicitly. Anything created outside `setup()` must fetch by
  hand.
- The app mounts *before* the first router guard resolves. The shell's `onMounted` asked for the
  manager's direct-report count, saw `session.employee` still null, gave up, and never retried,
  so managers silently lost their Approvals nav item. That lookup is chained off the guard's own
  `setEmployee()` now, not off a component lifecycle hook.
- Profile rendered a literal `{}` for Manager. It resolved the name with
  `frappe.client.get_value` on the *manager's* Employee row, which U5's permlevel lock correctly
  forbids -- the call returns `{}`, not an error. It reads `manager_name` from
  `helixhr.api.get_dashboard` (server-side `_get_employee_header`) now, so the two screens cannot
  disagree.

### Do not run `prettier` on `frontend/`

The repo-root `.editorconfig` sets `indent_style = tab` for `*.js`/`*.vue` (it exists for
Frappe's Python and JSON conventions), Prettier honours `.editorconfig`, and the frontend is
2-space throughout. A single `npx prettier --write src` rewrote all 22 source files to tabs and
collapsed the one-attribute-per-line style `eslint-plugin-vue` enforces. `yarn lint`
(`eslint src`, with `--fix`) is the formatter for this directory; there is no prettier script in
`package.json` for a reason.

## Re-running the `/impeccable` UI audit

The pass that produced the contrast/touch/focus corrections used a throwaway Playwright probe
rather than a saved script -- it logs in as both fixture users, loads all ten routes at 1440px
and at 360px with `hasTouch: true` (without that, `pointer: coarse` never matches and every
touch-target check silently passes), and reads back computed contrast ratios, target sizes,
focus-ring styles, heading order and console errors in one render. Screenshots land in
`frontend/test-results/audit/` (gitignored).

Two things it gets wrong unless you account for them, both of which cost real time here:

- **Disabled controls are exempt.** WCAG 1.4.3 does not apply to disabled elements. The three
  hits that survive on the Timesheet page are the `Select`s for an already-approved week; they
  report `disabled: true`. Do not "fix" them.
- **`<label for>` is not the only way to name a control.** A naive probe that checks only
  `aria-label`/`textContent` reports all 18 frappe-ui `FormControl` inputs as unnamed. They are
  correctly associated via `label[for]` + generated `id`.

Run `node .claude/skills/impeccable/scripts/detect.mjs --json frontend/src` for the mechanical
slop checks; it is fast and catches things like the thick coloured `border-l-4` the unread
notification rows used to carry.

## Three bugs the e2e suite could not see, and why

All three shipped green. Each is the same shape: a test asserted that a *label* appeared and never
that the thing behind it worked.

- **Sign out dumped users on a Frappe "Not permitted" page.** `logout` is POST-only; `call('logout')`
  sent a GET because `call()` only upgrades to POST when given params. Frappe refused with
  `PermissionError`, a bare `.catch(() => {})` swallowed the 403, and the redirect ran with the
  session still alive -- at which point `/login` 301s a logged-in user to their Desk home page,
  which an Employee Self Service user cannot open. No test clicked Sign out.
- **`attendance_this_month` never worked, for anyone, ever.** `_get_attendance_summary` passed the
  `datetime.date` objects `get_first_day()`/`get_last_day()` return to
  `hrms.api.get_attendance_calendar_events`, which is annotated `from_date: str`; Frappe's typing
  validation raised `FrappeTypeError` and `_safe` turned it into `null`. The card rendered "Nothing
  recorded yet" regardless of real attendance -- a type error wearing a plausible empty state, which
  is the failure mode that hides longest. Any call into an `hrms.api` method must stringify dates.
- **An employee could never see why their timesheet was sent back.** `Timesheet.vue` read the
  manager's comment with `frappe.client.get_list` on `Comment`, a doctype the Employee Self Service
  role cannot read, so the call 403'd on every rejected week. `timesheet-approval.spec.ts` asserted
  only that the text "Sent back" was visible, never the reason, so the flow passed for months. The
  comment now ships inside `get_my_week`'s payload, resolved server-side, and both the Python and
  e2e tests assert the reason text itself.

**The pattern worth remembering:** assert the payload, not the chrome. "Sent back is visible" and
"the employee can read why" are different claims, and only the second one is the feature.

## Attendance exceptions are dormant until a check-in device exists (R16)

No attendance device is configured, so the Attendance page's exceptions strip needs a rule that
does not turn every past day red the moment it ships. `helixhr.api.get_my_attendance` only counts
a day as **missing** when it is:

- on or after `tracking_since` — the employee's **first ever** submitted Attendance record. Before
  that date the company simply was not recording, so nothing can be absent from it. With no records
  at all, `tracked` is false and `missing` is always empty;
- strictly before today (today is not late yet);
- not already recorded, not a holiday on the employee's holiday list, and not covered by approved leave.

If the employee has no resolvable holiday list, working days are unknowable, so `working_days_known`
comes back false and `missing` stays empty rather than being guessed.

**This needs no code change when the device arrives.** The first real Attendance record sets
`tracking_since` and the feature starts working from that date forward. `late` reads Attendance's
own `late_entry` flag, which only a device (or HR) sets, so it stays at zero until then too.

`helixhr/tests/test_api_attendance.py` covers the no-data case first, because that is the one that
ships today.

## Performance baseline (P2-U0)

`frontend/tests/e2e/performance.spec.ts` is the frozen measurement protocol behind P2-R21..P2-R24.
It is a Playwright project of its own, and it only exists when `BASELINE_MODE` is set, so a plain
`yarn test:e2e` can never pick it up: it throttles CPU 4x and the network to
1.6Mbps/750Kbps/150ms, so it takes minutes and would only make the other specs flaky.

Seed the volume profile first. `setup_playwright_fixtures` gives the happy path three rows;
`setup_baseline_fixtures` gives one employee a year of real history (365 Attendance, 260 check-ins,
52 Timesheets, 40 Leave Applications, 100 HR Requests, 250 Notification Logs), 200 Employees across
two companies, 75 mixed-company document links, and the manager 20 reports with 25 mixed pending
approvals. Same `allow_tests` gate as every other fixture entry point, so it cannot run on a real
site. It takes about a minute the first time and is idempotent afterwards (a second call is ~1s and
must report identical counts).

```bash
# in the bench container
bench --site test_site execute helixhr.tests.utils.setup_baseline_fixtures   # seed (idempotent)
bench --site test_site execute helixhr.tests.utils.baseline_fixture_counts   # expected vs actual
bench --site test_site execute helixhr.tests.utils.teardown_baseline_fixtures  # reset

# on the host, after a production build of the exact commit under test
cd frontend && yarn build
BASELINE_MODE=full BASE_URL=http://localhost:8000 SITE_HOST=test_site \
  yarn test:e2e -- --project=baseline --workers=1
```

**Tear the baseline down before `bench run-tests`.** The seed is not test-suite-neutral: with
it in place five `test_api_dashboard_week` queue tests fail, because `_get_needs_you` picks up
the 100 seeded "Baseline request" rows. Run `teardown_baseline_fixtures` first and the suite is
back to one failure (the leave-balance baseline documented in `CLAUDE.md`) instead of six.

`BASELINE_MODE=lightweight` runs the same protocol with 3 cold loads and 6 interactions instead of
10 and 20 — that is the after-each-unit regression run; the full protocol is for U0 and U9.

The reset that is always honest is a fresh site. `teardown_baseline_fixtures` exists for a
long-lived local site: it cancels submitted Leave Applications before deleting them so HRMS unwinds
its own Leave Ledger Entries, and removes the manager DocShares the seeded pending timesheets
created. It deliberately leaves the two fixture identities and their allocations alone.

Results, screenshots and the environment pin land in `.impeccable/review/baseline/`, which is
gitignored along with the rest of `.impeccable/review/` — nothing from a run is committed, and the
run records only URLs (query strings stripped), byte counts and timings, never record content. The
numbers that matter are quoted in a plan/change record by their **result identifier**, which is what
each run prints and writes into its own JSON.

A run refuses to produce a number it cannot trust and fails instead: any console/page error, any
request that fails or returns >= 400, a missing metric (no LCP, no event-timing entry, an
unsupported PerformanceObserver type), a fixture count that differs from `BASELINE_PROFILE`, a
`frontend/src` file newer than the built entry chunk (stale build), or an environment that differs
from `environment-pin.json` (browser version, viewport, throttling, site, fixture anchor). Delete
that pin only when you mean to re-baseline. Cold and warm runs are labelled separately and only the
cold runs feed the accepted numbers; the warm sample is one extra load in an already-populated
context and is reported for context only.

### The first baseline: `P2-U0-full-20260904T2020-ded07d7`

Chromium 151.0.7922.34, 360x800 @3x, CPU 4x, 1.6Mbps/750Kbps/150ms, 10 cold Dashboard loads plus 20
scripted interactions (Leave, Timesheet, Requests, More sheet, Attendance, Home — repeated),
75th percentile by nearest rank, `test_site` on the local bench.

| Metric | p75 (cold) | Target |
|---|---|---|
| LCP | 3936 ms | P2-R23: <= 2500 ms |
| CLS | 0.8431 | P2-R23: <= 0.1 |
| Interaction latency (event timing) | 24 ms | P2-R23: <= 200 ms |
| Requests | 15 | — |
| Application data requests on Dashboard | 4 (`hrms.api.get_current_employee_info`, `frappe.client.get_list`, `frappe.client.get_count`, `helixhr.api.get_dashboard`) | P2-R21: <= 2 |
| Transferred, whole page | 826,220 B | — |
| Transferred JavaScript | 354,014 B | P2-R24: -20% |
| Transferred CSS | 162,906 B | P2-R24: no regression |
| Remote font requests | 2 (`fonts.googleapis.com`, `fonts.gstatic.com`) | P2-R24: 0 |
| Public source maps | yes (`index-*.js.map` returns 200) | P2-R24: none |

Server time at p75: `get_dashboard` 195ms, `frappe.client.get_count` 192ms, `frappe.client.get_list`
189ms, `get_current_employee_info` 162ms. Warm load for comparison: LCP 752ms, 8,174 B transferred.

Two honest caveats, both machine-recorded in every result file rather than argued in prose:

- **No HTTPS staging host exists yet**, so this is the local bench. `asset_content_encoding` reads
  `identity`: the bench dev server serves JavaScript and CSS uncompressed, so the transfer numbers
  above are raw bytes, not gzip. A before/after comparison is only valid between runs with the same
  encoding — compare local to local, or re-baseline on staging before quoting a gzip figure.
- The pre-U0 estimates in the plan (~133KB gzip JS, ~22KB gzip CSS) are superseded by this result
  identifier, per P2-R24.

### The P2-U9 result: `P2-U0-full-20260905T0806-f5aef9f`

Same protocol, same pin, same seeded fixture set, one commit later. This is the number P2-R21..R24
are argued from.

| Metric | U0 (`...20260904T2020-ded07d7`) | U9 (`...20260905T0806-f5aef9f`) | Target | Verdict |
|---|---|---|---|---|
| Application data requests on Dashboard | 4 | **2** (`get_portal_bootstrap`, `get_dashboard`) | P2-R21: ≤ 2 | **pass** |
| Requests, whole page | 15 | **13** | — | pass |
| LCP p75 | 3936 ms | **2860 ms** | P2-R23: ≤ 2500 ms | **fail here, staging gate** |
| CLS p75 | 0.8431 | **0** | P2-R23: ≤ 0.1 | **pass** |
| Interaction latency p75 | 24 ms | **32 ms** | P2-R23: ≤ 200 ms | **pass** |
| Transferred JavaScript | 354,014 B | **277,088 B** (−21.7%) | P2-R24: −20% | **pass** |
| Transferred CSS | 162,906 B | **78,983 B** (−51.5%) | P2-R24: no regression | **pass** |
| Remote font requests | 2 | **0** | P2-R24: 0 | **pass** |
| Public source maps | yes | **no** (`.map` returns 404, none built) | P2-R24: none | **pass** |
| Transferred, whole page | 826,220 B | **404,182 B** | — | pass |

Server time at p75: `get_dashboard` 196 ms, `get_portal_bootstrap` 159 ms. Warm load: LCP 708 ms,
12,811 B.

The harness now prints and records these verdicts itself (`gates` in the result JSON) instead of
leaving them to prose, and an enforced gate that fails invalidates the run. **`P2-R23-lcp` is the
one gate that is measured but not enforced locally**, and the reason is in the same result file:
`asset_content_encoding: identity`. This bench serves JavaScript and CSS uncompressed, so the
emulated 1.6 Mbps link carries about 356 KB that a real proxy would have gzipped to roughly a
third of that. R23 is written against "representative staging"; run the protocol there with
`PERF_GATE=staging` and the gate becomes a hard failure. **Until that run exists on an HTTPS
staging host, R23's LCP clause is unproven, not passed.** CLS and interaction latency are
environment-robust and pass here.

Where the JavaScript went, for anyone re-doing this:

- `feather-icons` is aliased to a stub in `vite.config.js` (−96,010 B raw). frappe-ui's `Button`
  and `Dialog` import `FeatherIcon` unconditionally, and the package is one un-tree-shakeable
  module holding every glyph. Nothing here passes a Feather icon name; a vitest guard fails if
  anything starts to.
- Production source maps are off (no transfer effect, but they were public).
- CSS: `tailwind.config.cjs` scans a named list of frappe-ui components rather than all of them,
  and `src/index.css` expands frappe-ui's three `@tailwind` directives inline instead of importing
  its stylesheet — which also drops the two Inter variable fonts (about 600 KB of assets no rule
  ever referenced).

### Running the whole release set

```bash
# in the bench container
bench --site test_site run-tests --app helixhr
bench --site <site> execute helixhr.preflight.run

# on the host, from frontend/
npx eslint src && npx vitest run && npx vite build
BASE_URL=http://localhost:8000 SITE_HOST=test_site npx playwright test \
  -c tests/playwright.config.ts --workers=1
```

The Playwright run includes the **employee-mobile-webkit** project (P2-U9 step 9): the critical employee
flows on the only engine iOS has, under a coarse pointer. It needs WebKit's system libraries:

```bash
npx playwright install --with-deps webkit     # needs root
```

On a host that cannot install them the browser downloads but refuses to launch
(`browserType.launch` fails immediately). CI installs them; this repo's dev VM cannot, so
**employee-mobile-webkit is a CI-only gate here** and a local run must select the projects explicitly:

```bash
npx playwright test -c tests/playwright.config.ts \
  --project=setup --project=employee --project=manager --workers=1
```

### Rate limits are off on a site with `allow_tests`, on purpose

`helixhr.utils.rate_limits_enforced()` returns false when the site has `allow_tests`. Without that,
the two suites and the limits are mutually exclusive: the Python suite creates far more than ten HR
Requests as one user in one run (the `create_my_request` bound is 10/hour), and running the
Playwright suite twice inside a minute re-trips the 30/minute timesheet bound.

It is safe because `preflight.check_test_mode` **FAILs** any site with `allow_tests` on, and
preflight exits non-zero — so no production site can reach that branch. The bound itself is proved
by `helixhr/tests/test_upload_security.py::TestPerUserRateLimits`, which sets
`frappe.flags.helixhr_enforce_rate_limits` and asserts the eleventh request in an hour is refused.

A site may tighten a bound without a release:

```bash
bench --site <site> set-config helixhr_rate_limits '{"create_my_request": [5, 3600]}'
```

Loosening one is a policy decision, and `preflight.check_rate_limits` FAILs until
`RATE_LIMIT_POLICY` in `helixhr/utils.py` is edited to match.

## Go-live checklist

How the portal is exposed, which host names serve what, how employees are kept
out of Desk, and how a new employee is onboarded are in
[deployment.md](deployment.md). This section is the per-site settings check.


Most of this is checked by one command. Run it on staging, then again on production, after
every deploy; it exits non-zero on any FAIL so a deploy script can gate on it:

```bash
bench --site <site> execute helixhr.preflight.run
```

It reports PASS/WARN/FAIL for: Apply Strict User Permissions, every linked employee having a
User Permission on their own Employee, Custom DocPerm coverage, the two HR Settings behind R14
and self-approval, the legacy approved-but-unsubmitted leave backlog, Disable Signup, the
sign-in phase, password policy, the **exact** upload policy, **every named per-user write
bound**, site `rate_limit`, **test mode**, **CSRF**, the **HTTPS header and cookie probe**, the
HR contact address, the four fixtures the app cannot work without, and the frontend being built.
The checks and their rationale live in `helixhr/preflight.py`.

Three of those are new in P2-U9 and judge *values*, not presence:

- **Upload policy** FAILs unless System Settings lists only PDF/PNG/JPG/JPEG/DOCX/XLSX, Max File
  Size is at most 10 MB, guests cannot upload, and public uploads are restricted to System
  Managers. The app's own `validate_portal_upload` already refuses anything else on an HR Request;
  this is about every *other* upload the site accepts.
- **Per-user write limits** re-derives every effective bound, `helixhr_rate_limits` site config
  included, and FAILs on anything looser than `helixhr.utils.RATE_LIMIT_POLICY`.
- **Test mode off** FAILs on `allow_tests`. That flag both exposes the fixture entry points and
  disables the per-user write limiter, so it is the one setting that must never survive to
  production. On a *test* site this FAIL is expected and correct.

Sign-in is phase-aware through site config rather than a code comment:

```bash
bench --site <site> set-config helixhr_auth_phase entra   # default: "local"
```

In the `local` phase password login must stay on (turning it off with no enabled Social Login Key
locks everyone out) and the Office 365 key is a WARN. In the `entra` phase both flip: a missing or
disabled key is a FAIL, and password login still being enabled is a FAIL.

### Host-only sign-offs

These live outside the site. Two of them are now **machine-detectable** — point preflight at the
real hostname and it fetches the portal over HTTPS and inspects what came back:

```bash
bench --site <site> set-config helixhr_public_url https://<host>/helixhr
bench --site <site> execute helixhr.preflight.run
```

The `HTTPS headers and cookies` check then FAILs unless the response carries
`Strict-Transport-Security`, a `Content-Security-Policy` with `frame-ancestors`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy` and `Permissions-Policy`, and unless the `sid`
cookie is set with `Secure`, `HttpOnly` and a `SameSite` attribute. Unset, it is a WARN that names
this section — never a PASS.

The app sets those headers itself (`helixhr.utils.set_security_headers`, registered as an
`after_request` hook), with `setdefault`, so a proxy that sets a stricter value keeps it. HSTS is
sent only when the request arrived over HTTPS, which is exactly why the proxy item below still
has to be checked by a person the first time.

- [ ] **`X-Forwarded-Proto` reaches Frappe behind the real proxy.** Everything else follows from
      it: Frappe marks the `sid` cookie `Secure` only when `request.scheme == "https"`, and this
      app sends HSTS on the same condition. Confirm with the preflight probe above, and look at
      the cookie in the browser after the first HTTPS login.
- [ ] **Hashed assets are served immutably and compressed.** `/assets/helixhr/helixhr/assets/*`
      filenames all carry a content hash (the CI `Asset, cache and header policy` step asserts
      that), so the proxy should serve them
      `Cache-Control: public, max-age=31536000, immutable` with gzip or brotli on. The bench dev
      server serves `max-age=43200, public` and **no compression at all**, which is why every
      transfer figure above is raw bytes. The portal shell must stay uncached —
      `helixhr/www/helixhr.py` sets `no_cache = 1` and Frappe answers
      `no-store,no-cache,must-revalidate,max-age=0`; do not let a proxy override that.
- [ ] **Re-run the performance protocol on staging with `PERF_GATE=staging`.** R23's LCP clause is
      unproven until that run exists (see the P2-U9 result above).
- [ ] **One screen-reader pass** over navigation, error states, dialogs, status changes, and both
      primary mobile workflows (send a week for approval; send a request with an attachment).
      Everything deterministic is already measured by `visual-foundation.spec.ts` and
      `hardening.spec.ts` — 320px reflow, 200% text zoom, coarse-pointer target sizes, focus
      trapping, dialog close labels, reduced motion. What a machine cannot judge is whether the
      announced order and wording make sense, and that is what this pass is for.
- [ ] **The Entra OAuth round trip**, end to end, including the `redirect-to` behavior above,
      before flipping `helixhr_auth_phase` to `entra`.
- [ ] A Lighthouse accessibility run against Dashboard/Leave at 360px. Optional, and a second
      opinion rather than a gate; it needs the `lighthouse` package, which is not a dependency
      here.

What each preflight line means, for whoever has to fix one:

- **Apply Strict User Permissions** (System Settings) must be on. Without it, a User
      Permission on Employee only directly restricts the *Employee* doctype's own records --
      it does **not** by itself stop an unrelated user from reading a *different* doctype's
      document just because that document has a Link field pointing to Employee (e.g. a
      manager reading a report's pending Timesheet by name, confirmed while writing U8's
      tests). This app's *write* paths don't depend on this setting (Timesheet approval is
      independently enforced by the workflow condition and the `before_submit` guard, not by
      User Permission), but plain reads do -- turn this on before go-live, and re-check HR's
      own Desk views afterwards in case it over-restricts a legitimate cross-employee report
      they rely on.
- **Employee User Permissions**: every active Employee with a `user_id` must have a User
      Permission (`allow = Employee`, `for_value` = their own record). Creating the Employee
      with "Create User Permission" checked does this; the check exists because one missed
      checkbox means that user can read every employee.
- **Document link URLs**: every stored `HelixHR Document Link` must be a plain `http(s)`
      address. The doctype validates on save and nothing revalidates a row that is never saved
      again, so a `javascript:` or `data:` link written before that rule still renders into an
      `:href`. Fix or delete each named row in Desk.
- **Upload policy**: System Settings **Allowed File Extensions** (PDF, PNG, JPG, JPEG, DOCX,
      XLSX -- one per line), **Max File Size** 10, **Allow Guests to Upload Files** off, and
      **Only allow System Managers to upload public files** on. All four are core Frappe
      settings and all four are unset or wrong by default on a fresh site.

      Separately from them, `helixhr.utils.validate_portal_upload` is what actually governs an
      HR Request attachment, and it does not depend on any of the above: private, at most 10MB,
      and PDF/PNG/JPEG/DOCX/XLSX only, checked by extension **and** by leading signature. The two
      OOXML types are opened as zip containers, so a `.docm` renamed to `.docx` (it still carries
      `vbaProject.bin`) and a truncated archive are both refused, as are SVG, HTML, the legacy
      `.doc`/`.xls` formats and anything whose bytes disagree with its name. It runs in
      `api.attach_to_my_request` and again in the `File.before_insert` hook, so a File written by
      any other path gets the same answer. Files attached to an HR Request are additionally
      served with `Content-Disposition: attachment`, so an uploaded document can never render in
      the site's own origin.
- **Site rate_limit**: `bench --site <site> set-config rate_limit '{"limit": 600, "window": 60}'`
      (tune to real traffic). Frappe's site-wide request limiter, separate from and in addition
      to this app's own per-user limiter (`helixhr.utils.rate_limit_per_user`), which since
      P2-U9 covers every sensitive write:

      | Action | Bound |
      |---|---|
      | `update_my_profile` | 20 / minute |
      | `save_my_week`, `submit_my_week` | 30 / minute |
      | `act_on_approval` | 30 / minute |
      | `mark_my_request_read` | 60 / minute |
      | `apply_for_leave`, `withdraw_my_leave` | 20 / hour |
      | `attach_to_my_request` | 20 / hour |
      | `create_my_request` | 10 / hour |

      Buckets are keyed by session user and site, never by IP -- one office behind one address
      would otherwise share one bucket. See "Rate limits are off on a site with `allow_tests`"
      above before wondering why the suites do not trip them.
- **HR contact address**: `bench --site <site> set-config helixhr_hr_contact hr@example.com`.
      Shown as a mailto link on the not-linked page (`frontend/src/pages/NotLinked.vue`), which
      reads it from the `window.helixhr_hr_contact` global that `helixhr/www/helixhr.py` injects
      through the shell's `boot`. Unset, the page says "Contact HR" with no address; nothing is
      hardcoded. Site config is cached 60s per web process, so no restart is needed.
- **Frontend built**: `cd frontend && yarn build` (or inside the bench container:
      `cd apps/helixhr/frontend && yarn build`). This regenerates `helixhr/public/helixhr/` and
      `helixhr/www/helixhr.html` from source; both are gitignored, so every deploy rebuilds.
      Rerun after any `frappe-ui` version bump. On a bench that has never served this app's
      assets before, also run `bench build --app helixhr` once -- `yarn build` alone does not
      create the `sites/assets/helixhr` symlink the server actually serves static assets from;
      see the CI root-cause writeup above for what happens when it's missing.

Not a check, but part of going live: to surface a document on the Documents page, create a
**HelixHR Document Link** (Desk list, HR Manager/System Manager only) with `title`, `url`,
optional `company` (scopes it to one company; leave blank for all) and `description`. No app
code change is needed for a new link.
