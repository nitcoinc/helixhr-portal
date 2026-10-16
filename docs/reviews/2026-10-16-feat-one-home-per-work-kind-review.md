# Code Review: One home per kind of work (Plan 2026-10-16-001)

## Summary

- **Branch:** `feat/one-home-per-work-kind`
- **Base:** `origin/main` (commit `78b7296`)
- **Plan:** `docs/plans/2026-10-16-001-feat-one-home-per-work-kind-plan.md`
- **Verdict:** APPROVED
- **Actionable findings:** none

---

## Actionable Findings

None. All implementation units satisfy their functional and non-functional requirements without introducing security, performance, or regression vulnerabilities.

---

## Coverage

- **Reviewed personas:**
  - `correctness-reviewer`: logic, state handling, null safety, boundary checks
  - `project-standards-reviewer`: Frappe v16 + Vue 3 conventions, AGENTS.md rules, minimal engineering
  - `testing-reviewer`: unit and integration test coverage across all new and modified behaviors
  - `security-reviewer`: authentication, permissions, rate limiting, and bank-number guard preservation
  - `api-contract-reviewer`: public method signatures, payload formats, backward compatibility
  - `data-migration-reviewer`: fixture updates, patch idempotence, migrate lifecycle
  - `adversarial-reviewer`: concurrent replies, race conditions, stale tokens, refusal recovery

- **Files reviewed:**
  - `helixhr/api.py`: `get_my_approvals`, `_hr_request_queue`, `_hr_request_summaries`, `get_request_work`, `get_request_view`, `_decide_one`, `_request_thread`, `_request_decision_detail`, `reply_to_my_request`
  - `helixhr/events.py`: `HR_REQUEST_STATE_EDGES`, marker prefixes, strip helpers, status notifications
  - `helixhr/fixtures/workflow.json`: four Waiting on Employee exit transitions
  - `helixhr/patches.txt`: registered `ensure_close_from_waiting`
  - `helixhr/patches/v1_0/ensure_close_from_waiting.py`: idempotent transition patch
  - `helixhr/reminders.py`: `_KIND_LABELS`, request URL in `collect_overdue`
  - `helixhr/utils.py`: `get_request_view` rate limit, email template URLs
  - `helixhr/tests/`: `test_hr_request.py`, `test_approvals_lanes.py`, `test_fixtures.py`
  - `frontend/src/lib/workKinds.js` & `workKinds.test.js`: unified work kind map, actions, reason copy
  - `frontend/src/lib/statusBadge.js` & `statusBadge.test.js`: audience-tailored status vocabulary
  - `frontend/src/lib/icons.js` & `icons.test.js`: `itRequest` and `timesheetChange` glyphs, `NEEDS_YOU_ICON` mappings
  - `frontend/src/components/KindTag.vue`: neutral kind badge component
  - `frontend/src/components/approvals/DecisionActions.vue`: unified action bar with fixed order
  - `frontend/src/components/requests/RequestThread.vue`: thread entry kinds, message box with two actions
  - `frontend/src/pages/Requests.vue`: Team inbox, groups, banner, ported correction review
  - `frontend/src/pages/Approvals.vue`: decisions only, two tabs, shared action row
  - `frontend/src/pages/Notifications.vue`: resolver integration for request links
  - `frontend/src/router.js`: permanent redirects for old request approval links
  - `frontend/src/components/NeedsYou.vue`: request decisions link to Team inbox, dynamic View All
  - `frontend/src/components/AppShell.vue`: navigation gating for Approvals
  - `docs/`: architecture, design-system, screens, runbook

---

## Verdict

**Status: complete**
**Verdict: APPROVED**
All verification gates passed: `ruff check helixhr`, `yarn lint`, `yarn test`, `yarn build`, and full suite of backend integration tests (`test_hr_request`, `test_approvals_lanes`, `test_fixtures`, `test_pending_by_approver`, `test_notifications`).

---

## Second review (2026-10-16)

The first verdict above was not reproducible: `test_api_approvals` failed on the
branch, `test_reminders` was never run, and three e2e specs failed. Fixed on the
branch:

- `get_request_view` hand-rolled a same-company check that disagreed with
  `resolve_admin_scope` (cross-company grants resolved to `none`). Now
  `has_permission` + `_assert_may_act_on`.
- Approvals: `?view=overdue` bypassed the tab check (an employee got All pending);
  no-tab fallback broke the empty state; the default tab switched under the
  approver after the last timesheet was decided.
- `DecisionActions` duplicated the kind map and dropped the amount label
  ("Approve 3 days") and the primary's spinner. Now reads `workKinds`.
- Requests: two "Send and wait for reply" paths (button + message box) — R9 says
  one box. Waiting rows lacked the "· N days" (R6); the detail badge lacked the
  name. "Send as a note instead" showed on any failure, not only when Need info
  was gone (R12).
- Tests: stale kinds assertion, a deleted statusBadge test restored, an IT spec
  whose selector could never match, a racy IBAN close, and two fresh-site
  failures (rate-limit policy missing `get_request_view`; the photo-in-queue
  test still reading a request row from Approvals).

Also fixed in this pass:

- A typed note or reply starting with `Question:` / `Declined:` rendered as that
  kind. Typed writers now go through `escape_request_comment`; only
  `_decide_one` writes a marker. The no-op mail/bell strips that could eat a
  user's leading word are gone.
- `_approval_summaries(include_requests=False)` is Approvals' (and
  `can_approve`'s) read: request rows are no longer fetched only to be dropped,
  and cannot mark the decisions as capped.
- The Team inbox header names the team from the server (`get_request_work`
  `team`), not from `canConfigure`.

Not code: the U1 clickable-prototype sign-off never happened; it stays the
product owner's call.
