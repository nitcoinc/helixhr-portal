# Pre-Spec Project Brief — HelixHR Employee Portal

> Use this as the starting document before running `/grill-with-docs`.
>
> Keep refinement focused on product behavior, permissions, security, integration boundaries, and scope. Do not start architecture or implementation planning yet.

---

## 1. Idea

**What do you want to build?**

Build a modern, simple employee-facing web application called **HelixHR Employee Portal** using **Frappe HR as the backend HR system**.

Employees should use the custom portal instead of the standard Frappe Desk interface for their normal HR activities.

Frappe HR will remain the source of truth for employee records, leave, attendance, timesheets, approvals, workflows, permissions, and other HR data.

HR administrators and system administrators will continue using the standard Frappe HR interface for administration and configuration.

The employee portal should eventually include an **AI HR Assistant** that lets employees perform supported HR activities using natural language.

---

## 2. Problem

**What problem are you trying to solve?**

Frappe HR provides strong HR functionality and workflows, but its standard interface is primarily an administrative/business application and is more complex than necessary for everyday employee self-service.

Employees should not need to understand Frappe modules, DocTypes, forms, menus, or backend terminology to perform simple HR activities.

The goal is to provide employees with a clean and focused interface for common actions such as:

- viewing their profile,
- applying for leave,
- checking leave balances,
- viewing attendance,
- submitting timesheets,
- viewing requests,
- accessing HR documents,
- and later interacting with HR through an AI assistant.

The custom portal should improve usability without replacing Frappe HR's business logic or creating a second HR system.

---

## 3. Users

**Who is this for?**

### Primary Users

- Employees
- Managers / Reporting Managers

### Administrative Users

Administrative users will primarily continue using Frappe HR directly:

- HR Manager
- HR Admin
- Super Admin
- IT Admin
- Finance users where applicable

Initial focus should be on the **employee experience**.

Manager-specific functionality can be expanded after the employee experience is stable.

---

## 4. Desired Outcome

**What should be better or possible when this is built?**

- Employees have a simple and modern HR self-service portal.
- Employees do not need to use the normal Frappe Desk interface for routine HR activities.
- Frappe HR remains the authoritative HR backend and source of truth.
- Existing Frappe workflows, permissions, approvals, and HR business rules continue to be respected.
- Employees can quickly understand their current HR status from one dashboard.
- Common HR activities require fewer screens and fewer clicks.
- The frontend can evolve independently without heavily modifying Frappe HR core.
- Future Frappe upgrades should require minimal frontend changes.
- The same controlled backend capabilities should eventually be usable by an AI HR assistant.
- Employee access should work securely with the company's Microsoft identity environment.

---

## 5. Current Requirements

### Authentication

- Employees should authenticate using company-managed identities.
- Microsoft Entra ID SSO is preferred.
- Employees should not receive or manage Frappe API keys.
- A logged-in employee must only be able to access information and actions permitted for that employee.

### Employee Dashboard

Provide a simple home dashboard showing relevant employee information such as:

- employee name,
- designation,
- department,
- reporting manager,
- location,
- current leave balances,
- attendance summary,
- current week's timesheet status,
- pending requests or approvals,
- recent HR activity,
- useful quick actions.

The dashboard should prioritize information employees frequently need rather than exposing all available HR data.

### Employee Profile

Employees should be able to:

- view their employee information,
- view employment information,
- update selected personal information,
- view HR-related links/documents where permitted.

Some sensitive or controlled fields must not be directly editable.

Existing Frappe HR permissions and business rules should remain authoritative.

Examples of employee-editable information may include:

- personal phone number,
- personal email,
- present address,
- permanent address,
- emergency contact information.

Sensitive or controlled information may require restrictions or HR approval.

### Leave

Employees should be able to:

- view leave balances,
- view leave history,
- view pending leave applications,
- apply for leave,
- cancel or withdraw a leave request where allowed,
- view approval status,
- see rejected leave and rejection reasons where available.

The frontend must respect Frappe HR leave policies, allocations, holiday lists, approval workflows, and validation rules.

The frontend must not independently calculate whether leave is valid.

### Attendance

Employees should be able to:

- view their attendance,
- view daily check-in/check-out information where available,
- view monthly attendance summaries,
- identify attendance exceptions such as absent, late, incomplete, or missing records.

Attendance data will come from Frappe HR.

Attendance import and device administration remain HR/Admin functions and are not part of the employee portal.

### Timesheets

Employees should be able to:

- view their current week's timesheet,
- add time entries,
- select permitted projects/tasks,
- save a draft,
- submit the weekly timesheet,
- view approval status,
- view previously submitted timesheets.

Existing Frappe HR project/task restrictions and approval rules should be respected.

Employees should not be able to enter time against unauthorized projects.

### Requests / Employee Services

Provide a simple employee request area for supported internal requests.

Possible categories include:

- HR requests,
- HR letters,
- asset / IT requests,
- payroll questions,
- travel requests,
- other employee service requests supported by the existing HR process.

The exact request workflow should use existing Frappe capabilities where practical instead of introducing an unrelated request system.

### Documents

Employees should be able to access permitted HR-related documents and links.

Where SharePoint is used for document storage, HelixHR may provide links to the appropriate employee document location while SharePoint remains responsible for document access control.

The frontend must not expose documents that the employee is not authorized to access.

### Notifications

Employees should be able to see relevant status information for actions such as:

- leave submitted,
- leave approved/rejected,
- timesheet submitted,
- timesheet approved/rejected,
- request status changes.

Initial notifications may be shown inside the portal.

Email or Microsoft Teams notifications can be considered separately.

### AI HR Assistant — Future Capability

The portal should be designed so an AI HR Assistant can later be added.

Example requests:

- "How much casual leave do I have?"
- "Apply leave this Friday."
- "Show my attendance this month."
- "What is missing from my timesheet?"
- "Submit my timesheet."
- "Update my phone number."
- "Who is my reporting manager?"
- "Show my pending HR requests."

The AI must operate through controlled HR actions.

The AI must not receive unrestricted access to Frappe DocTypes or generic database/API operations.

Actions that change HR data should clearly show what is going to happen before performing sensitive or significant actions.

---

## 6. Examples

### Example 1 — Apply for Leave

**User:** Employee

**Action:** Opens Leave, selects Casual Leave, chooses September 4, enters a reason, and submits.

**Expected result:**

The portal sends the request to Frappe HR.

Frappe HR validates:

- leave allocation,
- leave policy,
- holiday rules,
- overlapping requests,
- workflow requirements,
- and applicable permissions.

If valid, the leave application is created and the employee sees its current approval status.

---

### Example 2 — Weekly Timesheet

**User:** Employee

**Action:** Opens the current week's timesheet and records time against an assigned project.

**Expected result:**

Only projects/tasks the employee is permitted to use are displayed.

The employee can save the timesheet as a draft and submit it when complete.

Frappe HR remains responsible for validation and approval workflow.

---

### Example 3 — Employee Profile Update

**User:** Employee

**Action:** Changes their mobile number.

**Expected result:**

If the field is employee-editable, the change is saved through the HR backend and reflected in the employee record.

If the employee attempts to modify a controlled field, the portal prevents the direct update or follows the appropriate approval process.

---

### Example 4 — AI Leave Request

**User:** Employee

**Action:** Types:

"Take casual leave this Friday."

**Expected result:**

The AI determines the requested date and retrieves the employee's available leave information.

Before creating the request, the assistant presents something similar to:

Casual Leave  
September 4, 2026  
1 day

The employee confirms the action.

The assistant submits the request through the same controlled leave capability used by the normal employee interface.

The result and approval status are shown to the employee.

---

### Example 5 — AI Information Request

**User:** Employee

**Action:** Types:

"How many leave days do I have?"

**Expected result:**

The assistant retrieves the authenticated employee's leave balances and returns only information that employee is permitted to see.

No confirmation is required because the action is read-only.

---

## 7. Scope

### In Scope — Initial Employee Portal

- Microsoft Entra ID authentication
- Employee dashboard
- Employee profile
- Leave balance
- Leave history
- Leave application
- Leave status
- Attendance view
- Attendance summary
- Timesheet entry
- Timesheet submission
- Timesheet history
- Employee requests
- HR document links
- Employee notifications/status
- Responsive desktop/mobile web interface
- Integration with Frappe HR
- Secure employee-specific access
- Foundation for future AI assistant

### Potential Phase 2

- AI HR Assistant
- Manager dashboard
- Manager approvals
- Team attendance
- Team leave calendar
- Employee onboarding experience
- Employee directory
- Performance management
- Additional HR service requests
- Rich notifications
- Microsoft Teams integration

### Out of Scope — Initial Version

- Replacing Frappe HR
- Building a second HR database
- Rebuilding Frappe HR administration
- HR configuration
- Payroll administration
- Salary structure administration
- Leave policy administration
- Shift administration
- Attendance device configuration
- Attendance imports
- User/role administration
- Workflow designer
- Custom report builder
- Organization administration
- Recruitment
- Accounting
- Generic Frappe Desk replacement
- Direct database access from the employee frontend
- Exposing Frappe API credentials to employees
- Allowing AI unrestricted access to Frappe

HR/Admin users will continue using Frappe HR for these activities.

---

## 8. Known Constraints

- Frappe HR is the existing HR backend and must remain the HR system of record.
- Existing Frappe HR business rules should be reused rather than recreated in the frontend.
- Existing Frappe roles and permissions must remain an important security boundary.
- The solution handles employee and HR PII and therefore requires strict access controls.
- The organization operates in both India and the USA.
- Different companies, locations, leave policies, and HR processes may exist between India and USA employees.
- Current employee count is approximately 80–100, with expected growth toward approximately 200.
- Microsoft Entra ID is the preferred identity provider.
- SharePoint may remain the storage/access platform for some employee documents.
- Frappe HR must remain upgradeable without maintaining a heavily modified fork.
- Frappe core should not be changed when the requirement can be implemented through supported extension mechanisms.
- The employee portal must never trust employee identity, authorization, or business-rule validation only at the browser/frontend layer.
- AI functionality must not bypass normal Frappe authorization or HR workflows.

---

## 9. Assumptions / Uncertainties

The following decisions still need refinement before creating the specification.

### 1. Employee Portal Authentication Model

Microsoft Entra ID SSO is preferred, but the exact relationship between:

- Entra identity,
- portal authentication,
- Frappe User,
- and Employee record

needs to be finalized.

### 2. Frappe API Boundary

We need to decide how much of the standard Frappe REST API the frontend should consume directly versus using a dedicated HelixHR API/interface layer.

**Current preference:** expose controlled HelixHR employee operations rather than tightly coupling the frontend to many raw Frappe DocTypes.

### 3. Employee Profile Editing Rules

We need a definitive list of:

- employee-editable fields,
- read-only fields,
- sensitive fields,
- fields requiring HR approval.

### 4. Manager Experience

It is not yet decided whether manager functionality should:

- remain entirely within Frappe HR initially,

or

- be included in the custom portal shortly after employee functionality.

### 5. Request Desk Implementation

We need to determine whether employee service requests should use:

- an existing Frappe HR/Frappe mechanism,
- a small custom DocType/workflow,
- or another existing internal process.

Avoid building a large ticketing platform unless the business actually requires one.

### 6. Notifications

It is undecided whether the first version requires:

- portal-only notifications,
- email,
- Microsoft Teams,
- or a combination.

### 7. AI Action Confirmation

A policy needs to be defined for which AI operations require confirmation.

Current assumption:

**Read-only actions**

Examples:

- leave balance,
- attendance,
- employee information.

No confirmation required.

**Write actions**

Examples:

- apply leave,
- submit timesheet,
- update employee information.

The user should see and confirm the proposed change before sensitive or consequential actions are executed.

### 8. AI Scope

The initial AI assistant could either:

- support only a small number of high-value HR actions,

or

- attempt broad conversational coverage.

**Current preference:** start with a small set of reliable tools rather than a general-purpose agent with broad HR access.

---

## 10. Additional Context

This project is part of the **HelixHR** initiative.

The company currently operates two primary legal entities:

- Nitco Inc — USA
- Nitco Outsourcing Pvt Ltd — India

Frappe HR is being used as the HR platform.

The intended product boundary is:

**Employees**

Use the HelixHR Employee Portal.

**Managers**

Initially may use a combination of HelixHR and Frappe HR depending on the final scope.

**HR / Administrators**

Continue using Frappe HR Desk.

The long-term objective is not simply to redesign Frappe HR.

The objective is to use Frappe HR as the reliable HR engine while providing a significantly simpler employee experience.

A future HelixHR AI Assistant should act as another interface to the same permitted HR capabilities rather than becoming a separate HR system or receiving unrestricted backend access.

---

# Refinement Priority

When running `/grill-with-docs`, prioritize unresolved decisions in approximately this order:

1. Employee identity and authorization boundary
2. Employee profile editing/PII rules
3. Exact v1 employee workflows
4. Manager responsibilities in the custom portal
5. Request Desk behavior
6. AI write-action safety and confirmation rules
7. Notifications
8. API boundary between HelixHR and Frappe

Do not spend significant refinement time on:

- visual styling,
- component libraries,
- database schemas,
- framework selection,
- endpoint naming,
- deployment topology,
- coding standards,
- detailed AI model selection,

unless one of those decisions materially changes the product requirements.

Once the important product and permission decisions are resolved, stop refinement and move the document to the specification stage.