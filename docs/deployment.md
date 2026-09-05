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
