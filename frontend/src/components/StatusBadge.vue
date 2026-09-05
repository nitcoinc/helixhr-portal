<script setup>
import { computed } from 'vue'

// P2-U3 / P2-R5 / P2-R9. One place that turns a Frappe status value into the
// word an employee reads.
//
// Five pages held their own copy of this mapping -- Leave had `statusLabel`
// and `statusTheme`, Timesheet had `badgeLabel` and `badgeTheme`, so did
// TimesheetHistory, Requests had `badgeTheme` with no label mapping at all
// and rendered the raw workflow value, and Approvals had none. They had
// already drifted: "Rejected" was "Sent back" on two pages and "Rejected" on
// a third, and a waiting timesheet said "Waiting for manager" while a
// waiting leave said "Waiting" with no name in it.
//
// Not a generic badge component (P2-R9 forbids that): it takes a domain
// status and a document kind and answers with this product's vocabulary.
// frappe-ui's own `Badge` is not used because its themes are tied to the
// `blue`/`green`/`orange` scales rather than to the measured status pairs in
// docs/design-system.md, each of which is a specific ink on a specific tinted
// surface (5.07-5.28:1).
const props = defineProps({
  /** The raw Frappe value: Leave Application status, Timesheet
   * workflow_state, or HR Request status. */
  status: { type: String, default: '' },
  /** Which document it came from. The same word means different things:
   * an HR Request "Open" is untouched, a Leave Application "Open" is
   * waiting on a named person. */
  kind: {
    type: String,
    default: 'leave',
    validator: (value) => ['leave', 'timesheet', 'request'].includes(value),
  },
  /** Who it is waiting on, when the portal knows. Turns "Waiting" into
   * "Waiting for Priya" -- the single most useful word on the row. */
  approver: { type: String, default: '' },
})

// Tone is a *pair* (ink, surface), never a hue applied to text alone. Every
// pair is one of the four measured status pairs in docs/design-system.md.
const TONE = {
  waiting: 'bg-surface-amber-1 text-ink-amber-3',
  done: 'bg-surface-green-2 text-ink-green-3',
  sentBack: 'bg-surface-red-2 text-ink-red-4',
  resting: 'bg-surface-gray-2 text-ink-gray-7',
}

const MAP = {
  leave: {
    Open: { label: 'Waiting', tone: 'waiting', waiting: true },
    Approved: { label: 'Approved', tone: 'done' },
    Rejected: { label: 'Sent back', tone: 'sentBack' },
    Cancelled: { label: 'Withdrawn', tone: 'resting' },
  },
  timesheet: {
    Draft: { label: 'Draft', tone: 'resting' },
    'Pending Approval': { label: 'Waiting', tone: 'waiting', waiting: true },
    Approved: { label: 'Approved', tone: 'done' },
    Rejected: { label: 'Sent back', tone: 'sentBack' },
    Cancelled: { label: 'Cancelled', tone: 'resting' },
  },
  request: {
    Open: { label: 'Open', tone: 'resting' },
    'In Progress': { label: 'In progress', tone: 'waiting' },
    Done: { label: 'Done', tone: 'done' },
    Rejected: { label: 'Sent back', tone: 'sentBack' },
  },
}

const entry = computed(() => MAP[props.kind]?.[props.status] || null)

const label = computed(() => {
  const mapped = entry.value
  // An unmapped status is shown as itself rather than swallowed: a workflow
  // someone adds in Desk should look unfamiliar on screen, not invisible.
  if (!mapped) return props.status || '—'
  if (mapped.waiting && props.approver) return `Waiting for ${props.approver}`
  if (mapped.waiting && props.kind === 'timesheet') return 'Waiting for manager'
  return mapped.label
})

const toneClass = computed(() => TONE[entry.value?.tone] || TONE.resting)
</script>

<template>
  <!-- The word is the status. Colour is a second, redundant channel, which is
       what keeps this off the "meaning by colour alone" list (WCAG 1.4.1):
       "Sent back" and "Approved" are distinguishable in greyscale, by anyone
       who cannot separate the two tints, and by a screen reader. -->
  <span
    class="inline-flex max-w-full items-center whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium"
    :class="toneClass"
    :data-status="status"
  >
    {{ label }}
  </span>
</template>
