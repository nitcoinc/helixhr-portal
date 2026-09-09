// P2-U3 / P2-R5 / P2-R9, moved out of StatusBadge.vue in P3-U1 step 3 so the
// mapping is a plain function a unit test can hold (`<script setup>` cannot
// export). StatusBadge.vue is the only renderer of `resolveStatus`;
// StepStrip.vue imports `TONE` so the strip and the badge cannot disagree
// about a tone pair (P3-U9).

// Tone is a *pair* (ink, surface), never a hue applied to text alone. Every
// pair is one of the four measured status pairs in docs/design-system.md.
export const TONE = {
  waiting: 'bg-surface-amber-1 text-ink-amber-3',
  done: 'bg-surface-green-2 text-ink-green-3',
  sentBack: 'bg-surface-red-2 text-ink-red-4',
  resting: 'bg-surface-gray-2 text-ink-gray-7',
}

export const KINDS = ['leave', 'timesheet', 'request', 'attendance']

// `waiting: true` marks the one state per kind that names a person when the
// portal knows who: "Waiting for Priya".
const MAP = {
  leave: {
    Open: { label: 'Waiting', tone: 'waiting', waiting: true },
    Approved: { label: 'Approved', tone: 'done' },
    // P4-KTD4 / P4-R4: one HRMS status, two meanings, told apart by
    // docstatus. Unsubmitted is the recoverable send-back the employee can
    // edit and resend; submitted is the terminal rejection, which is why
    // `submitted` exists at all rather than a second status word.
    Rejected: {
      label: 'Sent back',
      tone: 'sentBack',
      submitted: { label: 'Rejected', tone: 'sentBack' },
    },
    // Leave has no Workflow (P2-KTD17), so `helixhr_stage` is what puts a
    // request with HR. Leave.vue hands the stage in under this key so the
    // word matches the other two kinds -- and so it stays distinct from the
    // legacy "Waiting for HR" defect state, which is deliberately unmapped.
    'Pending HR': { label: 'Waiting for HR', tone: 'waiting' },
    Cancelled: { label: 'Withdrawn', tone: 'resting' },
  },
  // P4-KTD1/P4-KTD2: Timesheet's send-back state is now named honestly, and
  // there is no Reject transition on it at all -- a week that must not be
  // resent would lock hours that still have to be recorded. A legacy
  // `Rejected` row the rename patch missed therefore reads as itself, in
  // resting grey, rather than being quietly re-worded.
  timesheet: {
    Draft: { label: 'Draft', tone: 'resting' },
    'Pending Approval': { label: 'Waiting', tone: 'waiting', waiting: true },
    'Pending HR': { label: 'Waiting for HR', tone: 'waiting' },
    Approved: { label: 'Approved', tone: 'done' },
    'Sent Back': { label: 'Sent back', tone: 'sentBack' },
    Cancelled: { label: 'Cancelled', tone: 'resting' },
  },
  request: {
    Open: { label: 'Open', tone: 'resting' },
    'In Progress': { label: 'In progress', tone: 'waiting' },
    Done: { label: 'Done', tone: 'done' },
    Rejected: { label: 'Sent back', tone: 'sentBack' },
  },
  // P3-U1 step 3 / P3-R15, P3-R17: Attendance Request `workflow_state`. Two
  // waiting states, and only the manager's names a person -- "Waiting for
  // HR" is where a manager handed the request over (P4-R5), whoever the
  // manager is. Approved reads "Counted" because that is when the days
  // become attendance.
  //
  // P4-KTD1: Sent Back and Rejected are now two states with two words.
  // Rejected shares the sent-back tone pair deliberately -- it is the same
  // measured (ink, surface) pair, and no fifth colour was added for it.
  attendance: {
    Draft: { label: 'Draft', tone: 'resting' },
    'Pending Manager': { label: 'Waiting', tone: 'waiting', waiting: true },
    'Pending HR': { label: 'Waiting for HR', tone: 'waiting' },
    Approved: { label: 'Counted', tone: 'done' },
    'Sent Back': { label: 'Sent back', tone: 'sentBack' },
    Rejected: { label: 'Rejected', tone: 'sentBack' },
    Cancelled: { label: 'Cancelled', tone: 'resting' },
  },
}

const WAITING_FALLBACK = { timesheet: 'Waiting for manager', attendance: 'Waiting for manager' }

/**
 * The word an employee reads for a Frappe status, and its tone pair.
 *
 * `docstatus` 2 wins over any state (P3-KTD6): cancelling an Approved
 * attendance request leaves `workflow_state` Approved, so the state alone
 * would read "Counted" on a request that counted nothing.
 *
 * `docstatus` 1 selects a state's `submitted` variant where it has one
 * (P4-KTD4): leave's Rejected is a send-back unsubmitted and a terminal
 * rejection submitted, and the badge is the only place that difference is
 * visible to the employee.
 */
export function resolveStatus({ kind = 'leave', status = '', approver = '', docstatus = null }) {
  const table = MAP[kind] || {}
  if (docstatus === 2) {
    const cancelled = table.Cancelled || { label: 'Cancelled', tone: 'resting' }
    return { label: cancelled.label, toneClass: TONE[cancelled.tone] }
  }
  let mapped = table[status]
  // An unmapped status is shown as itself rather than swallowed: a workflow
  // someone adds in Desk should look unfamiliar on screen, not invisible.
  if (!mapped) return { label: status || '—', toneClass: TONE.resting }
  if (mapped.submitted && docstatus === 1) mapped = mapped.submitted
  let label = mapped.label
  if (mapped.waiting && approver) label = `Waiting for ${approver}`
  else if (mapped.waiting && WAITING_FALLBACK[kind]) label = WAITING_FALLBACK[kind]
  return { label, toneClass: TONE[mapped.tone] || TONE.resting }
}
