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

- A user with an **active Employee record** who does **not** hold `HR User`,
  `System Manager` or `Administrator` → `/helixhr`.
- Everybody else → whatever Frappe would have done anyway, which for a System
  User is Desk.

Managers are employees too, so they also land on the portal; their extra
Approvals page appears inside it.

**`HR Manager` left that list in P4 (P4-KTD8).** HR now works a queue *inside*
the portal — everything managers have handed over, across leave, timesheets and
attendance requests — so an HR Manager with an active Employee record lands on
`/helixhr` like anybody else, with Desk one click away in the shell. Their
bookmarks still work. `HR User` and `System Manager` are unchanged and still
land in Desk, and an HR Manager with **no** Employee record has no portal
identity, so the rule does not move them either.

One consequence to handle **before** the deploy, not after: a
`default_workspace` pinned on a User overrides the resolved landing page, by
Frappe's own precedence, so an HR Manager who had pinned a workspace keeps
landing in Desk and reads it as the feature not working.
`preflight.check_portal_landing` already FAILs and names every portal user with
a pin — the set it reports simply grows to include HR Managers. Clear the pin
(Desk → User → Default Workspace) and `bench --site <site> clear-cache`. It is
a release step, not code.

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
- **Request categories and their routing, message text, leave types, holiday
  lists, and shift types — from the portal itself**, at `/settings` (HR
  Manager and System Manager only; nothing here is reachable from Desk-only
  roles, and nothing here is reachable from Desk at all any more for
  categories or message text — see the two subsections below). Everything
  else in this list is still a Desk record.
- **Leave types, holiday lists, allocations, approvers** — stock HRMS. The
  portal reads whatever HRMS says. `/settings` edits leave types, holiday
  lists and shift types through the same three named field sets below, on
  top of the real doctype — a Desk edit and a portal edit are the same record,
  never two stores to keep in sync.
- **Which leave types HR decides** — tick **HR Approves** on a Leave Type,
  either in Desk or from `/settings → Leave types`. A request for that type is
  filed straight into the HR queue: the manager never sees it, and the
  employee reads "Waiting for HR" from the moment they send it (P4-R7). Untick
  it and the next request goes to the manager again; requests already in
  flight keep the stage they were filed with.
- **HR reply text** — the `hr_note` field on a request. Changing it notifies
  the employee and puts the request back in their queue.
- **The HR contact address** shown to a signed-in user with no Employee record
  — `bench --site <site> set-config helixhr_hr_contact hr@example.com`.

Approving leave or a timesheet in Desk works too, and the portal reflects it —
the portal's approval path exists for convenience, not as the only route. What
Desk *cannot* do is bypass the rules: three `before_submit` hooks refuse a
submit by the requester themselves, and refuse a manager submitting a leave
request that is already with HR, whichever route the submit arrives on.

### Request categories moved off `HR Request.category`'s Select options (P5-U1, P5-U13)

A request category is now its own record, `HelixHR Request Category`, edited
at `/settings → Categories` — not a Select option on `HR Request` any more.
Each category names the role its requests route to (`HR Manager` or
`IT Team` today — nothing else is accepted, on save or on the portal's own
picker), an SLA in days (carried on the record; no scheduler acts on it yet),
and whether it is offered to employees at all. Deactivating a category hides
it from the picker without touching any request already filed under it.

Re-routing a category only changes where the **next** request goes.
`HR Request.routed_to_role` is stamped once, at insert, from the category's
route at that moment — re-pointing `Payroll Question` from `HR Manager` to
`IT Team` does not hand IT a single request filed before the change.

### HR-editable message text is a fixed token contract, never Jinja (P5-U13, P5-KTD11)

Two messages the portal sends are editable at `/settings → Message text`:
who is emailed when a request arrives, and what the employee is told when its
status changes. Each is stored in `HelixHR Message Template` as plain text —
rendered by simple `{token}` substitution (`helixhr.utils.render_tokens`),
**never** by `frappe.render_template`. A body containing `{{ frappe.get_doc(...) }}`
comes back as that literal string; nothing HR types is ever executed. An
unrecognized `{token}` is left as-is rather than rendered empty, so a typo
never blanks the sentence around it.

The token contract, one row per message (`helixhr.utils.TEMPLATE_TOKENS` is
the source of truth — this table is a copy of it, kept in sync by hand):

| `template_key`             | Tokens                                       | Sent when |
|-----------------------------|-----------------------------------------------|-----------|
| `request_arrival`           | `{category}`, `{subject}`, `{portal_url}`     | A request is filed, to the routed role. |
| `request_status_changed`    | `{category}`, `{subject}`, `{state}`, `{reason}` | The status changes, to the employee. |

`Notification` and `Email Template` — the two doctypes Frappe itself renders
through unrestricted Jinja — are deliberately untouched by this feature and
stay System-Manager-only. The birthday and work-anniversary reminders
described below still go through that older `Email Template` path; they were
not moved onto this token system (it would be a real behavior change, since
that template uses loops and conditionals over a variable number of people,
which flat substitution cannot express).

### Leave type, holiday list and shift type: a named field set, not a Desk reskin (P5-U13, P5-KTD12)

`/settings` deliberately shows only these fields per area — not every field
the underlying HRMS doctype has:

- **Leave type** — name, maximum days allowed, is carry-forward, is leave
  without pay, whether HR approves it.
- **Holiday list** — name, from date, to date, weekly off day, and the
  holiday rows.
- **Shift type** — name, start time, end time, and the check-in/check-out
  window (minutes before/after the shift).

Every save still runs through `doc.save()`, so HRMS's own `validate()` always
runs — a value HRMS itself would reject is rejected here too. A field outside
the named set posted to the save method is silently ignored, never written.

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

## The birthday and work-anniversary emails HR words (P4-U6)

HRMS sends both of these already, from a subject, header and Jinja file that
are hardcoded in HRMS Python. There is no record to edit, so changing the copy
means editing HRMS -- which the next `bench update` overwrites. HelixHR adds a
second sender that reads an **Email Template** instead, so HR owns the words.

### The switch: two pickers on HR Settings

**HR Settings -> Reminders** now carries two extra fields:

| Field | What it does |
|---|---|
| HelixHR Birthday Template | Empty: HelixHR sends no birthday email. Set: it sends that template, every morning, to everyone in the celebrating person's company. |
| HelixHR Work Anniversary Template | The same, for work anniversaries. |

Two templates ship as starting points -- **HelixHR Birthday Reminder** and
**HelixHR Work Anniversary Reminder** -- created once, on install or on the
first `bench migrate`, and never overwritten afterwards. Edit them in Desk
(**Email Template**); the edit survives every later deploy. Neither is picked
for you: switching the emails on is HR's decision, so a site that upgrades
does not start emailing anybody.

**Only one sender per event.** Frappe merges scheduler jobs across apps and
offers no way to remove HRMS's, so HRMS's own reminder keeps going out for as
long as its checkbox is ticked. Picking a HelixHR template while the matching
HRMS checkbox is still on is therefore refused on save:

> HRMS and HelixHR would both send the birthday email: untick 'Birthdays' in
> HR Settings > Reminders, or clear 'HelixHR Birthday Template'.

`helixhr.preflight.run` FAILs on the same contradiction, for the routes that
never reach a save (a fixture import, a raw write), and FAILs when a picked
template has since been deleted -- the job logs that and sends nothing, so the
event would otherwise go quiet with no other sign. Neither sender on for an
event is a WARN, not a FAIL: a site may not want the email at all.

Both senders need a **default outgoing Email Account** (preflight WARNs
without one), the same one the HR-queue notifications use.

### What the template can read

This is the whole contract. A template that reads anything else is reading
something the app does not promise to keep:

| Variable | What it is |
|---|---|
| `persons` | The people celebrating. Each has `name`, `first_name`, `image_url` (absolute, may be empty), and on an anniversary `years` -- the number completed. |
| `names` | Their names as one string: `Ada, Grace & Jim`. |
| `count` | How many of them there are. |
| `company` | The celebrating people's company. |
| `logo_url` | That company's logo, absolute. Empty when the Company has none, so guard on it: `{% if logo_url %}`. |
| `date` | Today, formatted for the site. |
| `portal_url` | Absolute link to the portal. |

Both the subject and the body are Jinja, and Email Template caps the subject
at 140 characters -- template markup included.

### Preview one before you switch it on

From `bench --site <site> console`, with a real Employee name:

```python
# A console session has no language set, and Frappe's own email footer
# rendering raises UnboundLocalError without one. Harmless, and only here.
frappe.local.lang = "en"

from helixhr.reminders import _context

name = "<EMPLOYEE>"           # an Employee record's name, e.g. HR-EMP-00002
company = frappe.db.get_value("Employee", name, "company")
person = frappe.get_all(
    "Employee",
    filters={"name": name},
    fields=["employee_name as name", "image", "date_of_joining"],
)[0]
mail = frappe.get_doc("Email Template", "HelixHR Birthday Reminder").get_formatted_email(
    _context([person], company, "birthday")
)
frappe.sendmail(recipients=["you@example.com"], subject=mail["subject"], message=mail["message"], now=True)
```

`now=True` sends it instead of queueing it, so a mistake in the markup shows
up in your own inbox rather than in everybody's tomorrow morning.

### Two things about the recipients

Who is celebrating, and who hears about it, is HRMS's own answer -- HelixHR
imports those helpers rather than re-implementing them, so the email and
Home's "this month" card can never disagree about who is eligible. Two
consequences follow, and both are HRMS's behaviour rather than a choice made
here:

- **A personal address can receive it.** For an employee with no linked User
  and no company email, HRMS falls back to `personal_email`. So a branded
  company email can arrive in a personal inbox. Clear `personal_email`, or
  fill in `company_email`, for anyone that is not wanted for.
- **Everyone active in the company is a recipient**, minus the people
  celebrating. When two or more share a day, each of them also gets one email
  about the others.

## Before the migrate that ships the four approval outcomes (P4-U1)

Three things change at this migrate that an operator has to know about: a state
gets renamed in the database, attendance requests stop needing HR, and HR
Managers start landing in the portal. None of them needs a hand-written SQL
statement, and one of them must never get one.

### The `Sent Back` rename is automatic, and one-way

*Rejected* used to mean "the approver sent this back, edit it and send it
again" on both the Timesheet and the Attendance Request workflows. P4 gives
*Rejected* its literal meaning — a final no — and moves the recoverable one to
a new state, **Sent Back**. Every row a site already holds in the old state has
the old meaning, so `patches/v1_0/rename_sent_back_state.py` moves it:

```
UPDATE  ... SET workflow_state = 'Sent Back'
WHERE   workflow_state = 'Rejected' AND docstatus = 0
```

per doctype, guarded on this app's Workflow existing on the site, and
idempotent — a second run matches nothing. Only docstatus-0 rows are touched;
`Approved` is the only submitted state on either workflow, so a docstatus-1 row
was never a send-back, and a docstatus-2 row is cancelled history.

Nothing to do beforehand. Two things to know:

- **It is one-way.** Rolling the *app* back would leave rows in a state the old
  fixture does not know about, where the employee is offered nothing at all,
  because the `Edit` transition now hangs off `Sent Back`. Rolling back means
  reversing the rename too, by hand and deliberately.
- **A migrate that dies part-way is fixed by running migrate again — never by
  editing rows.** Frappe's order is patches, then `sync_fixtures`
  (`frappe/migrate.py`), so the patch runs *before* the `Sent Back` Workflow
  State record exists. That is by design and is fine: it is a column update on
  the document table, not a Link validation, and the fixture creates the state
  moments later. But it means there is a window in which the site has rows in
  `Sent Back` and a Workflow that has never heard of it — which is what a
  migrate interrupted between the two looks like. `bench --site <site> migrate`
  again finishes the job (the patch is idempotent, the fixture import is not
  destructive). `helixhr.preflight.run`'s `Fixtures installed` line is the
  check: it FAILs on a missing `Sent Back` Workflow State or a missing
  `Send Back` / `Send to HR` Workflow Action Master, because a Workflow's state
  and action names are Links and fixture import runs with `ignore_links`, so a
  half-installed set leaves the Workflow itself looking fine.

### Requests already in flight

Both existing pending states survive the migrate, so nothing is stranded:

- One already in **Pending HR** completes under HR exactly as before — the
  state and HR's transitions off it still exist.
- One already in **Pending Manager** becomes single-step. The manager's next
  `Approve` submits it and writes the Attendance, where before it moved the
  request to Pending HR. Managers who worked the old flow will notice; nothing
  needs migrating.

Approving is now the manager's submit, so their DocShare on a Pending Manager
request carries `submit=1` where it used to carry only `write`. It is granted
only while the request is Pending Manager and only to the Active reports-to
user, which is the scope Timesheet has had since phase 2.

### The new Notification fixtures need an outgoing Email Account

HR is told that a request reached their queue by four fixture Notifications on
**channel Email** (`HelixHR Leave Sent To HR`, `HelixHR New Leave For HR`,
`HelixHR Timesheet Sent To HR`, `HelixHR Attendance Request Sent To HR`), and
they send from inside the save that escalates the request. `frappe.sendmail`
throws without a **default outgoing Email Account**, and because it throws
*inside* that save, the throw is the save's: on a site with no mail configured
a Send to HR does not merely go unannounced, it is refused at the moment the
manager presses it. Applying for an HR-approves leave type is refused the same
way, since the stage write raises the same notification.

`preflight.check_outgoing_email` therefore **FAILs**, not WARNs. Send to HR is
a new action that simply does not work without the account, which is different
from existing behaviour degrading — and swallowing the error instead would be
worse, because HR would never be told the request arrived. Configure the
account in Desk → Email Account before anyone escalates anything. The
celebration reminders need the same account.

### HR staff are the deliberate exception to self-scoping

Preflight carries a real tension here, and it is written down so nobody
"fixes" it later by making the two checks agree the other way.

`check_employee_user_permissions` FAILs when a linked employee has no User
Permission on their own Employee record: without one, that person can read
every employee on the site. It is the app's whole authorization boundary.

An **HR Manager must not have one.** A User Permission on Employee beats HR
Manager's own read permission on Leave Application, Timesheet and Attendance
Request, and it does so silently: `check_permission("read")` inside
`frappe.model.workflow.get_transitions` throws for every row that is not
theirs, so the Approvals page shows HR nothing but their own records. That reads
as an empty queue, not as a permission problem — which is the worst possible
failure shape for the person whose job is the queue.

So as of P4 the first check **exempts HR Manager logins**, through the shared
`preflight._hr_manager_users()` helper that `check_hr_manager_self_scope` uses
to find them. The two checks read the same list and want opposite answers about
it, on purpose:

| Check | About | Wants |
|---|---|---|
| `Employee User Permissions` | every linked employee **except** HR Manager logins | a self-scoping User Permission on each — FAIL without |
| `HR queue scoping` | HR Manager logins only | **no** User Permission on Employee — WARN with |

It is a WARN and not a FAIL on the second, because whether a particular HR
Manager login is meant to work the queue is HR's call. And it is easy to arrive
at by accident: an Employee record created with HR Settings' "Create User
Permission" ticked — the default — is exactly where the unwanted permission
comes from. If HR reports an empty Approvals page, this is the first thing to
check.


### The P4 migrate, as commands

The reasoning is above; this is the sequence. Run it on staging against a
restored production dump first — the rename is one-way, so a rehearsal is the
only cheap way to find out what it does to your data.

**Before.** Record the numbers; step 2 reconciles against them.

```bash
# rows the rename will move (docstatus 0 only -- see above)
bench --site <site> mariadb -e "SELECT workflow_state, docstatus, count(*) \
  FROM \`tabTimesheet\` WHERE workflow_state='Rejected' GROUP BY 1,2;"
bench --site <site> mariadb -e "SELECT workflow_state, docstatus, count(*) \
  FROM \`tabAttendance Request\` WHERE workflow_state='Rejected' GROUP BY 1,2;"

# the guard the patch relies on: both workflows exist and are active
bench --site <site> mariadb -e "SELECT name, is_active FROM \`tabWorkflow\` \
  WHERE name IN ('Timesheet Approval','Attendance Request Approval');"

# a default outgoing Email Account, without which Send to HR is refused
bench --site <site> mariadb -e "SELECT name FROM \`tabEmail Account\` \
  WHERE enable_outgoing=1 AND default_outgoing=1;"

# HR Managers who will keep landing in Desk, or who have no queue at all
bench --site <site> execute helixhr.preflight.check_portal_landing
bench --site <site> execute helixhr.preflight.check_hr_manager_self_scope

bench --site <site> backup --with-files   # the only recovery path
```

Do **not** hand-write an `UPDATE` on `workflow_state` beforehand. The patch is
the only sanctioned writer, and a hand-edit desyncs the counts above.

**After.** `bench migrate`, then:

```bash
# 1. nothing left in the legacy meaning
bench --site <site> mariadb -e "SELECT count(*) FROM \`tabTimesheet\` \
  WHERE workflow_state='Rejected' AND docstatus=0;"        # expect 0
bench --site <site> mariadb -e "SELECT count(*) FROM \`tabAttendance Request\` \
  WHERE workflow_state='Rejected' AND docstatus=0;"        # expect 0 at this moment;
# a later non-zero count is legitimate NEW terminal rejections -- check `modified`

# 2. reconciles with the before-counts
bench --site <site> mariadb -e "SELECT count(*) FROM \`tabTimesheet\` \
  WHERE workflow_state='Sent Back' AND docstatus=0;"
bench --site <site> mariadb -e "SELECT count(*) FROM \`tabAttendance Request\` \
  WHERE workflow_state='Sent Back' AND docstatus=0;"

# 3. no row in a state its workflow no longer defines
bench --site <site> mariadb -e "SELECT workflow_state, count(*) \
  FROM \`tabAttendance Request\` WHERE docstatus IN (0,1) GROUP BY 1 HAVING \
  workflow_state NOT IN ('Draft','Pending Manager','Pending HR','Approved','Sent Back','Rejected');"

# 4. the whole gate
bench --site <site> execute helixhr.preflight.run
```

A `Fixtures installed` FAIL with steps 1 and 2 clean is the interrupted-migrate
window described above: run `bench migrate` again, never edit rows.

**Watch for 48 hours.** Email Queue errors (`SELECT status, count(*) FROM
\`tabEmail Queue\` WHERE creation > DATE_SUB(NOW(), INTERVAL 1 HOUR) GROUP BY
1;` — steady state is `Sent` only), the celebration job in `tabScheduled Job
Log` (a failed import of an HRMS helper surfaces here, by design), and any
Pending Manager attendance request older than 48 hours with no manager action.

### "Rolling back" this release means rolling forward

There is no reverse patch, and a `git revert` plus `bench migrate` is **not** a
rollback:

- Rows already in `Sent Back` stay there. The old fixture has no such state, so
  the old `Edit` transition — which hangs off `Rejected` — no longer matches,
  and the employee is offered **nothing at all** on those rows.
- Rows that are genuinely, terminally `Rejected` under P4 would be turned back
  into send-backs by a naive reverse `UPDATE`, resurrecting decisions HR made.
  Any reverse has to be scoped to a cutoff and leave real rejections alone.
- The Custom DocPerm rows and the seeded Email Templates are not removed by
  running old code. Deltas are corrective, not reversible.

So: restore the pre-migrate backup to a **separate** site to inspect rows, and
reconcile the live site forward. Tell whoever approves the deploy this before
it goes out, not after.


## Before the migrate that ships the attendance workflow (P3-U5)

`Attendance Request Approval` (P3-KTD6) gives Attendance Request a
`workflow_state`, and Frappe backfills every row that already exists by
docstatus: drafts become `Draft`, submitted ones `Approved`, cancelled ones
keep a null state. Count the drafts first:

```bash
bench --site <site> execute frappe.db.count   --args '["Attendance Request", {"docstatus": 0}]'
```

Either have HR submit those in Desk before the migrate, or afterwards move
each of them along with the workflow's HR-only `Approve` action, which since
P4-U1 takes an old draft straight to `Approved`. Deciders need the **HR
Manager** role — HR User is refused on purpose. Employees never submit an
attendance request themselves.

`events._is_hr` is the exact rule behind "HR": `Administrator`, or a user
holding **HR Manager** or **System Manager**. HR User is not in that set, so an
HR User can open a request in Desk and cannot decide it, edit it out of a
pending state or delete it. Grant HR Manager to whoever is meant to decide.

**Never cancel an approved request to correct a day.** HRMS writes the
Attendance rows at the approver's submit and cancels them outright on cancel — including
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
