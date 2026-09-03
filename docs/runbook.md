# HelixHR runbook (U3)

Operational steps for a human. Filled in as each unit adds a step; U11 finishes it.

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

## Microsoft Entra ID login (production / staging only)

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
  Custom DocPerm row (role `Employee`, permlevel 0, `delete=1`, `if_owner=1` so it only ever
  applies to the caller's own documents) -- shipped as
  `helixhr/fixtures/leave_application_custom_docperm.json`. **Two `fixtures` entries for the
  same doctype (`"dt": "Custom DocPerm"`) need distinct `prefix` values in `hooks.py`** --
  confirmed the hard way: without a prefix, `bench export-fixtures` names the file purely
  from the doctype, so a second entry for a different `parent` doctype silently overwrites the
  first entry's exported file instead of producing a second one.

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

## Go-live checklist (grows through U11)

- [ ] Confirm every Employee Self Service user has a User Permission on their own Employee
      (query: Employee where `create_user_permission` was unchecked, or User Permission count
      mismatched against active Employee count).
- [ ] Disable Signup and Disable Username/Password Login are set (see above).
- [ ] `X-Forwarded-Proto` reaches Frappe correctly behind the real proxy.
- [ ] The real Entra OAuth round trip (not just password login) has been verified end to end,
      including the `redirect-to` behavior above.
- [ ] System Settings **Apply Strict User Permissions** is turned on. Without it, a User
      Permission on Employee only directly restricts the *Employee* doctype's own records --
      it does **not** by itself stop an unrelated user from reading a *different* doctype's
      document just because that document has a Link field pointing to Employee (e.g. a
      manager reading a report's pending Timesheet by name, confirmed while writing U8's
      tests). This app's *write* paths don't depend on this setting (Timesheet approval is
      independently enforced by the workflow condition and the `before_submit` guard, not by
      User Permission), but plain reads do -- turn this on before go-live, and re-check HR's
      own Desk views afterwards in case it over-restricts a legitimate cross-employee report
      they rely on.
- [ ] System Settings **Allowed File Extensions** and **Max File Size** are set. The app's own
      `file_before_insert` hook (`helixhr/events.py`) only refuses a non-private upload against
      an HR Request -- it does not constrain file type or size. Those are core Frappe settings,
      unset by default on a fresh site.
- [ ] Site config `rate_limit` (Frappe's site-wide request rate limiter, in `site_config.json` /
      via `bench set-config`) is set for production traffic. This is separate from, and in
      addition to, this app's own per-user limiter (`helixhr.utils.rate_limit_per_user`), which
      only covers `update_my_profile` today.
- [ ] To surface a document on the Documents page: create a **HelixHR Document Link** (Desk list,
      HR Manager/System Manager only) with `title`, `url`, optional `company` (scopes it to one
      company; leave blank for all) and `description`. No app code change needed for a new link.
- [ ] Building the frontend for release: `cd frontend && yarn build` (or inside the bench
      container: `cd apps/helixhr/frontend && yarn build`). This regenerates
      `helixhr/public/helixhr/` and `helixhr/www/helixhr.html` from source -- always commit the
      rebuilt output alongside a frontend source change, and rerun after any `frappe-ui` version
      bump. On a bench that has never served this app's assets before (a fresh install, not just
      a rebuild on an already-running bench), also run `bench build --app helixhr` once --
      `yarn build` alone does not create the `sites/assets/helixhr` symlink the dev/prod server
      actually serves static assets from; see the CI root-cause writeup below for what happens
      when it's missing.
- [ ] `frontend/src/pages/NotLinked.vue`'s `hrContactEmail` is a placeholder
      (`hr@nitcoinc.com`, the company domain, not a confirmed real HR mailbox) -- set it to the
      real address before go-live.
- [ ] **Not verified in this environment (say so, don't fake it):** the real Entra ID OAuth round
      trip on a real staging install, and a Lighthouse accessibility run against Dashboard/Leave
      at 360px. This dev setup is a local Docker bench with password-only test users and no
      staging host or Lighthouse tooling available; both need a real staging deploy to check.
