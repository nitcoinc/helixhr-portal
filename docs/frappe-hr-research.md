# Frappe HR research — what we get for free

> Date: 2 Sep 2026. Target: Frappe Framework / Frappe HR / ERPNext **v16** (released 12 Jan 2026). Confirmed as the site version.
> Purpose: stop us building things Frappe already has.

## 1. The existing employee mobile app (`/hrms`)

Frappe HR ships a PWA at `/hrms` built with Vue 3, Ionic, Tailwind and frappe-ui.

| Has | Does not have |
|---|---|
| Leave (balances, apply, list), Attendance calendar, Attendance Request, Shift Request, Expense Claims, Employee Advances, Salary Slips, push notifications, approvals for leave / expense / shift | Timesheets, HR requests, document links, profile editing, any theming or branding (community has asked; not supported) |

**Use:** copy its ideas and reuse its backend methods. Do not fork it.

## 2. Backend methods we can call today (`hrms.api`)

All are `@frappe.whitelist()` and run as the logged-in user. Most resolve "current employee" from the session. Key ones for phase 1:

| Method | Use in portal |
|---|---|
| `get_current_user_info`, `get_current_employee_info` | Dashboard header, profile read |
| `get_leave_balance_map` | Leave balances |
| `get_leave_applications(employee, approver_id, for_approval, limit)` | My leave list; manager approvals list (`for_approval=True`) |
| `get_leave_types(employee, date)`, `get_leave_approval_details(employee)`, `get_holidays_for_employee(employee)` | Apply-leave form |
| `get_attendance_calendar_events(from_date, to_date)` | Attendance month view |
| `get_shifts` | Shift info on dashboard (optional) |
| `get_unread_notifications_count`, `mark_all_notifications_as_read` | Bell icon |
| `get_workflow(doctype)`, `get_doctype_states(doctype)` | Show status names / colours from the real workflow |
| `upload_base64_file`, `get_attachments`, `delete_attachment` | Attachments on requests |
| `get_permitted_fields_for_write(doctype)` | Sanity check of what a role may write |

Timesheets have **no** `hrms.api` method. Use standard `frappe.client.*` / `/api/resource/Timesheet` plus one small `helixhr.api.get_my_week` helper.

## 3. Permissions model

- Frappe HR creates a custom role **Employee Self Service (ESS)**. Every employee user gets it.
- When HR creates an Employee with "Create User Permission" ticked (default), Frappe adds a **User Permission**: this user may only see their own Employee record. Leave Application, Timesheet, etc. then auto-filter to that employee.
- ESS role permissions (from `hrms/setup.py`):
  - **Employee: read, write** (own record only)
  - Leave Application, Attendance Request, Compensatory Leave Request, Expense Claim, Employee Advance: read, write, create, delete
  - **Timesheet**, Shift Request, Training Feedback: read, write, create, delete, **submit, cancel, amend**
  - Travel Request, Employee Grievance, Employee Referral: read, write, create, delete
  - Holiday List, Company, Leave Type, Salary Slip, Employee Checkin: read

### Security gotcha (must fix in phase 1)

ESS has **write on Employee**. Field-level lock is not set by default. An employee can PUT `department`, `reports_to`, `bank_ac_no`, `date_of_joining`, etc. on their own record through `/api/resource/Employee/<id>`. The portal UI hiding a field does not stop this.

**Fix, no core change:** Property Setter, set `permlevel = 1` on every sensitive Employee field. ESS keeps permlevel 0 (personal contact fields). HR Manager gets permlevel 1. Add an automated test that a PUT with `department` fails as ESS.

## 4. Microsoft Entra ID login

Built into Frappe: **Social Login Key**, provider **Office 365**.

- Azure: register a Web app, redirect URI `https://<site>/api/method/frappe.integrations.oauth2_logins.login_via_office365`, add the **email** optional claim, create a client secret.
- Frappe: Desk, Social Login Key, Office 365, Client ID + Secret, enable.
- Matching: by **email**. If a Frappe User with that email exists, the user is logged in. If not, Frappe creates a **Website User**, unless **Website Settings: Disable Signup** is on. **Turn it on.**
- Result: ordinary Frappe session cookie. The portal needs no auth code at all.
- Optional hardening: hide the password form (Website Settings, login page), enforce Entra MFA / Conditional Access in Azure.

Alternative if IT insists on SAML: `castlecraft/microsoft_integration` app. Not needed for phase 1.

## 5. Frontend stack (the Frappe way)

- **frappe-ui**: Vue 3 component library + data helpers (`createResource`, `useDoc`, `useList`, `useCall`). Tailwind.
- **frappe-ui-starter** (`npx degit netchampfaris/frappe-ui-starter frontend` inside the app): Vue 3, Vue Router, Tailwind, Vite. Dev server on 8080 proxies to Frappe on 8000. Build output goes to the app's `www/<name>` folder; `hooks.py` `website_route_rules` maps `/helixhr/*` to it.
- Session: same origin, so the cookie is sent automatically. CSRF: production `index.html` injects `window.csrf_token` from boot; frappe-ui sends `X-Frappe-CSRF-Token`. Dev: **not** `ignore_csrf` -- that disables CSRF for every mutation on the site and `helixhr.preflight.check_csrf` FAILs a site that has it. The shell renders a real token in dev too; see `frontend/README.md`. (P2-U9)
- Frappe's own products (HR mobile, Helpdesk, CRM, Insights) use this exact pattern.

## 6. Pieces we reuse instead of building

| Need | Frappe piece |
|---|---|
| Timesheet entry, projects, tasks | ERPNext **Timesheet**, **Project** (Users table restricts who can book), **Task** |
| Timesheet approval | **Workflow** on Timesheet (Draft, Pending Approval, Approved / Rejected). Config, no code |
| Leave approval | Built-in `leave_approver` + Leave Application workflow |
| In-portal notifications | **Notification Log** (framework) and HR's **PWA Notification** |
| Email notifications later | **Notification** DocType (event, recipients, template). Config |
| Travel requests | HR **Travel Request** (phase 2) |
| Expense claims, salary slips | HR **Expense Claim**, **Salary Slip** (phase 2) |
| Grievances | HR **Employee Grievance** (if ever needed) |
| Audit trail | Framework **Version** log, Activity |
| Field locking | **Property Setter** (permlevel), **Custom Field** |
| Two countries | Two **Company** records, per-company Holiday List, Leave Policy, Payroll |

## 7. What is actually new in `helixhr`

Keep this list short. If it grows, ask why.

1. `frontend/`: the Vue app.
2. `helixhr/api.py`: about 5 whitelisted methods: `get_dashboard`, `update_my_profile` (allow-list), `get_my_week` (timesheet), `get_document_links`, `get_my_approvals`.
3. DocType **HR Request** (+ one Workflow or plain status Select).
4. Single DocType **HelixHR Settings** (document links table, editable-fields allow-list).
5. Fixtures: Property Setters for Employee permlevel, Notification for new HR Request.

## 8. Frappe HR v16 notes (Jan 2026)

- New Desk UI ("Espresso"), persistent sidebar, about 2x faster. Good for HR staff; irrelevant to the portal.
- Multiple Holiday Lists per employee / company, half-day holidays, leave adjustments, overtime from check-ins, payroll corrections, flexible benefits.
- No new employee-portal or theming feature. Our reason to build stands.

## Sources

- Frappe HR repo: https://github.com/frappe/hrms (`hrms/api/__init__.py`, `hrms/setup.py`, `hrms/hooks.py`, `frontend/`)
- Frappe HR v16: https://frappe.io/hr/version-16 · https://frappe.io/releases/version-16 · https://discuss.frappe.io/t/frappe-erpnext-and-frappe-hr-version-16-release/159053
- Social logins (Office 365): https://docs.frappe.io/framework/user/en/guides/deployment/how-to-enable-social-logins
- Microsoft integration app (SAML alternative): https://github.com/castlecraft/microsoft_integration
- frappe-ui docs: https://ui.frappe.io/docs/getting-started · starter: https://github.com/netchampfaris/frappe-ui-starter · vite plugin: https://github.com/frappe/frappe-ui/blob/main/vite/README.md
- doppio starter (alt scaffold): https://github.com/NagariaHussain/doppio_frappeui_starter · CSRF issue: https://github.com/NagariaHussain/doppio/issues/39
- PWA theming request: https://github.com/frappe/hrms/issues/3942 · customising Frappe Vue apps: https://discuss.frappe.io/t/how-to-customize-frontend-vue-app-i-e-hrms-employee-self-service-helpdesk/121181
- ESS role issues: https://discuss.frappe.io/t/employee-self-service-users-unable-to-access-the-hr-section/109698 · https://discuss.frappe.io/t/cant-change-the-permissions-for-employee-self-service-role/90839 · https://github.com/frappe/hrms/issues/1677
- Employee doc: https://docs.frappe.io/hr/employee
