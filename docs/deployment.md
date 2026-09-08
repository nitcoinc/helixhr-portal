# Deploying HelixHR

How the portal is exposed, who can reach what, and what HR can change without a
developer. Read `README.md` first for install and per-site configuration; this
document covers the decisions you only make once, at go-live.

Everything here is per-site data or reverse-proxy configuration. None of it
lives in the repo, so CI can never check it — `bench --site <site> execute
helixhr.preflight.run` is what checks it, and it must be run on staging and
again on production.

---

## One site, two audiences

HelixHR is not a separate application. It is a Frappe app installed alongside
ERPNext and HRMS **on the same site**, sharing one database, one session and
one login page:

| Audience | Reaches | Is |
|---|---|---|
| Employees and managers | `/helixhr` | the portal in this repo |
| HR and administrators | `/app` (Frappe Desk) | stock Frappe HR / ERPNext |

There is no second system of record. Every record the portal shows is a native
HRMS document, so a leave application approved in the portal is the same row HR
sees in Desk, immediately.

## Where people land after signing in

Both audiences use the same `/login`. What happens next is decided by
`helixhr.utils.portal_home_page`, registered as Frappe's
`get_website_user_home_page` hook:

- A user with an **active Employee record** who does **not** hold `HR Manager`,
  `HR User`, `System Manager` or `Administrator` → `/helixhr`.
- Everybody else → whatever Frappe would have done anyway, which for a System
  User is Desk.

Managers are employees too, so they also land on the portal; their extra
Approvals page appears inside it. An HR person who is *also* an employee keeps
Desk — the rule is deliberately "does not work in Desk" rather than "holds the
Employee role", because HR staff hold that role as well.

**Three things silently override this**, in Frappe's own precedence order.
`preflight`'s `Portal landing` check FAILs on all three, because the symptom —
"our people keep ending up in ERPNext" — reads as a portal bug and is nearly
impossible to trace back to a field somebody set in Desk months earlier:

| Override | Where | Beats the hook because |
|---|---|---|
| `home_page` on a Role | Desk → Role → Employee | Frappe checks Role home pages before any hook |
| Default Portal Home | Desk → Portal Settings | also checked before hooks |
| `default_workspace` on a User | Desk → User | applied last, after the answer is resolved |

Frappe caches the resolved landing page per user. After changing any of the
above, run `bench --site <site> clear-cache`.

## Restricting employees to the portal

By default a portal user is a Frappe **System User**, which means that even
though they now *land* on `/helixhr`, they can still type `/app` and get the
Desk shell. Their data is safe — User Permissions scope every record to their
own Employee — but they will see workspace tiles for Accounting, Buying,
Selling and the rest, which is confusing and looks like a leak even when it is
not.

Close it at the reverse proxy, with two host names pointing at the same site:

```nginx
# HR and administrators: the whole site, Desk included.
server {
    server_name hr.example.com;
    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # required, see below
        proxy_pass http://127.0.0.1:8000;
    }
}

# Employees: the portal only.
server {
    server_name portal.example.com;

    # Sign-in lands on /helixhr by itself; this covers a bookmarked bare host.
    location = / { return 302 /helixhr; }

    # The Desk UI is not served here. 404 rather than 403: there is no reason
    # to advertise that something exists at this address.
    location /app  { return 404; }
    location /desk { return 404; }

    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

Both names must resolve to the **same Frappe site**, so either name the site
after one of them and add the other with `bench setup add-domain`, or keep the
`Host` header intact as above so Frappe resolves the site the same way for
both.

Two things to be honest about:

- **Do not block `/api/method/frappe.*`.** The portal legitimately calls
  Frappe's own generic endpoints — `frappe.client.get_count` (which routes
  through `frappe.desk.reportview`) feeds the unread badge, and the
  notification-log methods mark rows read. Blocking the `frappe.desk` namespace
  breaks the portal.
- **This hides the Desk UI; it is not the security boundary.** Someone who
  knows the API could still call it directly from `portal.example.com`. The
  real boundary is Frappe permissions plus User Permissions plus this app's
  session-scoped methods, all of which are tested. The proxy rule exists so
  employees are not *presented* with a system that is not theirs.

If you later want a Frappe-level block rather than a proxy-level one, the
mechanism is HRMS's `Employee Self Service` User Type — a non-System user type
cannot open Desk at all. It is a bigger change than it looks: that type's
allowed-doctype list does **not** include `HR Request`, `HelixHR Document Link`
or `Attendance`, so it has to be extended and every portal screen re-verified
against the new permission model.

## Adding an employee

Four things have to be true before someone can use the portal. Three are
automatic if you use the Employee form:

| # | What | How |
|---|---|---|
| 1 | An Employee record, status Active | HR creates it in Desk |
| 2 | A User linked in `user_id` | ERPNext's **Create User** button on the Employee, or type the address into `user_id` |
| 3 | The `Employee` role on that User | added automatically by **Create User** |
| 4 | User Permissions for Employee **and** Company | created automatically when `user_id` is set on the form, because `Create User Permission` defaults to on |

**The one trap.** ERPNext's `Create User` *button* passes
`create_user_permission = 0`, while editing `user_id` on the form honours the
checkbox, which defaults to on. Take the button path and step 4 can be skipped
— and without it, strict User Permissions do not scope that person to their own
records. `preflight`'s `Employee User Permissions` check FAILs on exactly this
and names the user, so run preflight after onboarding a batch of people.

To remove access, set the Employee's status to Left or disable the User. The
landing rule checks Employee **status**, not merely the link, so a leaver is not
redirected into a portal that would refuse every read.

## What HR can change without a developer

These are data, not code, and take effect immediately (site config is cached
for 60 seconds; Desk records are not cached at all):

- **Documents page contents** — one `HelixHR Document Link` record per link.
  Leave `company` empty for "everyone", or set it to scope the link to one
  company. URLs must be `http(s)`; anything else is refused on save.
- **Leave types, holiday lists, allocations, approvers** — stock HRMS. The
  portal reads whatever HRMS says.
- **Request categories** — the `category` field's options on `HR Request`.
- **HR reply text** — the `hr_note` field on a request. Changing it notifies
  the employee and puts the request back in their queue.
- **The HR contact address** shown to a signed-in user with no Employee record
  — `bench --site <site> set-config helixhr_hr_contact hr@example.com`.

Approving leave or a timesheet in Desk works too, and the portal reflects it —
the portal's approval path exists for convenience, not as the only route.

## What HR sets up before the self-service surfaces work (P3-U9)

Six screens shipped in phase 3 — Payslips, Holidays, check-in on Attendance,
Fix a day, Team and Directory. Team and Directory need nothing at all: Team
appears for anyone with an active direct report, and Directory reads the
Employee records that already exist. The other four each rest on HRMS data
somebody has to create, and until it exists the screen tells the employee the
truth (an empty year, a missing button, no payslips) rather than failing. Every
item below is checked by `helixhr.preflight.run`.

### Check-in: two HR Settings, then a Shift Type and Shift Assignments

**HR Settings → Allow Employee Checkin From Mobile App**
(`allow_employee_checkin_from_mobile_app`) is the master switch. With it off,
`get_my_attendance` returns `checkin.enabled: false` and the Today strip on
Attendance never offers a punch. `preflight`'s `Check-in settings` check WARNs
rather than FAILs, because a site may deliberately not want portal check-in.

**HR Settings → Allow Geolocation Tracking** (`allow_geolocation_tracking`) is
reported by preflight as information, never judged (P3-KTD4), and it is the one
flag to think about before turning on:

- The portal requires coordinates for its own punches **whatever this flag
  says**. `punch_my_checkin` validates them itself, and the browser is asked
  for a position when the sheet opens. Turning the flag on changes nothing
  about the portal.
- Turning it on **does** make HRMS refuse a coordinate-less Employee Checkin
  from *any* source — a biometric or RFID device that posts no position, and a
  punch typed by HR in Desk. On a site that has those, this flag stops them
  working. Leave it off unless every punch source carries coordinates.

Then the part without which nothing becomes attendance:

| # | What | Why |
|---|---|---|
| 1 | A **Shift Type** with **Enable Auto Attendance** on | HRMS marks Attendance from punches only for such a shift. With none, `preflight`'s `Shift Types` check WARNs and the button never appears at all. |
| 2 | **Process Attendance After** set on that Shift Type | HRMS ignores every punch before this date. Empty, and the nightly job has no start point — preflight WARNs. |
| 3 | **Auto Update Last Sync** on, *or* **Last Sync of Checkin** kept current | HRMS marks attendance only for punches *older* than `last_sync_of_checkin`. A shift whose sync never advances collects punches that stay bare Employee Checkin rows for ever and later read as missing days. Preflight WARNs when the field is empty, or older than two days, and auto-update is off. |
| 4 | A submitted, Active **Shift Assignment** per employee | The punch button is offered only while HRMS resolves a shift window (grace periods included) for *now* — `_shift_windows` makes the same call `EmployeeCheckin.fetch_shift` does. Outside the window HRMS stores the punch `offshift` and never marks attendance, so the portal refuses it instead and the strip says when check-in opens. |

An open-ended Shift Assignment (no end date) is fine and is the common case;
it is also why `create_my_attendance_request` resolves the shift itself when
HRMS's own lookup returns nothing (P3-KTD14).

### Optional: a geofence, through Shift Location

A **Shift Location** with a radius, referenced from the Shift Assignment, is
HRMS's geofence. The portal does not implement one and has no UI for it: it
sends the coordinates, HRMS measures the distance, and the check-in sheet shows
HRMS's own refusal sentence verbatim — it names the radius, which is the number
that makes the refusal actionable. Without a Shift Location the coordinates are
recorded and nothing is enforced; that is a policy position, not a bug, and the
plan records it as one (P3 risk table).

### Holiday lists have to resolve for every employee

`get_my_holidays` resolves a list through HRMS's own
`hrms.utils.holiday_list` — the employee's **Holiday List Assignment** first,
then the company's. Confirm one of the two covers every active employee:

- With no list, the Holidays page shows the Attendance page's "cannot tell yet,
  ask HR" state, never an empty year (P3-R11).
- The same gap disables **Send** on a Fix a day request: with no list,
  `get_attendance_request_preview` returns `known: false`, because HRMS's own
  warning call raises rather than answering, and the sheet cannot say which
  days it would mark.

`bench --site <site> execute helixhr.preflight.run` reports this directly:
**Holiday list coverage** walks every active employee, asks HRMS which list
resolves for them today, and FAILs naming the people it found none for. Run it
after assigning lists and again after any bulk employee import (P3-R26).

### Payslips need payroll to have run in ERPNext

The portal shows **Salary Slips** and nothing else. `get_my_payslips` lists the
employee's own slips with `docstatus == 1`, so:

- Until a payroll entry has been submitted in ERPNext, the page is correctly
  empty for everybody. Draft slips are invisible; cancelled slips are hidden;
  a `Withheld` slip is listed with a "Withheld, ask HR" badge and no PDF.
- **The PDF is the site's own print format.** `download_my_payslip` renders
  `frappe.get_meta("Salary Slip").default_print_format`, falling back to
  `Salary Slip Standard`. Set the doctype's default print format to whatever
  HR emails today, or the PDF an employee downloads will not look like the one
  they are used to. It is the doctype's own `default_print_format`, set from a
  Salary Slip's print view or through Customize Form — per-site data, so no
  release is needed.
- **The host needs a PDF generator.** `download_my_payslip` renders through
  Frappe's own PDF path, which shells out to `wkhtmltopdf` (or Chrome when the
  site sets `pdf_generator`). The Frappe production images carry it; a
  hand-built bench may not, and without it the download answers 500 while
  every other payslip screen looks correct. Preflight's **PDF generator**
  line FAILs when the binary is missing.

Payslip email settings stay HRMS's own (Payroll Settings). The portal never
sends a slip.

### Location reaches the browser only over HTTPS, and only if the header allows it

Two host-level conditions, both of which produce the *same* symptom — a
browser that reports the user denied location when they never saw a prompt.

1. **Serve the portal over HTTPS.** The Geolocation API is a secure-context
   API. On a plain-`http` origin (a dev bench on a LAN IP, for instance) no
   browser will ever offer a prompt. The check-in sheet detects this case
   specifically and says "Location needs a secure connection" instead of
   telling somebody to change a browser setting that is not the problem.
2. **Do not let the proxy set `Permissions-Policy` for geolocation, or allow
   `self`.** The app sends
   `camera=(), microphone=(), geolocation=(self), payment=(), usb=()` from
   `helixhr.utils.set_security_headers`, with `setdefault` — so a reverse proxy
   that sets its own `Permissions-Policy` **wins**, and a proxy sending
   `geolocation=()` disables the API for the whole origin (P3-KTD12).

   ```nginx
   # Wrong: the browser reports a denial the portal cannot tell from the
   # employee's own choice.
   # add_header Permissions-Policy "geolocation=()" always;

   # Right: either omit the header entirely and let the app set it, or
   # allow this origin.
   add_header Permissions-Policy "camera=(), microphone=(), geolocation=(self), payment=(), usb=()" always;
   ```

`preflight.check_public_endpoint` FAILs unless the effective header on the real
host contains `geolocation=(self)`, which is why `helixhr_public_url` is worth
setting on every site:

```bash
bench --site <site> set-config helixhr_public_url https://<host>/helixhr
bench --site <site> execute helixhr.preflight.run
```

CI asserts the same value on the built shell, so the app half of this cannot
regress silently.

### Punch coordinates: the retention key, the daily job, and the notice

Every portal punch stores a latitude and longitude. The erasure path shipped
with the feature (P3-R28, P3-KTD15); only the **period** is an open decision,
and it is HR and legal's, not engineering's:

```bash
bench --site <site> set-config helixhr_checkin_location_retention_days 90
```

- `helixhr.tasks.null_stale_checkin_coordinates` runs **daily** (registered in
  `hooks.scheduler_events`) and clears `latitude`, `longitude` and
  `geolocation` on Employee Checkin rows older than that many days — **and**
  strips the same three fields out of their `tabVersion` rows, because Employee
  Checkin has `track_changes` and erasing the row alone would leave the
  coordinates in its history. "Clears" means `0`, `0` and `""`: Frappe's Float
  columns are `NOT NULL`, so zeroed is as empty as the column gets, and the job
  selects on `is set` so an already-erased row is never revisited.
- `events.employee_on_update` does the same immediately when an Employee's
  status becomes `Left`.
- Until the key is set the job does nothing and
  `preflight.check_checkin_location_retention` WARNs. Coordinates are then kept
  indefinitely. The job never restores anything, so a period set too short is a
  one-way loss — which is why it is unset rather than defaulted.
- It is a scheduled job, so it needs the site's scheduler to be enabled
  (`bench --site <site> enable-scheduler`; `bench doctor` reports the state).
  Preflight checks the key, not the scheduler: a site with the key set and the
  scheduler paused looks compliant and erases nothing.

**What the employee is told, and who can read it.** No consent record is kept
(the DPDP legitimate-use basis covers a punch-moment snapshot), so the sentence
in the check-in sheet *is* the disclosure. It is written once, in
`frontend/src/components/CheckInSheet.vue`:

> Your location is saved with this punch so HR and your manager can see where
> you checked in from. It is read once, when you tap the button below — the
> portal does not follow you between punches.

That is an accurate description of who can actually read the coordinates, and
it is wider than one row: Employee is a nested set, so a manager's own User
Permission reaches every Employee below them and therefore their punches, not
only their direct reports'. HR roles read everything. The employee reads their
own. The portal itself never sends coordinates back to a browser —
`get_my_checkins` returns `has_location` as a boolean and nothing more — so the
readers above are Desk readers. An Employee Checkin export from Desk carries
the coordinates and is HR Manager only.

Changing that sentence is a compliance change, not a copy tweak. A California
risk assessment and notice-at-collection text for US employees is HR work that
this phase deliberately did not attempt.

## Before the migrate that ships the attendance workflow (P3-U5)

`Attendance Request Approval` (P3-KTD6) gives Attendance Request a
`workflow_state`, and Frappe backfills every row that already exists by
docstatus: drafts become `Draft`, submitted ones `Approved`, cancelled ones
keep a null state. Count the drafts first:

```bash
bench --site <site> execute frappe.db.count   --args '["Attendance Request", {"docstatus": 0}]'
```

Either have HR submit those in Desk before the migrate, or afterwards move
each of them along with the workflow's HR-only `Approve` action, which takes an
old draft straight to `Pending HR`. Confirmers need the **HR Manager** role —
HR User is refused at the final step on purpose. Employees never submit an
attendance request themselves; the manager's approve is a save and HR's is the
submit that writes Attendance.

`events._is_hr` is the exact rule behind "HR": `Administrator`, or a user
holding **HR Manager** or **System Manager**. HR User is not in that set, so an
HR User can open a request in Desk and cannot confirm it, edit it out of a
pending state or delete it. Grant HR Manager to whoever is meant to confirm.

**Never cancel an approved request to correct a day.** HRMS writes the
Attendance rows at HR's submit and cancels them outright on cancel — including
a row it rewrote in place that was Present before the request. Cancelling
therefore leaves a day with no attendance rather than the day it used to have.
The portal refuses a request over a day that already carries real attendance
for exactly this reason (P3-KTD14); if one gets through another route, re-mark
the day by hand in Desk instead of cancelling and re-approving.

## Rolling the phase 3 attendance request back (P3-KTD16)

Rolling this feature back is a **code change plus one data reconcile**, never a
Desk toggle — a workflow deactivated in Desk is reactivated by the next
`bench migrate`, because the fixture is the source of truth.

1. Ship `helixhr/fixtures/workflow.json` with `is_active: 0` on
   `Attendance Request Approval`, then `bench --site <site> migrate`. Frappe's
   `Workflow.on_update` clears the doctype cache, so the "no workflow" answer
   takes effect without a separate cache patch.
2. Remove the `Attendance Request` block from `doc_events` in `hooks.py`
   (`validate`, `on_update`, `before_submit`, `on_trash`) and the portal's
   routes, nav entry and the `attendance` kind from the approvals kind map.
3. **Reconcile away every remaining DocShare.** The share is a live grant with
   `write=1`: leave one behind on a plain draft and a former manager keeps
   write access to somebody's request for ever. With the doc events gone
   nothing removes them any more, so do it once, deliberately:

   ```bash
   bench --site <site> execute frappe.db.delete \
     --args '["DocShare", {"share_doctype": "Attendance Request"}]'
   bench --site <site> clear-cache
   ```

   Count them first (`frappe.db.count` with the same filter) so the rollback
   report says how many grants were withdrawn.

What **stays**, on purpose:

- The `workflow_state` Custom Field and every value in it. Dropping the field
  destroys the only record of which requests were confirmed by whom.
- Every **Attendance** row an approved request wrote. Those are real
  attendance; HR marked them, and payroll and the exceptions strip both read
  them.
- The permission deltas from `apply_permission_deltas` (role Employee's lost
  `share` on Attendance Request, and its lost `create`/`write`/`delete` on
  Employee Checkin). Restoring them is a separate, deliberate decision: with
  `share` back an employee can grant a colleague `submit` on their own request.

## Before you let anyone in

```bash
bench --site <site> set-config allow_tests false     # required on production
bench --site <site> execute helixhr.preflight.run    # must show zero FAIL
```

`allow_tests` is not cosmetic: it exposes the fixture-seeding endpoints **and**
disables the per-user write rate limiter. Preflight FAILs while it is on, and
exits non-zero, so a deploy script can gate on it.

Preflight cannot see outside the site. These stay human sign-offs, and are
listed in `docs/runbook.md`:

- `X-Forwarded-Proto` set by the proxy, or Frappe will not mark the session
  cookie `Secure`.
- Compression and immutable caching for hashed assets at the proxy.
- The performance run against a real HTTPS host (`PERF_GATE=staging`).
- One screen-reader pass.
- The Entra ID round trip, if you move off password login.
