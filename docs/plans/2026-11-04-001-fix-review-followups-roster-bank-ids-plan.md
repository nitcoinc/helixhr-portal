---
title: "fix: Code review follow-ups for roster draft controls and Bank & IDs self-edit"
type: fix
date: 2026-11-04
origin: none
follows: docs/plans/2026-11-03-001-feat-roster-draft-controls-bank-ids-self-edit-plan.md
---

# fix: Code review follow-ups for roster draft controls and Bank & IDs self-edit

## Summary

The two-axis `/code-review` of merge `306fbd6` (plan 2026-11-03-001) raised 11 findings. Checking each against the code left five that need code changes (three defects: `salary_mode` blocking first fills, silent run cut-short, missing preflight guard; one UX gap: picker; one smell: duplicate flag helper) and six that need no code change (table below). This plan fixes the real ones at their root, records why the rest stay, and ends with a second review round of the whole feature against `origin/main` before anything is pushed.

---

## Problem Frame

The feature is merged into local `main` but not pushed. The review found:

- **`salary_mode` blocks mass onboarding.** `submit_my_profile_changes` and `profileChanges.groupStates` both mark a group "filled" if *any* field in it has a value. `salary_mode` is in the bank group, and HR commonly pre-sets it ("Bank"). Every such employee's first bank entry then routes to HR with proof instead of filling instantly, which is the opposite of the onboarding ask. The plan-time doc review flagged "salary_mode in the blank test" as FYI and it was not acted on.
- **Blank/filled and core fields are decided twice.** The server and the browser each compute group state and each hold a copy of the first-fill core fields (`PROFILE_FILL_CORE_FIELDS` in `helixhr/utils.py`, `FILL_CORE_FIELDS` in `frontend/src/lib/profileChanges.js`). That is how the `salary_mode` bug lives in two places, and the copies will drift on regional sites.
- **A run cut short is silent (amendment A3).** The simulator can only refuse. When a run-aware Change/End stops at a day whose off status is unknown, or a person has no holiday list (A7), it acts on fewer blocks than the lead expects and says nothing. A3 asked that the preview name the person.
- **No preflight guard for the new field allow-list.** `PROFILE_CHANGE_GROUPS` is a portal allow-list over Employee (ERPNext/HRMS-owned). A site missing a regional field (e.g. `ifsc_code` on `test_site`) silently relaxes the core-field rule. CONTRIBUTING.md: "Every structural change gets a preflight guard."
- **Duplicate flag helper.** `api._truthy` is new and duplicates the existing `api._flag`.
- **Picker gap (A12).** "Select all shown" picks only loaded people. Whole team exists as its own option, but the picker does not point to it.

### Findings closed without a code change

| Finding | Why no code change |
|---|---|
| A4: Change on a later block's start date | Implemented in `roster_ops._apply` (`via_gap` / `_has_previous_in_run`) and covered by the shared `CASES` row "run: change on a later block's first day recreates it". |
| B3: email check vs message | The check passes on any of company, personal or previous personal email. The refusal tells the employee to add a personal email because that is the only address they can set themselves. Correct as is. |
| R10: older requests show no block count | Intended: v1 requests keep single-block behaviour (A1) and have no stored steps. |
| B5: an HR Manager's own bank fill alerts no other HR | HR Managers already edit these fields in Desk with no alert, so the alert adds no control for them; nobody can decide their own HR Request (`helixhr/events.py`, "You can't decide your own request"). Recorded in docs. |
| `roster_ops._flag` vs `api._truthy` behaviour drift | No live divergence: `_roster_draft_ops` normalises `skip_off_days` to 0/1 before the simulator sees it. Only the duplication is fixed. |
| `submit_my_profile_changes` length (~170 lines) | Judgement-call smell; splitting it is deferred (see Scope Boundaries). |

---

## Requirements

- R1. Whether a Bank & IDs group is blank (instant fill) or filled (goes to HR) depends only on its identity fields. `salary_mode` never makes a group filled.
- R2. `salary_mode` stays employee-editable in the bank group and routes with the group.
- R3. The server is the only place group state and first-fill core fields are defined; the browser uses what `get_my_profile` sends.
- R4. A run-aware Change/End that stops early because off days are unknown, or that acts on one block because the person has no holiday list, returns a non-blocking note naming the person. The draft bar shows it.
- R5. Notes never refuse a draft, submit or approval, and are not part of the stored steps.
- R6. Preflight reports which Bank & IDs fields the site's Employee lacks: WARN for regional fields, FAIL for fields ERPNext always ships.
- R7. One flag parser in `helixhr/api.py`.
- R8. The people picker tells the lead to use whole team when more people exist than are loaded.
- R9. Docs record the R1 rule, notes, the preflight check, and the closed findings with reasons.
- R10. A second `/code-review` of the whole feature against `origin/main` runs after the fixes; its real findings are fixed or explicitly deferred before push.

---

## Scope Boundaries

- No change to who can edit which field, the fill gates (password, Active, 72h hold, recipients), or the notice content.
- No new roster refusal. Notes are informational only.
- HR approval view does not show notes; the lead sees them before submit.

### Deferred to Follow-Up Work

- Splitting `submit_my_profile_changes` into parse / route / notify helpers.
- Showing run notes to HR on the approval view.
- A read-only roster preview endpoint (live block preview before save).

---

## Key Technical Decisions

- **KTD1. Identity fields drive state.** Group state = "filled" iff any field in the group *other than* `salary_mode` has a value. Expressed as one server-side definition next to `PROFILE_CHANGE_GROUPS` (e.g. a set of fields excluded from state), not a second group table. Rationale: `salary_mode` is a payroll preference, not an identifier; HR pre-setting it says nothing about whether bank details exist.
- **KTD2. Core fields apply only when an identity field is staged.** A blank bank group where the employee stages only `salary_mode` fills instantly without demanding bank name/account. Staging any identity field in a blank group still requires the core set. Rationale: otherwise an employee choosing "Cash" with no bank account is stuck.
- **KTD3. `get_my_profile` sends group facts.** A `groups` map `{group: {state, core}}`, where `core` lists only fields this site has. The browser drops its own `groupStates` computation and `FILL_CORE_FIELDS` copy, and builds `seen` from the server's `state`. The existing stale check in `submit_my_profile_changes` is unchanged and still decides.
- **KTD4. Notes are a separate simulator output channel.** The simulator reports run notes alongside problems without changing what problems or steps mean. Steps (compared at approval, A2) and problems (refusals) are untouched. Exact mechanism (extra return value vs optional collector argument) is an implementation choice; it must be mirrored in `frontend/src/lib/rosterDraft.js` and expressed in the shared `CASES` table.
- **KTD5. Two note causes.** (a) Run stopped at a day whose off status is unknown (outside the off-day window or holiday list range). (b) Person has no off-day data, so Change/End acted on the covering block only (A7). A run broken by a working day or a different shift type is normal and gets no note.
- **KTD6. Preflight severity split.** FAIL when an ERPNext-standard Employee field in `PROFILE_CHANGE_GROUPS` is missing (an upgrade renamed it). WARN listing regional fields that are absent, saying the core-field rule relaxes for them. Which fields are regional is decided by what ERPNext v16's Employee ships without regional apps; confirm at implementation.

---

## Implementation Units

### U1. Bank & IDs group state from identity fields, served once

**Goal:** Fix the `salary_mode` blocker and make the server the single source of group state and core fields.

**Requirements:** R1, R2, R3

**Dependencies:** none

**Files:**
- Modify: `helixhr/utils.py`, `helixhr/api.py` (`submit_my_profile_changes` state and core-field checks, `get_my_profile`)
- Modify: `frontend/src/lib/profileChanges.js`, `frontend/src/components/profile/BankIdsEditor.vue`, `frontend/src/pages/Profile.vue`
- Test: `helixhr/tests/test_api_profile.py`, `frontend/src/lib/profileChanges.test.js`, `frontend/tests/e2e/profile-bank-edit.spec.ts`

**Approach:**
- Add the state-exclusion definition (KTD1) beside `PROFILE_CHANGE_GROUPS`; use it in the `submit_my_profile_changes` state loop and the B4 retry check.
- Apply KTD2 in the core-field check.
- `get_my_profile` returns `groups` (KTD3), computed by the same helper the submit path uses.
- Browser: `routes`, `seenStates`, `missingCore` read the server's `groups`; delete `groupStates` and `FILL_CORE_FIELDS`.

**Execution note:** Write the failing `salary_mode`-preset test first.

**Patterns to follow:** `PROFILE_FILL_CORE_FIELDS` / `PROFILE_CHANGE_GROUPS` in `helixhr/utils.py`; `_profile_change_input` in `helixhr/api.py` for per-field facts sent to the editor.

**Test scenarios:**
- Happy path: employee with `salary_mode="Bank"` and no other bank fields submits bank name + account (+ IFSC where present) → status `applied`, no HR Request, self-filled mark written.
- Happy path: `get_my_profile` for that employee reports `groups.bank.state == "blank"`.
- Edge: blank bank group, stage only `salary_mode="Cash"` → `applied`, no core-field refusal.
- Edge: blank group, stage `bank_name` only → refused for missing core fields, naming account number.
- Edge: `bank_ac_no` on file, change `salary_mode` → goes to HR (filled group), proof required.
- Edge: `groups.bank.core` omits `ifsc_code` on a site without that field.
- Error: `seen.bank="filled"` sent while server state is blank → stale refusal (unchanged behaviour).
- Integration: B4 retry with `salary_mode` preset → second identical submit reports `applied`.
- Browser (vitest): `routes` returns `fill` for bank when `groups.bank.state` is blank even though `salary_mode` has a value; `missingCore` uses `groups.*.core`.
- e2e: seeded employee with `salary_mode` set and blank bank details fills bank in one submit, no proof asked.

**Verification:** Mass-onboarding path works with `salary_mode` pre-set; no state or core-field logic remains in `profileChanges.js`.

---

### U2. Run notes for unknown off days

**Goal:** Tell the lead, by name, when a Change/End touched fewer blocks than the run because off days are unknown.

**Requirements:** R4, R5

**Dependencies:** none

**Files:**
- Modify: `helixhr/roster_ops.py`, `frontend/src/lib/rosterDraft.js`
- Modify: `helixhr/api.py` (`_roster_draft_payload`, the save/bulk responses that carry problems)
- Modify: `frontend/src/components/RosterDraftBar.vue`
- Test: `helixhr/tests/test_roster_ops.py`, `frontend/src/lib/rosterDraft.test.js`, `helixhr/tests/test_roster_change.py`

**Approach:**
- Simulator emits notes per KTD4/KTD5 with `{index, employee, message}`; new message constants beside `OFF_DAYS_UNKNOWN`.
- Legacy mode (`off_days=None`, v1 requests) emits no notes.
- API prefixes the person's name as it already does for problems (`"{0}: {1}"`) and returns `notes` in the draft payload.
- Draft bar lists notes under the affected row, styled as info, not error; `role="status"`.

**Execution note:** Add the `CASES` rows first in both suites and confirm they fail.

**Patterns to follow:** `problems` shape and name prefixing in `helixhr/api.py`; shared literal `CASES` table kept identical across `test_roster_ops.py` and `rosterDraft.test.js`.

**Test scenarios:**
- Happy path (CASES): run of three blocks, gap day between block 2 and 3 outside the off-day window → Change from block 1 recreates blocks 1–2, note at index 0.
- Happy path (CASES): person with no off-day data, End mid-block → ends covering block only, note "no holiday list" cause.
- Edge (CASES): run broken by a working day → no note.
- Edge (CASES): run broken by a different shift type → no note.
- Edge (CASES): legacy mode → no notes for the same input.
- Error path: note present does not add a problem; draft still submits.
- Integration (`test_roster_change`): save a draft with such a Change → payload `notes` carries the employee name; submit succeeds; approval compares steps only and applies.

**Verification:** Both CASES suites pass identically; lead sees a named note in the draft bar for the two causes and nothing otherwise.

---

### U3. Small cleanups: one flag parser, clearer names, picker hint

**Goal:** Remove the duplicate helper, sharpen the unclear names the review named, and close the A12 gap.

**Requirements:** R7, R8

**Dependencies:** none

**Files:**
- Modify: `helixhr/api.py`, `helixhr/roster_ops.py`, `frontend/src/components/RosterPeoplePicker.vue`
- Test: `helixhr/tests/test_roster_change.py` (existing skip-flag cases), `frontend/tests/e2e/roster.spec.ts` (picker hint)

**Approach:**
- Replace `_truthy` with the existing `_flag` (a garbage value counts as off).
- Rename where it reads better: `_steps_for`, local `off` → `off_days`-style names in `roster_ops.py`. Keep public names (`simulate`, `affected_window`) and the JS mirror in step.
- Picker: when the team has more people than loaded, show one line under "Select all shown" pointing to whole team.

**Patterns to follow:** `api._flag` docstring; existing hint text style in `RosterShiftSheet.vue` ("Everyone who reports to you…").

**Test scenarios:**
- `skip_off_days` values `1`, `"1"`, `"true"`, `True` → kept; `0`, `"false"`, `"yes"`, `None` → dropped.
- e2e: picker with more than one page of people shows the whole-team hint; a small team does not.

**Verification:** `grep` finds no `_truthy`; existing roster suites pass unchanged.

---

### U4. Preflight guard for the Bank & IDs field allow-list

**Goal:** Make a missing or renamed Employee field visible per site.

**Requirements:** R6

**Dependencies:** U1 (uses the same field definitions)

**Files:**
- Modify: `helixhr/preflight.py` (new check, registered in `CHECKS`)
- Test: `helixhr/tests/test_preflight.py`

**Approach:** Mirror `check_configuration_field_sets`: walk `PROFILE_CHANGE_GROUPS`, split missing fields by KTD6, return FAIL / WARN / PASS with field names.

**Patterns to follow:** `check_configuration_field_sets` and `_result` in `helixhr/preflight.py`.

**Test scenarios:**
- PASS when every field exists (patch `has_field` to True).
- WARN naming `ifsc_code` when only regional fields are missing.
- FAIL naming `bank_ac_no` when a standard field is missing.

**Verification:** `bench --site test_site execute helixhr.preflight.run` shows the new check; on `test_site` it WARNs for the regional fields that site lacks.

---

### U5. Docs

**Goal:** Record the new rule, notes, preflight check, and why the closed findings stay closed.

**Requirements:** R9

**Dependencies:** U1–U4

**Files:**
- Modify: `docs/architecture.md` (Bank & IDs self-edit, Roster draft controls sections), `docs/runbook.md`

**Approach:** Update in place; no new sections beyond a short "Review 2026-11-04" note listing the closed findings and reasons from the Problem Frame table.

**Test expectation:** none -- docs only.

**Verification:** Docs describe shipped behaviour; no stale "any field on file" wording remains.

---

### U6. Second review round and full verification

**Goal:** Confirm the fixes and catch anything the first review missed before push.

**Requirements:** R10

**Dependencies:** U1–U5

**Files:** none planned; fixes from this round land in the unit they belong to.

**Approach:**
- Full verification set from AGENTS.md on `test_site` (ruff, `yarn lint`, `yarn test`, `yarn build`, full Python suite, roster/profile/approvals e2e).
- `/code-review` against `origin/main` (whole feature, both axes).
- Fix real findings; list anything deferred with a reason.
- Fresh-site gate runs in CI on push (user approves push separately).

**Test expectation:** none -- review and verification only.

**Verification:** Second review has no unaddressed real findings; local suites green except documented `test_site` drift (preflight HR-manager scope test, timesheet single-run e2e).

---

## System-Wide Impact

- **Employees with `salary_mode` pre-set** move from "goes to HR" to instant fill on their first bank entry. Existing open requests are unaffected.
- **`get_my_profile` response** gains `groups`; additive.
- **Roster draft payload** gains `notes`; additive. Approval behaviour unchanged.
- **Preflight** gains one check; sites missing regional fields see a new WARN.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| R1 widens instant fills to employees HR thought were "set up" because salary mode was set | Fill gates (password, Active, recipients, HR alert, self-filled mark) still apply; identity fields were genuinely empty. |
| Notes channel breaks simulator parity with the JS mirror | Shared CASES rows written first in both suites. |
| Preflight misclassifies a field as standard vs regional | Confirm against ERPNext v16 Employee meta on a site without regional apps during U4. |

---

## Sources

- Code review results (Standards + Spec axes), this session, against `origin/main` `7de177f`.
- Origin feature plan: `docs/plans/2026-11-03-001-feat-roster-draft-controls-bank-ids-self-edit-plan.md`.
- `helixhr/api.py` (`submit_my_profile_changes`, `_roster_draft_payload`, `_roster_draft_ops`), `helixhr/roster_ops.py`, `helixhr/utils.py`, `helixhr/preflight.py`, `frontend/src/lib/profileChanges.js`.
