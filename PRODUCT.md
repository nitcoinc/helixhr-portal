# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

<!-- Stack omitted: the codebase already answers it. Frappe v16 app (Python)
     with a Vue 3 + frappe-ui + Tailwind frontend built by Vite. -->

## Users

- **Employee** (primary): one of 100–200 staff across the company's India and USA
  offices. Any active Employee record with the Employee Self Service role and a
  User Permission on their own Employee record. Confirmed this session: they open
  the portal **daily**, and the job they come to do is **act on whatever is
  waiting for them** — a timesheet sent back, an HR reply, something to clear —
  rather than browse their numbers. Mobile-first; phones are the primary device.
- **Manager**: an Employee who is `reports_to` or `leave_approver` for others.
  Same portal as everyone, plus one Approvals page.
- **HR Manager**: works HR Requests, configures workflows, and is the approval
  fallback. Works in Frappe Desk, not in this portal.

## Product Purpose

A small employee portal inside Frappe covering leave, attendance, timesheets,
profile, requests, documents and notifications on a few plain screens, plus one
approvals page for managers.

Frappe HR Desk is an administration tool: employees have to learn DocTypes,
menus and workflow words to do simple things. Frappe's stock `/hrms` mobile app
lacks timesheets, requests and documents, and its look cannot be changed.

Success is an employee finishing a routine task without learning any Frappe
vocabulary, and one developer being able to keep the whole thing running.

## Primary surface

The home screen (`/helixhr`) is an **action queue built on the working week**,
not a summary of numbers. Its job is to show what is waiting on the employee
and let them clear it; a leave balance and a sent-back timesheet are not
equal-weight facts, and the screen must not present them as such. Reference
figures (balances, attendance counts) are demoted to a rail because they are
what someone consults, not what they came to do. When nothing is waiting, the
screen says so and names the outstanding weekly obligation instead of going
blank.

## Positioning

Frappe HR stays the only source of truth and the only place for HR
administration. The portal adds screens, a handful of thin whitelisted methods
and three document event hooks — it never becomes a second system of record.
Every server call runs as the logged-in Frappe user, so Frappe's own permissions
are the security model and the browser never holds an API key or token.

## Constraints

- Never modify Frappe / ERPNext / HRMS core code.
- Reuse before build: existing `hrms.api` methods, Frappe Workflow, Notification,
  Notification Log, DocShare, Property Setter and User Permission are used as-is.
- Employees may edit only: personal mobile, personal email, current address,
  permanent address, emergency contact name, relation and phone. Every other
  Employee field is read-only, enforced by permlevel, not by the UI.
- Sign-in is Microsoft Entra ID via Frappe's Office 365 Social Login Key. Self
  sign-up is off; no custom auth code.
- A logged-in user with no active Employee record gets a friendly "not linked"
  page, never an error.
- Mobile-first, and must work at 360px with no horizontal scroll.
- WCAG AA: contrast measured, not assumed; 44px touch targets under coarse
  pointers. (The design system's original contrast figures were estimates and
  several were wrong — see docs/design-system.md.)

## Terminology

Plain words only; no Frappe vocabulary reaches the screen. "Ask for leave", not
"Create Leave Application". "Waiting for [manager]", not "Pending Approval" or
"docstatus". "Sent back", not "Rejected". "Edit and resubmit", not "Amend". Every
empty state names the next action. The full mapping is the copy table in
docs/design-system.md.

## Out of scope

Phase 1 is employee-first plus one approvals page. No team views, no HR screens,
no AI assistant, no dark mode. HR configuration, payroll, leave policy, shifts,
attendance devices, reports, recruitment and accounting all stay in Frappe Desk.

## Open decisions

- The HR contact address on the not-linked page is a placeholder
  (`hr@nitcoinc.com`); the real address is unconfirmed.
- The Entra ID sign-in round trip and a Lighthouse run have not been verified on
  a staging host — no such environment exists in the current setup.
