<script setup>
import { computed } from 'vue'
import { KINDS, resolveStatus } from '@/lib/statusBadge'

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
//
// The mapping itself lives in `lib/statusBadge.js` (P3-U1 step 3) so it can
// be unit-tested; this file only renders its answer.
const props = defineProps({
  /** The raw Frappe value: Leave Application status, Timesheet or
   * Attendance Request workflow_state, or HR Request status. */
  status: { type: String, default: '' },
  /** Which document it came from. The same word means different things:
   * an HR Request "Open" is untouched, a Leave Application "Open" is
   * waiting on a named person. */
  kind: {
    type: String,
    default: 'leave',
    validator: (value) => KINDS.includes(value),
  },
  /** Who it is waiting on, when the portal knows. Turns "Waiting" into
   * "Waiting for Priya" -- the single most useful word on the row. */
  approver: { type: String, default: '' },
  /** The document's docstatus, when the state alone cannot say it was
   * cancelled (P3-KTD6: a cancelled attendance request keeps its Approved
   * state). 2 renders Cancelled whatever `status` says. */
  docstatus: { type: Number, default: null },
})

const resolved = computed(() => resolveStatus(props))
const label = computed(() => resolved.value.label)
const toneClass = computed(() => resolved.value.toneClass)
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
