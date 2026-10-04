---
title: "fix: Requests -- per-category numbering, real picker name, My requests / To work on tabs"
type: fix
date: 2026-10-04
depth: standard
execution: code
---

# fix: Requests -- per-category numbering, real picker name, My requests / To work on tabs

## Summary

Each request category carries its own ID prefix, so an IT/Assets request is numbered `IT-REQ-2026-00001` instead of `HR-REQ-…`. The request timeline names the person who picked a request up instead of the fixed "Picked up by HR". The Requests page gains two sub-tabs: "My requests" (what I raised) and "To work on" (what is routed to me), so a handler deals with all request work from one page.

---

## Problem Frame

`HR Request` has one naming series, `HR-REQ-.YYYY.-`, for every category, so IT/Assets requests routed to `IT Team` look like HR tickets. The employee-facing timeline hardcodes "Picked up by HR" (`frontend/src/pages/Requests.vue:260`) although `picked_up_by` is stamped with the real user (`helixhr/helixhr/doctype/hr_request/hr_request.py:56-58`). Handlers work requests only from the mixed Approvals queue; the Requests page shows only the caller's own requests.

---

## Requirements

**Numbering**
- R1. A new request's ID prefix comes from its category's configured prefix; a category without one falls back to `HR-REQ`.
- R2. Counters run per prefix per year; existing request IDs never change.
- R3. HR can set a category's prefix in Settings > Categories, validated as 2–10 uppercase letters/digits/hyphens, not ending in a hyphen.

**Pickup display**
- R4. The timeline and list show the picker's full name ("Picked up by Asha Rao"); with no picker recorded, the step is omitted as today.
- R5. Other "HR" wording on request surfaces (reply labels, thread author fallback) names the handling team from `routed_to_role` (HR / IT) instead of a fixed "HR".

**Workspace tabs**
- R6. `/requests` shows "My requests" and, for a user who holds a routed role (HR Manager or IT Team), "To work on". A user with no routed role sees no tab bar.
- R7. "To work on" lists requests in the caller's company routed to a role the caller holds, excluding the caller's own requests, with filters Open / Mine (picked up by me) / Waiting on employee / Closed. Closed is off by default.
- R8. Acting on a request from "To work on" uses the existing pickup / need-info / done / reject flow and permission checks, not a second implementation.
- R9. The active tab and filter live in the URL (`?tab=work&state=…`), so a link from a notification opens the right view.

---

## Key Technical Decisions

- KTD1. Prefix stored on `HelixHR Request Category` (`name_prefix` Data field) and applied in `HRRequest.before_insert` by setting `self.naming_series = f"{prefix}-.YYYY.-"`. `before_insert` already loads the category and runs before naming; `_validate_selects` skips `naming_series`, so the Select keeps its single option. A Document Naming Rule was rejected: it is per-site data, needs one rule per category, and its counter never resets by year.
- KTD2. Prefix chosen from category, not `routed_to_role` (user decision). Two categories routed to IT can carry different prefixes; a category re-pointed later keeps its prefix for new requests and old IDs stay as they were.
- KTD3. A seed patch sets `IT-REQ` on the seeded "IT / Asset" category and `HR-REQ` on the rest, only where `name_prefix` is empty (insert-if-absent, never overwrite HR's edit). Also called from `install.after_install`.
- KTD4. Picker name resolved server-side (`frappe.utils.get_fullname`) and returned as `picked_up_by_name`; the raw user id stays off the employee projection. Employee has no read on User, so the client cannot resolve it.
- KTD5. "To work on" reuses the server query behind Approvals' request rows (`_hr_request_summaries`, `helixhr/api.py:2177`), extended with a state filter, rather than a second query. Approvals keeps showing request rows; both views read one function so counts never disagree. Today that function excludes `Waiting on Employee`; the new filter argument includes it only when asked.
- KTD6. Tab and gate use bootstrap booleans (`can_work_requests` already exists for routed-role holders; extend to HR Manager as a `can_handle_requests` flag) per the "nav gates are bootstrap booleans, never role names" rule.

---

## Implementation Units

### U1. Category prefix and per-category naming

- **Goal:** New requests take the category's prefix.
- **Requirements:** R1, R2, R3
- **Dependencies:** none
- **Files:**
  - Modify: `helixhr/helixhr/doctype/helixhr_request_category/helixhr_request_category.json` (add `name_prefix`), `helixhr_request_category.py` (validate format)
  - Modify: `helixhr/helixhr/doctype/hr_request/hr_request.py` (`before_insert` sets `naming_series`)
  - Create: `helixhr/patches/v1_0/seed_request_category_prefixes.py`; Modify: `helixhr/patches.txt`, `helixhr/install.py`
  - Modify: `helixhr/api.py` (category save/list endpoints carry `name_prefix`), `frontend/src/components/settings/` categories section (prefix input)
  - Test: `helixhr/tests/test_hr_request.py`, `helixhr/tests/test_install.py`
- **Approach:** Uppercase and validate in the category controller. Fallback to `HR-REQ` when the prefix is empty or the category is missing. Confirm the `naming_rule` "By fieldname" vs `autoname: naming_series:` mismatch in `hr_request.json` is harmless before relying on it; if it is not, align `naming_rule` to "By "Naming Series" field" in the same unit.
- **Patterns to follow:** `seed_request_categories.py` / `route_it_asset_requests.py` (insert-if-absent patch), existing category save endpoint in `api.py`.
- **Test scenarios:**
  - IT / Asset category with `IT-REQ` → new request named `IT-REQ-<year>-00001`; next HR category request named `HR-REQ-<year>-…` (independent counters).
  - Category with empty prefix → `HR-REQ-…`.
  - Prefix `it-req` saved → stored `IT-REQ`; `IT REQ!` or `IT-` refused with a plain sentence.
  - Changing a category's prefix after requests exist leaves existing names unchanged; the next request uses the new prefix.
  - Seed patch run twice does not overwrite an HR-edited prefix.
- **Verification:** New IT requests on a fresh site show `IT-REQ`; existing tests that seed `HR-REQ` names still pass.

### U2. Picker name and team-aware wording

- **Goal:** Request surfaces name the real picker and handling team.
- **Requirements:** R4, R5
- **Dependencies:** none
- **Files:**
  - Modify: `helixhr/api.py` (`_REQUEST_FIELDS` ~6053, `_requests_summary` ~6083, `_request_detail` ~6148 add `picked_up_by_name`, `handled_by_team`)
  - Modify: `frontend/src/pages/Requests.vue` (timeline ~260, list meta ~91, reply labels ~410-423 / ~642-651, thread ~720)
  - Test: `helixhr/tests/test_hr_request.py`, `frontend/tests/e2e/requests-documents.spec.ts`
- **Approach:** `handled_by_team` derives a display label from `routed_to_role` ("HR" for HR Manager, "IT" for IT Team, the role name otherwise). If the picker's user is disabled or Administrator, show the team label instead of a name.
- **Test scenarios:**
  - IT Team user picks up an IT request → employee's detail returns `picked_up_by_name` = that user's full name and timeline reads "Picked up by <name>".
  - HR request picked up by an HR Manager → name shown; reply label reads "HR replied".
  - IT request reply → "IT replied".
  - Request never picked up → no pickup step, no name key leaked.
  - Picker by Administrator → team label shown.
  - Employee projection never contains the picker's user id/email.
- **Verification:** e2e request lifecycle shows the picker name for an IT request.

### U3. "To work on" server feed

- **Goal:** One server function feeds both Approvals' request rows and the new tab.
- **Requirements:** R7, R8
- **Dependencies:** U2
- **Files:**
  - Modify: `helixhr/api.py` (`_hr_request_summaries` gains `states` argument; new whitelisted `get_request_work(state=None, limit=None, start=0)`; bootstrap `can_handle_requests`)
  - Modify: `helixhr/utils.py` (`RATE_LIMIT_POLICY` entry), `helixhr/tests/test_upload_security.py` (rate-limit mirror)
  - Test: `helixhr/tests/test_hr_request.py`, `helixhr/tests/test_scope_hardening.py`
- **Approach:** Gate on `_is_hr() or _holds_routed_role()` (never `_is_hr()` alone — that once emptied IT queues). Filter `Mine` = `picked_up_by == session.user`. HR Manager sees only `routed_to_role == "HR Manager"` rows here (it cannot act on IT rows). Returns rows with requester name, category, state, age, picker name, SLA overdue flag. Paginated with `limit`/`start`, one count query per filter.
- **Patterns to follow:** `get_my_approvals` paging and `total_is_capped`; `_holds_routed_role` gate.
- **Test scenarios:**
  - IT Team holder sees Open IT requests of own company, not HR-routed ones, not other companies.
  - HR Manager sees HR-routed requests, not IT-routed ones.
  - Caller's own request excluded from "To work on".
  - `state=waiting` returns Waiting on Employee rows; default excludes Closed (Done/Rejected).
  - `state=mine` returns only rows picked up by caller.
  - Plain Employee calling → refused with a plain sentence.
  - Approvals queue request rows unchanged after refactor (existing tests pass).
- **Verification:** Both feeds return identical rows for the same filter.

### U4. Requests page tabs

- **Goal:** Two sub-tabs on `/requests` with URL state.
- **Requirements:** R6, R8, R9
- **Dependencies:** U3
- **Files:**
  - Modify: `frontend/src/pages/Requests.vue` (tab bar, "To work on" list and filter chips, detail actions reuse Approvals' request decision component or call `act_on_approval`)
  - Modify: `frontend/src/lib/session.js` (`canHandleRequests`)
  - Modify: notification links for `request_arrival` to `/requests?tab=work`
  - Test: `frontend/tests/e2e/requests-documents.spec.ts`, `frontend/tests/e2e/approvals.spec.ts`
- **Approach:** Follow Approvals' `VIEW_TABS` + `role="tablist"` pattern (`Approvals.vue:70-80`, ~500). Detail opens the existing request decision detail (`_request_decision_detail`) so Pick up / Need info / Done / Reject buttons come from workflow transitions. Phone layout stacks chips; tabs stay a two-item segmented control.
- **Test scenarios:**
  - Plain employee: no tab bar, sees own requests.
  - IT Team user: both tabs; "To work on" lists an Open IT request; Pick up moves it to Mine and the requester's timeline shows the picker's name.
  - Reload on `?tab=work&state=waiting` restores the tab and filter.
  - Keyboard: tabs reachable and switch with arrow keys (ARIA tablist).
- **Verification:** e2e passes for employee, HR Manager and IT Team identities.

### U5. Docs

- **Goal:** Docs match the new behaviour.
- **Requirements:** R1–R9
- **Dependencies:** U1–U4
- **Files:** `docs/architecture.md` (request naming, workspace), `docs/runbook.md` (prefix setting)
- **Test expectation:** none -- documentation only.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| tabSeries counter collision if an HR-edited prefix equals another category's | Shared prefix simply shares a counter; names stay unique. Documented, not blocked. |
| Tests and e2e that hardcode `HR-REQ` names | They seed names directly; unaffected. Run full suite on a fresh site. |
| HR Manager no longer sees IT requests in "To work on" | Intended (cannot act). Read access via request detail links remains. |

---

## Sources & Research

- `helixhr/helixhr/doctype/hr_request/hr_request.py:21-58` (before_insert routing, pickup stamp)
- Frappe naming order `frappe/model/naming.py:153-195`; `before_insert` runs before naming
- `helixhr/api.py:2177` `_hr_request_summaries`, `:5791` `_may_act_on_hr_request`, `:5853` `_request_decision_detail`
- Regression test to keep green: `test_an_it_team_holders_queue_is_populated`
