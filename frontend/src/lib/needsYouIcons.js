// Every kind `helixhr.api._get_needs_you` emits, and the glyph its row draws.
// A module, because `<script setup>` cannot export and this map has to be
// assertable: a kind with no entry falls back silently, which is how a
// sent-back *leave* rendered with the Requests icon for a whole unit.
// `icons.test.js` holds the kinds against the server's own list. Not in
// lib/icons.js, which is eager: only Home's lazy chunk needs this.
export const NEEDS_YOU_ICON = {
  timesheet_rejected: 'timesheet',
  leave_rejected: 'leave',
  request_answered: 'requests',
  approval_leave: 'approvals',
  approval_timesheet: 'approvals',
  approval_request: 'requests',
  approval_change: 'approvals',
  leave_waiting: 'leave',
  // P3-U1 step 2 / P3-R16, P3-R17: the attendance request kinds P3-U5 emits.
  approval_attendance: 'approvals',
  attendance_request_rejected: 'attendance',
  attendance_request_waiting: 'attendance',
  // P4-R4: the two terminal rejections. They sit under "Decided" (plan
  // 2026-10-17-001 KTD11) with the reason, because the decision is made.
  leave_final_rejected: 'leave',
  attendance_request_final_rejected: 'attendance',
  // KTD12: an unread status-only update on a Done or Declined request.
  request_decided: 'requests',
}
