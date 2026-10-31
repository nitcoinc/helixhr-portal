---
title: "feat: Project people multi-add, open-to-everyone across companies, add controls on top"
type: feat
status: active
date: 2026-11-01
origin: none (follows plan 2026-10-31-001, shipped in v1.0.0)
---

# feat: Project people multi-add, open-to-everyone across companies, add controls on top

## Summary

Four changes to the project workspace shipped in v1.0.0 (plan 2026-10-31-001):

1. "Open to everyone" can name more companies than the project's own. Only non-billable projects can turn it on.
2. Department adds take several departments in one go. People adds keep their multi-select. There is no "Select all" anywhere.
3. The picker hides anyone who already has booking access, whether as a member or through an open company, and says how many it hid. The server skips them too.
4. "Add people" and "New task" move from the bottom of each pane to its header.

The project's own company does not change.

---

## Problem Frame

Common internal (non-billable) projects such as training, internal meetings or bench time are shared by every company in the group. Today "Open to everyone" covers only the project's own company. So each company needs its own copy of the project, or every other-company person must be added by name.

Billable projects stay member-only. People are added individually or by department.

The picker accepts one department per add. It also lists people who are already on the project, greyed out, among the search results.

On a large project the add controls sit below the full list, so adding means scrolling past everything.

---

## Requirements

- R1. A non-billable project's "Open to everyone" covers its own company plus any companies the editor picks from their project write scope.
- R2. A project cannot become both billable and open to everyone, from the portal or from Desk.
  - A billable project that is already open keeps working.
  - Its switch is shown so it can be turned off, but not turned on again.
- R3. Booking eligibility (`_my_project_names`) includes an Open project when its flag is on and the employee's company is the project's company or one of its extra companies.
- R4. The department add accepts several departments in one call. It deduplicates people across overlapping subtrees, and the count of people it would actually add respects `_BULK_MEMBER_LIMIT`.
- R5. The picker never offers someone who already has booking access:
  - Existing members are hidden from search.
  - On an Open project with the switch on, people in an open company are hidden from search.
  - Departments wholly in an open company are hidden from the department list.
  - A short line says what was hidden and why, only when something was.
- R6. The server skips covered people with a reason, so the rule holds even when the client is bypassed.
- R7. "Add people" sits in the People pane header and "New task" in the Tasks pane header. "New task" reveals the existing multi-line form at the top of the pane.
- R8. There is no "Select all" control.

---

## Scope Boundaries

- No change to how a project's company is chosen or shown.
- No bulk remove of people and no bulk close of tasks.
- People search stays own-company (`get_directory`). Cross-company people are still reached through departments.
- No change to billing, invoicing or ERPNext costing.
- No general Desk lock-down of Project (deferred by the 2026-10-19 access decisions). Only the two new rules below are enforced on Desk saves.

### Deferred to Follow-Up Work

- People search across every company in the caller's project scope. This needs a scoped search endpoint, because `get_directory` is own-company by design.
- Per-currency cost display on mixed-company projects (see Risks).
- Showing a headcount per department in the checklist.

---

## Context & Research

### Relevant Code and Patterns

- `helixhr/api.py`:
  - `_my_project_names` is the one booking rule. Task picker, week reads and save validation share it.
  - `set_project_open_to_all`, `add_project_members`, `_department_candidates`, `_project_departments`, `get_project`, `search_projects`, `_project_write_gate`.
- `helixhr/utils.py`: `resolve_project_scope`, `read_companies`, `scope_covers`.
- `helixhr/events.py` and `helixhr/hooks.py` `doc_events`. There is no Project hook yet.
- `helixhr/helixhr/doctype/helixhr_company_access_company/`, a one-field (`company` Link) child table. Shape to copy.
- `helixhr/fixtures/custom_field.json`: `Project-helixhr_open_to_all` (Check, inserted after `helixhr_is_billable`).
- Frontend:
  - `frontend/src/components/ProjectMemberPicker.vue` (`RESULTS_LIMIT` 20)
  - `frontend/src/components/projects/ProjectPeoplePane.vue`
  - `frontend/src/components/projects/ProjectTasksPane.vue`
  - `frontend/src/pages/Projects.vue`, which owns `get_project` and already holds the caller's company as `creatorCompany` from `search_projects.company`
  - `frontend/src/lib/projects.js` (pure helpers with vitest)
- Tests:
  - `helixhr/tests/test_api_projects.py`
  - `helixhr/tests/test_project_open_to_all.py`
  - `frontend/src/lib/projects.test.js`
  - `frontend/tests/e2e/projects.spec.ts`

### Research Findings

- **Cross-company booking already exists.**
  - `add_project_members` adds anyone in the caller's write scope, from any company.
  - Neither HelixHR's timesheet save nor ERPNext's Timesheet controller compares project company with timesheet company. Only the Desk form filters the project picker by company.
  - So an open project spanning several companies is the by-name rule applied to a whole company.
- **Costing is the only cross-company effect, and it is identical for by-name members.**
  - ERPNext `Project.update_costing` sums `base_costing_amount` over every submitted timesheet row on the project. Each row is in its own timesheet company's base currency.
  - With different currencies, `total_costing_amount` and `gross_margin` mix currencies.
  - Hours (`actual_time`) stay correct.
  - Timesheets post no GL entries. Non-billable time is never invoiced.
  - HelixHR books every row as activity type `General`, so costing is non-zero only where an Activity Cost or Activity Type rate exists.
  - The user accepted this: choosing a project from a company means accepting its costing.
- **`get_directory` returns only the caller's own company**, and allows up to 200 rows per page. When the project is open to the caller's company, every person search result is covered.
- **Billable can only be edited in Desk.** The portal sets it only at create. So Desk is the realistic path that would combine billable and open.

---

## Key Technical Decisions

- **The extra companies live in a new child table, `HelixHR Project Company` (one `company` Link).**
  - It is attached to Project as a Table MultiSelect custom field, `helixhr_open_companies`, which is `read_only` in Desk. Only `set_project_open_to_all` writes it, so the scope check cannot be bypassed.
  - The project's own company is always implied and never stored.
  - Existing open projects keep working with no patch, because an empty table means own company only.
  - A new child DocType over reusing `HelixHR Company Access Company`: a table named for another feature would show in Project's Desk form and couple two schemas.
- **Billable and open are kept apart by a Project `validate` doc event, checked on transitions only (R2).**
  - Using `get_doc_before_save`, it refuses a save that turns the flag on while billable, or turns billable on while the flag is on.
  - Legacy billable projects that are already open still save and still book. There is no data patch, because clearing them would silently cut off people already booking.
- **Turning the switch off clears every extra-company row.**
  - Turning off already revokes all open booking, so nothing effective is lost.
  - The next turn-on starts from exactly what that editor picks, so no hidden grant comes back. A turn-on that names no companies means own company only.
- **While the switch is on, editing the list replaces only the rows inside the editor's write scope.**
  - Rows for companies the editor cannot see are kept.
  - `get_project` reports them as fixed, so the dialog and the note show the true list. This mirrors the cross-company grant-authority rule.
- **"Covered" has one definition, shared by the server and the picker:** the project is Open, the flag is on, and the employee's company is in the effective open list (own company plus rows).
  - `add_project_members` skips covered people with reason "can already book".
  - `get_project` returns the effective list only when the project is covered by that rule. The server skip is the authority.
- **`get_project` carries everything the panes need in one payload** (for `can_edit` callers):
  - `company`
  - `open_companies`, the effective list
  - `open_company_choices`: `read_companies(write)` minus the project's company, or every Company name when unscoped
  - `fixed_open_companies`: stored rows outside the editor's scope
- **Several departments use a new `departments` list parameter** (at most 50 per call). `department` stays accepted, because whitelisted dotted paths are public API.
  - Candidates are unioned and deduplicated.
  - Members and covered people are removed before the `_BULK_MEMBER_LIMIT` check, so the limit counts real additions.
- **Header actions reuse the existing forms.**
  - "New task" toggles the current textarea form under the header. It does not become a dialog, so multi-line paste keeps working.
  - "Add people" opens the same `ProjectMemberPicker`.

---

## Implementation Units

### U1. Child table, custom field and Project validate hook

**Goal:** Store the extra open companies, and keep billable and open apart on every save path.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Create: `helixhr/helixhr/doctype/helixhr_project_company/` (JSON, `__init__.py`, controller), copying `helixhr_company_access_company`
- Modify: `helixhr/fixtures/custom_field.json`
  - Add `Project-helixhr_open_companies`: Table MultiSelect, `read_only: 1`, depends_on `helixhr_open_to_all`, after `helixhr_open_to_all`.
  - Its description names the extra companies, says they are set from the HelixHR portal, and warns that costing totals may mix currencies.
  - The applied `helixhr_open_to_all` field is left unchanged.
- Modify: `helixhr/events.py`, `helixhr/hooks.py` (Project `validate`)
- Test: `helixhr/tests/test_project_open_to_all.py`

**Approach:**
- The child table is `istable: 1`, with one required `company` Link field.
- The validate hook compares with `get_doc_before_save()` and refuses only the two transitions named in the Key Technical Decisions. A new Project doc counts as a transition from off.

**Test scenarios:**
- Happy path: a Project saved with two `helixhr_open_companies` rows reads back both.
- Error paths:
  - A Desk-style `doc.save()` turning the flag on for a billable project is refused.
  - Turning billable on for an open project is refused.
- Edge case: a legacy project that is both billable and open saves with an unrelated change, such as a renamed project.

**Verification:** `bench migrate` creates the DocType and field on a fresh site. The field shows read-only in Desk only when the flag is on.

---

### U2. Open-to-everyone across companies: write, booking, read

**Goal:** Writes and booking honour the extra companies, and `get_project` returns what the panes need.

**Requirements:** R1, R2, R3

**Dependencies:** U1

**Files:**
- Modify: `helixhr/api.py`: `set_project_open_to_all`, `_my_project_names`, `get_project`
- Test: `helixhr/tests/test_project_open_to_all.py` (existing tests must pass unchanged), `helixhr/tests/test_api_projects.py`

**Approach:**
- `set_project_open_to_all(project, value, companies=None)`:
  - Gate through `_project_write_gate`.
  - Load with `get_doc(..., for_update=True)`, set the flag and rows, then `save(ignore_permissions=True)`. `users` is untouched, so ERPNext adds no DocShare. The save also recomputes costing and percent complete.
  - Turn-on on a billable project is refused before the save. The validate hook backs this up.
  - Each company is checked with `scope_covers`, refusing an out-of-scope one with the generic not-here message. The project's own company is dropped from the list.
  - On, the in-scope rows are replaced and out-of-scope rows kept. Off, all rows are cleared.
  - Returns `{open_to_all, open_companies, fixed_open_companies}`.
- `_my_project_names` also matches Open, flagged projects whose child table holds the employee's company. That is one extra indexed query.
- `get_project` adds the fields named in the Key Technical Decisions.

**Patterns to follow:** existing `set_project_open_to_all` docstring and rate limit; the `get_doc(for_update=True)` write in `add_project_members`.

**Test scenarios:**
- Happy path: a non-billable project in company A opened to B. A B employee's `get_my_projects` includes it, and the project's tasks are bookable on save.
- Edge cases:
  - The flag is on with no rows: only A's employees see it. This is the regression guard for v1.0.0 data.
  - The project is not Open (Completed): a B employee does not see it.
  - Passing A in `companies` is ignored, with no duplicate row.
  - An editor scoped to A, editing while on, keeps a C row added by an unscoped admin. `get_project` lists C in `fixed_open_companies`.
  - An admin adds C, the switch is turned off, then an A-scoped editor turns it on with B: a C employee cannot book.
  - For an unscoped editor, `open_company_choices` lists every Company except the project's.
- Error paths:
  - Turning on a billable project is refused, and the flag stays off.
  - Turning off a billable project that is already open succeeds.
  - A company outside the editor's scope is refused, and nothing is written.
  - A caller with no write scope gets the existing not-found refusal.
- Integration: removing B from the list makes a B employee's draft week naming the project fail on next save, with the existing member-removal message.

**Verification:** new and existing open-to-all tests pass.

---

### U3. Several departments per add; skip covered people on the server

**Goal:** One `add_project_members` call takes many departments, and never adds someone who can already book.

**Requirements:** R4, R5, R6

**Dependencies:** U2

**Files:**
- Modify: `helixhr/api.py`: `add_project_members`, `_department_candidates`
- Test: `helixhr/tests/test_api_projects.py`

**Approach:**
- Accept `departments` (a list, at most 50) alongside the existing `department`.
- Resolve each department via `_department_candidates`, then union and deduplicate.
- The per-department count check stays as an early refusal.
- The covered check runs after the existing not-available / scope branch, so an out-of-scope id still returns no name.
- Covered people are skipped with reason "can already book".
- `_BULK_MEMBER_LIMIT` is applied to the people who would actually be added.

**Test scenarios:**
- Happy path: two departments with one shared person (a sub-department of both selections) add every active person once, and the shared person appears once in `added`.
- Edge cases:
  - Selections whose real additions exceed `_BULK_MEMBER_LIMIT` are refused with that count, and nobody is added.
  - The legacy single `department` still works.
  - 51 departments are refused.
- Covered:
  - The project is Open and open to A, and a department in A is added: everyone is skipped as "can already book".
  - The same with the project Completed: people are added normally.
  - A department in B (not open) is added normally.
- Error paths:
  - A department outside scope in the list refuses the whole call.
  - A non-string entry is refused.
  - An out-of-scope employee id in a covered company still comes back as "not available" with no name.

**Verification:** existing `add_project_members` tests pass unchanged, and the new tests cover multi-department and covered skips.

---

### U4. Picker: department checklist, hide covered, no "Select all"

**Goal:** The member picker offers several departments at once and never offers anyone with access.

**Requirements:** R4, R5, R8

**Dependencies:** U2, U3

**Files:**
- Modify: `frontend/src/components/ProjectMemberPicker.vue` (new props `openCompanies`, `callerCompany`)
- Modify: `frontend/src/pages/Projects.vue`: pass `creatorCompany` as `callerCompany`
- Modify: `frontend/src/lib/projects.js`: a pure helper. Given departments, people results, member ids and open companies, it returns what to offer and the hidden counts.
- Test: `frontend/src/lib/projects.test.js`

**Approach:**
- **Department mode** becomes a labelled checkbox group:
  - It has a filter field and a scrollable flat list. Each row shows the department, its parent, and its company when there are several.
  - A note says that ticking a department includes the departments under it.
  - Chips show the selection. The add button reads "Add people from N departments" and is disabled at zero.
  - A live region announces the filter match count.
  - Empty states read "No departments match." and "Everyone in these departments can already book here."
- Departments whose company is covered are hidden, with "Departments in {companies} are left out: everyone there can already book here."
- **People mode:**
  - The picker requests `min(200, 20 + members)` rows and drops members on the client.
  - "N already on this project not shown." appears only when N > 0.
  - A page still full after filtering shows "More matches: type more of the name."
  - When `callerCompany` is covered, the picker opens in Departments mode. The People tab shows a note with a button that switches to Departments.
- The result card keeps "Added N, skipped N" with reasons.

**Test scenarios:**
- Helper:
  - Departments in an open company are excluded and counted.
  - With the flag off, nothing is excluded.
  - Members are excluded from people results and counted.
  - A full page made up entirely of members reports zero offerable people and flags more matches.
  - An empty input returns empty output.

**Verification:** `yarn test` and `yarn lint` are clean. The picker shows no "Select all".

---

### U5. Panes: header actions, company list on the switch, billable gating

**Goal:** Add controls sit on top, and the open switch edits its company list on non-billable projects only.

**Requirements:** R1, R2, R7

**Dependencies:** U2, U4

**Files:**
- Modify: `frontend/src/components/projects/ProjectPeoplePane.vue`
- Modify: `frontend/src/components/projects/ProjectTasksPane.vue`
- Modify: `frontend/src/pages/Projects.vue`: pass `billable` and the `get_project` company fields

**Approach:**
- **People header:** title and count, then "Add people", then the switch.
  - At phone width each control wraps to its own row and keeps a 44px target.
- **Switch:**
  - It renders when `!billable || openToAll`.
  - On a billable project that is open, it carries "Billable projects can only be turned off."
  - On a billable project that is not open, a muted line reads "Billable projects can't be opened to everyone."
  - While on, a summary "Open to A, B · Edit companies" opens the dialog.
- **Dialog, for turning on or editing:**
  - The project's company is shown fixed and checked, and `fixed_open_companies` are checked and disabled with "set by someone with wider access".
  - `open_company_choices` are checkboxes. With none, the line reads "No other companies in your access."
  - It has a saving state and an inline error that keeps the dialog open.
  - The note is built from the server's returned effective list.
- **Turn-off and company-removal confirm** says "Everyone in {companies} not listed by name will no longer be able to book here. Their draft weeks with this project will need that row removed."
- **Tasks header:**
  - "New task" is a toggle with `aria-expanded` and `aria-controls`. It reveals the existing form directly under the header and focuses the textarea.
  - Escape closes the form but keeps the draft until a successful add, and focus returns to the button.
  - A failed add keeps the form open, with the text and an inline error.
  - The empty state offers the same action.

**Test scenarios:** covered by U6 e2e. There is no pure logic beyond U4's helper.

**Verification:** `yarn build` passes. At 375px there is no horizontal scroll.

---

### U6. End-to-end coverage

**Goal:** Prove the flows in a browser.

**Requirements:** R1, R2, R5, R7, R8

**Dependencies:** U5

**Files:**
- Modify: `frontend/tests/e2e/projects.spec.ts`
- Modify: `helixhr/tests/utils.py`: `setup_playwright_fixtures` gains a second company B with a department and a non-billable project. It also gives the hr e2e user a HelixHR Delivery Manager org assignment for B, so `resolve_project_scope` covers A and B for project writes only. `resolve_admin_scope` and the other hr specs are unchanged.

**Test scenarios:**
- On a non-billable project, open to everyone and add company B. The summary names both companies, and a reload keeps it.
- A billable project shows the "can't be opened" line and no switch.
- With 60 tasks, "New task" is visible without scrolling. Pasting 3 lines adds 3 and closes the form.
- Picker:
  - Select two departments and add. The result card reports added and skipped.
  - Existing members are absent from search, and the hidden count is shown.
- A view-only caller still sees no add controls. The existing test is updated for the header placement.
- The existing keyboard-focus test still passes with "Add people" in the header.

**Verification:** the e2e suite passes on a fresh site per `README.md` → Verify, including `employee-mobile-webkit`.

---

## System-Wide Impact

- **Booking rule:** `_my_project_names` feeds the task picker, week reads and save/submit validation. One change covers all three.
- **Removal effects:** removing a company, or turning the switch off, refuses that company's draft weeks naming the project on next save. The confirm states this.
- **Reports:** hours reports grouped by project will now show other-company people's hours under a company-A project. That is intended. Costing totals may mix currencies (see Risks).
- **Desk:** two new save refusals on Project (billable and open together). The company list is read-only in Desk.
- **API:**
  - `add_project_members` gains `departments`.
  - `set_project_open_to_all` gains `companies`.
  - `get_project` gains `company`, `open_companies`, `open_company_choices` and `fixed_open_companies`.
  - All are additive, and the old parameters still work.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Mixed-currency `total_costing_amount` / `gross_margin` on a project with members from companies in different currencies. Hours stay correct. | Accepted by the user. Already true for cross-company by-name members. Warned in the new field's description. Per-currency display is deferred. |
| A billable project already open stays open after deploy. | The switch shows with a turn-off-only note. No silent data change. |
| `get_directory` is own-company, so People mode is empty when the caller's company is covered. | The picker opens in Departments, with a note. Cross-company search is deferred. |
| An editor with a narrow scope erases another company's grant, or one silently comes back. | Edits replace only in-scope rows. Turn-off clears all rows. Fixed rows are shown. Each is covered by a U2 test. |
| Desk edits bypass the portal gates. | Validate hook for billable and open. The company list is read-only in Desk. |

---

## Documentation / Operational Notes

- `docs/architecture.md` (projects section): describe the booking rule with the extra companies, the billable/open validate hook, and the turn-off-clears rule.
- No patch. `bench migrate` creates the child DocType and custom field.
