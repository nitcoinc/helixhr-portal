# HelixHR Employee Portal — Project Brief (v2)

> Status: **Ready for spec.** Research done, all decisions made, no open questions (2 Sep 2026).
> v1 of this brief is kept at `docs/archive/HelixHR_Portal-Project-Brief-v1.md`.
> Supporting docs: `docs/frappe-hr-research.md` (what Frappe gives us for free) and `docs/ai-assistant-phase2.md` (AI assistant, not phase 1).

---

## 1. What we are building

A simple, modern web portal called **HelixHR Employee Portal** for a company of 100–200 people.

- Employees do their everyday HR tasks here instead of Frappe Desk.
- Managers approve leave and timesheets here.
- HR and admins keep using Frappe HR Desk. Nothing about administration moves.
- **Frappe HR stays the only source of truth.** The portal is a thin, friendly screen on top of it.

Guiding rule: **basic and simple first. Add later.** One clear action per screen. Plain words. No Frappe terms (DocType, Desk, submit/cancel) shown to employees.

---

## 2. Problem

Frappe HR Desk is an admin tool. It is too busy for an employee who only wants to apply for leave or fill a timesheet. Frappe ships a small employee mobile app (`/hrms`), but it has no timesheets, no HR requests, no documents, and its look cannot be changed. We need one portal that covers our daily tasks and looks like ours.

---

## 3. Users

| User | Phase 1 | Where |
|---|---|---|
| Employee | Full portal | HelixHR Portal |
| Manager (reports_to / approver) | Employee features + one **Approvals** page (leave, timesheets) | HelixHR Portal |
| HR Manager / HR Admin / IT / Finance | Unchanged | Frappe HR Desk |

---

## 4. Decisions made (do not reopen without a reason)

| # | Topic | Decision | Why |
|---|---|---|---|
| D1 | Where the portal lives | A small **custom Frappe app `helixhr`** installed on the same site. The web app is served at `/helixhr` from the same domain. | Same-origin means the normal Frappe login cookie and CSRF token just work. No CORS, no tokens in the browser, one deployment. This is how Frappe's own apps (HR mobile, Helpdesk, CRM) are built. |
| D2 | Frontend stack | **Vue 3 + frappe-ui + Tailwind + Vite**, scaffolded from `frappe-ui-starter` inside `apps/helixhr/frontend`. | Official Frappe toolkit. Built-in data helpers (`createResource`, `useDoc`). Easy for any Frappe dev to maintain. |
| D3 | Login | **Frappe Social Login Key with Office 365 / Microsoft Entra ID** (built into Frappe). No custom auth code. Turn **off** self sign-up in Website Settings. Optionally hide the password login form. | Zero code. Frappe matches the Microsoft email to an existing Frappe User. Unknown emails get nothing. Employees never see API keys. |
| D4 | Who can see what | **Frappe roles + User Permissions** decide. Every employee user has the `Employee Self Service` role and a User Permission to their own Employee record (Frappe creates this by default). The frontend never decides permissions. | Already exists. Already tested by thousands of sites. |
| D5 | API boundary | **Reuse first, add little.** (a) Reuse `hrms.api.*` whitelisted methods (leave balance, leave list, attendance calendar, holidays, notifications). (b) Use standard `/api/method/frappe.client.*` and `/api/resource` for reads and for creating Leave Application, Timesheet, HR Request, where the ESS role already limits access. (c) Add a **small `helixhr.api`** only for: dashboard summary, safe profile update, document links, manager approval list. | Fewest new endpoints. Frappe permission checks run on every call either way. |
| D6 | Profile editing | Employees edit **only** an allow-list of fields through `helixhr.api.update_my_profile`. **Also** raise sensitive Employee fields to permission level 1 via Property Setters so the ESS role cannot write them by any route. | The ESS role has *write* on Employee. Without the permlevel lock, a curious employee could change their own department, bank account or joining date through the plain REST API. See section 7. |
| D7 | Timesheets | Use **ERPNext Timesheet** (installed). Weekly view in portal. Approval = a **Frappe Workflow on Timesheet** (Draft, Pending Approval, Approved / Rejected), configured by HR, no code. Allowed projects = ERPNext Project **Users** table / User Permission on Project. | Existing DocType, existing workflow engine, existing project access rules. |
| D8 | Employee requests | One **small custom DocType `HR Request`**: category, subject, details, attachment, status (Open / In Progress / Done / Rejected), HR note. HR works it in Desk. Categories phase 1: HR Letter, IT / Asset, Payroll Question, Other. Travel uses Frappe HR's existing **Travel Request** later. | Not a ticketing system. One form, one list. |
| D9 | Managers | Phase 1 includes **one Approvals page**: pending Leave Applications and Timesheets where the logged-in user is the approver. Approve / Reject with a comment. Nothing else for managers in phase 1. | Requested. Small. Uses Frappe workflow actions, no new rules. |
| D10 | Notifications | **In-portal only** in phase 1. Reuse Frappe **Notification Log** / HR's **PWA Notification** for status changes. Email later by turning on Frappe **Notification** documents (config, no code). Teams is phase 2. | Zero code for email later. |
| D11 | Documents | A **`HelixHR Settings`** single DocType holds a table of links (title, URL, company, role). Portal shows links. SharePoint keeps enforcing who can open them. | Links only. No file storage in the portal. |
| D12 | Versions | Frappe Framework **v16**, Frappe HR **v16**, ERPNext **v16**. **Confirmed by the owner (2 Sep 2026).** | Latest stable. New "Espresso" Desk UI for HR staff. |
| D13 | AI Assistant | **Phase 2. Separate document.** Phase 1 only keeps the API small and typed so the assistant can reuse it later. | Not a priority. See `docs/ai-assistant-phase2.md`. |
| D14 | User accounts | Every employee has a Frappe User whose email **equals** their Microsoft sign-in email. HR fixes any mismatch before go-live. | Frappe Social Login matches by email. No mapping code needed. |
| D15 | Countries | **One Frappe site, two Company records** (example: US Corp, India Pvt Ltd). Holiday lists, leave policies and payroll differ per Company. | Standard Frappe multi-company. Nothing for the portal to do. |
| D16 | Timesheet approval | Create a **Workflow on Timesheet**. Approver = the employee's `reports_to` manager. | Same manager approves leave and time. One Approvals page. |
| D17 | HR Request queue | **One queue** for both countries, worked by the **HR Manager** role. | 100 to 200 people. One queue is enough. Split later if needed. |
| D18 | Cancelling approved leave | Employees **cannot** cancel approved leave themselves. They open an HR Request. Draft or pending leave can be withdrawn by the employee. | Keeps balances and payroll safe. Frappe permission stays simple. |
| D19 | Editable profile fields | The allow-list in section 6 (Profile) is **final** for phase 1. | Confirmed by the owner. |

---

## 5. Phase 1 scope

### In

- Microsoft Entra ID login (Frappe Social Login Key)
- **Home dashboard**: name, designation, department, manager, location, leave balances, this month's attendance summary, this week's timesheet status, my pending items, recent activity, quick actions
- **Profile**: view; edit allow-listed personal fields
- **Leave**: balances, history, apply, withdraw draft or pending leave (approved leave goes through an HR Request), status and rejection reason
- **Attendance**: month calendar, daily check-in / check-out, summary, exceptions (absent, late, half day, missing)
- **Timesheets**: current week, add rows (project, task, hours, note), save draft, submit, history, status
- **Requests**: create HR Request, see my requests and status
- **Documents**: list of permitted links
- **Notifications**: in-portal list and unread count
- **Approvals** (managers): pending leave and timesheets, approve / reject
- Responsive (phone + desktop), accessible (keyboard, contrast, labels)

### Phase 2 (not now)

AI HR Assistant · Team attendance and leave calendar · Employee directory · Travel Request · Expense claims · Salary slips · Onboarding · Email and Teams notifications · Performance

### Out of scope (stays in Frappe Desk, forever or for now)

HR configuration · Payroll · Salary structures · Leave policy · Shifts · Attendance devices and imports · Users and roles · Workflow design · Reports · Recruitment · Accounting · Any direct DB access from the browser · Any Frappe API key in the browser

---

## 6. Feature requirements (short form)

### Login

- Click "Sign in with Microsoft". Land on the dashboard.
- Frappe matches by email. If no Frappe User exists, show a plain message: "Your account is not set up. Contact HR." No self sign-up.
- Session = normal Frappe session cookie. Logout ends it.
- Users with no active Employee record see a friendly "not linked" page, not an error.

### Dashboard

- One screen, no scrolling needed on desktop for the key numbers.
- Every number links to its page.
- Quick actions: Apply Leave, Fill Timesheet, New Request.

### Profile

- Read: name, employee ID, designation, department, company, location, reporting manager, date of joining, work email.
- **Editable by employee** (allow-list, confirmed by HR): personal mobile, personal email, current address, permanent address, emergency contact name, relation, phone.
- **Never editable by employee** (permlevel 1): company, department, designation, branch, reports_to, employment type, status, date of joining, date of birth, gender, salary mode, bank name, bank account, PAN / SSN and any tax IDs, passport, holiday list, grade, shift.
- Changes to locked fields = "Ask HR" link that opens a pre-filled HR Request.

### Leave

- Frappe validates everything: allocation, policy, holidays, overlaps, workflow, approver. Portal shows Frappe's error message in plain words.
- Portal must **not** compute leave validity itself. It may show balances read from Frappe.

### Attendance

- Read-only. Data from Attendance and Employee Checkin.
- Exceptions are Frappe statuses shown with a colour and a word, nothing more.

### Timesheets

- Week view (Mon to Sun, company-configurable start day later).
- Only projects where the employee is in the Project Users table (or has a User Permission) appear.
- Draft, Submit, then status from Workflow. Rejected timesheets can be edited and re-submitted (Frappe Amend, hidden behind "Edit and resubmit").

### Requests

- Form: category, subject, details, optional file.
- List: my requests with status and HR note.
- HR gets a Frappe Notification (config) on new request.

### Approvals (manager)

- List of Leave Applications and Timesheets pending for me.
- Approve / Reject with comment. Uses the Frappe workflow action. No bulk actions in phase 1.

### Notifications

- Bell icon with unread count. List of recent items: leave / timesheet / request status changes.

---

## 7. Security must-haves (non-negotiable)

1. Same-origin app, session cookie, CSRF token on every write. No API keys, no tokens in localStorage.
2. Self sign-up **off**. Only pre-created Frappe Users can log in.
3. Every server call runs as the logged-in user. `helixhr.api` methods never use `ignore_permissions` except the profile-update method, and that method (a) resolves the Employee **from the session user**, never from a parameter, and (b) only writes allow-listed fields.
4. Sensitive Employee fields locked at **permlevel 1** via Property Setters. Test: an employee calling `/api/resource/Employee/<own id>` with `department` in the body must be rejected.
5. Manager approvals use Frappe workflow transitions, so Frappe checks the approver, not the portal.
6. Files: uploads go through Frappe's file API and are attached to the employee's own document. `is_private = 1`.
7. Audit: Frappe's Version / Activity log stays on for Employee, Leave Application, Timesheet, HR Request.
8. PII in two countries (India, USA): no new data store; nothing leaves the Frappe site.
9. Frappe core is never modified. Only the `helixhr` app, Property Setters, Custom Fields, Workflows, Notifications.

---

## 8. UI principles (for the design pass)

- Mobile first. The phone view is the main view. Desktop widens it.
- One primary button per screen. Secondary actions are text links.
- Plain words: "Ask for leave", not "Create Leave Application". "Waiting for manager", not "Pending / docstatus 1".
- Show Frappe error messages, but rewrite the top 10 common ones into plain sentences.
- Empty states tell the user what to do next.
- WCAG AA contrast, visible focus, labels on every input, works with keyboard.
- Design system chosen with `/ui-ux-pro-max` before any markup; screens generated with `/hallmark`; polished with `/impeccable`.

---

## 9. Open questions

None. All questions were answered on 2 Sep 2026 and moved into section 4 as decisions D12 and D14 to D19.

---

## 10. Examples

**Apply for leave.** Employee opens Leave, picks Casual Leave and 4 Sep, writes a reason, taps Ask for leave. Portal creates a Leave Application in Frappe. Frappe validates and routes it to the approver. Employee sees "Waiting for [manager]".

**Weekly timesheet.** Employee opens This Week, adds 8 h on Project A / Task X, saves. On Friday taps Submit. Workflow moves it to Pending Approval. Manager sees it on Approvals and approves. Employee gets an in-portal notification.

**Profile update.** Employee changes mobile number. Portal calls `helixhr.api.update_my_profile`. Saved and shown. Employee tries to change department: field is read-only with an "Ask HR" link that opens an HR Request.

**Manager approval.** Manager opens Approvals, sees two leave requests and one timesheet. Approves one leave, rejects one with a comment. Frappe workflow runs; employees are notified.

---

## 11. Next step

Run `/to-spec` on this brief. Research and all decisions are done, so the long interview in `/grill-with-docs` is not needed. Then `/to-tickets`. Suggested first tracer bullet: **login, then dashboard with real leave balance**.
