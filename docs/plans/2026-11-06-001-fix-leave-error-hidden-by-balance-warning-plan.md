---
title: "fix: Leave errors hidden behind HRMS's insufficient-balance warning"
type: fix
date: 2026-11-06
depth: lightweight
---

# fix: Leave errors hidden behind HRMS's insufficient-balance warning

## Summary

`toPlainLeaveError` reads the first server message. For a leave type with `allow_negative = 1` and no allocation (every "IN-Work From Home" request), HRMS's non-fatal orange warning always comes first, so the real refusal is dropped and the user sees "You do not have enough IN-Work From Home . for these dates." Fix the selection rule in `frontend/src/lib/errorMap.js`, harden the balance pattern, and add translations for the two refusals that were hiding behind it.

---

## Problem Frame

- frappe-ui (`frappeRequest.js`) builds `error.messages` from every `_server_messages` entry in order, keeping only `.message` (the `indicator` is lost), then appends `error.message` and drops empties.
- HRMS `LeaveApplication.validate` calls `show_insufficient_balance_message()` (msgprint, `"Warning: Insufficient leave balance for Leave Type <strong>X</strong>."`) before `validate_leave_overlap`, `validate_attendance` and the portal's validate hooks. Every refusal therefore arrives as `[warning, real throw]`.
- `PATTERNS[0]` matches the warning; `stripTags` turned `</strong>.` into ` .`, giving the stray dot.

Hidden refusals seen on production (read-only replay):
- `AttendanceAlreadyMarkedError` on Approve — "Attendance for employee NP0096 is already marked for the following dates: <ul><li>…</li></ul>".
- Portal R6 grace rule on Approve — "Leave can start no earlier than 2026-11-03. For older dates, ask HR."
- HRMS `OverlapError` on Send — "Employee NP0097 has already applied for IN-Work From Home between 2026-10-27 and 2026-10-27 : <a>HR-LAP-…</a>".

---

## Requirements

- R1. A non-fatal "Warning…" message never decides the sentence while a non-warning message exists; the last non-warning message does.
- R2. Only-warning input still returns a sentence (the last warning, translated or plain) and never throws.
- R3. The balance pattern does not match text starting with "Warning:", and the captured leave type name loses trailing spaces and a trailing ".".
- R4. HRMS's attendance-already-marked refusal maps to one plain sentence.
- R5. HRMS's "has already applied for … between" overlap maps to the existing "You already have a leave request that overlaps these dates."
- R6. Unmatched messages still fall through as tag-stripped plain text; every existing `errorMap` test passes unchanged.
- R7. Callers (`pages/Approvals.vue` ×2, `components/LeaveForm.vue` ×2, `pages/Leave.vue`) need no change — confirmed during planning: all pass the raw error to `toPlainLeaveError`.

---

## Key Technical Decisions

- **Select by text prefix, not indicator.** frappe-ui discards `indicator` when it flattens `_server_messages`, so `/^\s*warning\b/i` on the plain text is the only signal left in `error.messages`. Reading `error.response` to recover indicators would couple to frappe-ui internals for no extra case.
- **Last non-warning message wins.** A `frappe.throw` ends the request, so its message is always the last server message; earlier ones are msgprints. Falls back to `error.message` / the error itself when `messages` is empty, as today.
- **Generic attendance sentence.** HRMS raises the same error for Present and Work From Home attendance on any leave type, so the sentence does not say "work from home": e.g. "Attendance is already marked for these dates, so this leave can't go ahead. Withdraw or reject it." (wording confirmed with the user's acceptance intent; final copy at implementation).
- **R6 stays out of this change.** `_refuse_backdated_past_grace` (`helixhr/events.py`) already runs only on insert or a `from_date` change, never on approver submit (its docstring's KTD3; introduced in commit `d26aa85`). The production "ask HR" on Approve is therefore either an older deploy or a path that marks `from_date` changed; see Open Questions.

---

## Implementation Units

### U1. Pick the real refusal and harden the balance pattern

**Goal:** `toPlainLeaveError` returns the throw's sentence, never the balance warning, when both are present.

**Requirements:** R1, R2, R3, R6, R7

**Dependencies:** none

**Files:**
- Modify: `frontend/src/lib/errorMap.js`
- Test: `frontend/src/lib/errorMap.test.js`

**Approach:**
- Convert each entry of `error.messages` with `toPlainMessage`, drop those whose plain text starts with "warning" (case-insensitive), take the last remaining; if none remain, take the last entry; if `messages` is empty or absent, keep today's `error.message ?? error` fallback.
- `PATTERNS[0]`: add a negative lookahead / guard so text beginning "Warning:" does not match; clean the capture with trim plus one trailing "." removed.
- Update the function's doc comment with the why (warning-first ordering from HRMS, frappe-ui drops the indicator).

**Execution note:** Test-first — add the production message orders and watch them fail before changing the selector.

**Patterns to follow:** existing `PATTERNS` comments citing plan IDs; `toPlainMessage` for flattening.

**Test scenarios** (each also asserts the result does not contain "You do not have enough"):
- `[warning, "Leave can start no earlier than 2026-11-03. For older dates, ask HR."]` → that sentence verbatim.
- `[warning, "Employee NP0097 has already applied for IN-Work From Home between 2026-10-27 and 2026-10-27 : <a href=\"x\">HR-LAP-2026-00129</a>"]` → overlap sentence (needs U2).
- `[warning, "Attendance for employee NP0096 is already marked for the following dates: <br><ul><li><a href=\"x\">04-11-2026</a></li></ul>"]` → attendance sentence (needs U2).
- `["Insufficient leave balance for Leave Type <b>Casual Leave</b>"]` → "You do not have enough Casual Leave for these dates."; existing quoted-name test still passes.
- Name with trailing dot: `["Insufficient leave balance for Leave Type <strong>Casual Leave</strong>."]` → no " ." in output.
- `[warning]` only → returns a non-empty string, does not throw.
- `[]` with `message: "Some error"` → "Some error"; `{}` → `FALLBACK_ERROR`.
- Lowercase `"warning: …"` is also skipped.

**Verification:** new and existing vitest cases green; eslint clean.

### U2. Translate the two refusals that were hiding

**Goal:** the attendance and "already applied" refusals read as plain sentences.

**Requirements:** R4, R5, R6

**Dependencies:** U1 (tests share fixtures)

**Files:**
- Modify: `frontend/src/lib/errorMap.js`
- Test: `frontend/src/lib/errorMap.test.js`

**Approach:**
- Widen the overlap pattern to also match `has already applied for .* between`.
- New pattern for `attendance for employee .* is already marked` → the generic attendance sentence.
- Place the attendance pattern before any broader pattern that could catch it; check it doesn't collide with the existing "some of those days now have attendance" mapping.

**Test scenarios:**
- Raw HRMS attendance message with `<ul><li>` list, no warning → attendance sentence.
- Raw HRMS overlap message with link, no warning → overlap sentence.
- "Some of those days now have attendance; send this to HR instead." still maps to itself.

**Verification:** vitest green; build OK.

---

## Acceptance (from the request)

- Approve with a same-day Attendance → attendance sentence (U1+U2 unit tests; manual check on Dev with a WFH Attendance Request + WFH leave on the same day).
- Approve older than the grace window → "ask HR" sentence (U1 test; see Open Questions on whether this still fires on current main).
- Duplicate send → "You already have a leave request that overlaps these dates." (U2).
- "You do not have enough IN-Work From Home" never appears for a negative-allowed type (U1 negative assertions).
- All existing `errorMap` tests pass.

---

## Scope Boundaries

- No HRMS/Frappe core change, no leave type setting change (`allow_negative` stays on), no data change.
- No caller edits (R7).
- `toPlainMessage` callers outside leave (roster, people, attendance sheet) unchanged — they read their own messages.

### Deferred to Follow-Up Work

- R6 on approval — owner decision, only if Open Question 1 shows it still fires on current code.

---

## Open Questions

1. **Does R6 fire on Approve on current main?** Code says no (`_refuse_backdated_past_grace` skips unless new or `from_date` changed). During implementation, reproduce on test_site: leave created inside grace, approved after grace passes (freeze_time or a backdated insert as HR). If it fires, report the path to the owner before changing anything; if not, Dev/production is on an older build and the owner question is already answered by KTD3.
2. Final attendance sentence copy — default above unless the owner prefers otherwise.

---

## Sources

- `frontend/src/lib/errorMap.js` (`toPlainLeaveError`, `PATTERNS`), `frontend/src/lib/api.js` (`apiRequest`).
- `frontend/node_modules/frappe-ui/src/utils/frappeRequest.js` (messages build, indicator dropped).
- `helixhr/events.py` `_refuse_pending_overdraw` (R1), `_refuse_backdated_past_grace` (R6).
