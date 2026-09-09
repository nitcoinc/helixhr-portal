<script setup>
import { computed } from 'vue'
import { TONE } from '@/lib/statusBadge'

// P3-U6 / P3-R17. The two steps an attendance request goes through, drawn as
// the four things an employee actually wants to know: that it left, who has
// it now, that HR has it, and that the days count.
//
// Why a strip and not the badge alone: the badge says "Waiting for HR", which
// is true but does not say that the manager already agreed. A request whose
// manager step is done reads very differently from one that has not moved,
// and the difference is the whole reason the employee is looking.
//
// Every step carries its state as a word as well as a tint, so nothing here
// is colour alone (P2-R5).
const props = defineProps({
  /** `workflow_state` (P3-KTD6): Draft, Pending Manager, Pending HR,
   * Approved or Rejected. */
  state: { type: String, default: 'Draft' },
  /** 2 means cancelled, which outranks any state (P3-KTD6). */
  docstatus: { type: Number, default: 0 },
  /** The manager's name, so the second step names a person when we know one. */
  approver: { type: String, default: '' },
})

// The tones are the badge's (`lib/statusBadge.js`), so a step and the badge
// beside it cannot disagree about what "done" or "sent back" looks like
// (P3-U9). The one exception is the resting step: a step that has not been
// reached yet is a *hint* of a step, one shade lighter than the badge's
// resting ink, which reads as a real status.
const DONE = TONE.done
const CURRENT = TONE.waiting
const SENT_BACK = TONE.sentBack
const RESTING = 'bg-surface-gray-2 text-ink-gray-5'

// How far along each state is: the index of the step currently in progress,
// or the number of steps once everything is done.
const REACHED = {
  Draft: 0,
  'Pending Manager': 1,
  'Pending HR': 2,
  Approved: 4,
  Rejected: 1,
}

const steps = computed(() => {
  const labels = ['Sent', props.approver || 'Manager', 'HR', 'Counted']
  const cancelled = props.docstatus === 2
  // A sent-back request stops at the step that sent it back. The stored
  // state cannot say which of the two steps that was -- both write
  // `Rejected` -- so it stops at the manager's, which is the only send-back
  // the portal itself can produce. HR's own send-back names HR in the reason
  // shown beside this strip. Upgrade path: carry the rejecting step on the
  // request projection if HR send-backs become common.
  const rejectedAt = props.state === 'Rejected' ? 1 : -1
  const reached = cancelled ? -1 : (REACHED[props.state] ?? 0)

  return labels.map((label, index) => {
    if (index === rejectedAt) {
      return { label: 'Sent back', tone: SENT_BACK, state: 'sent back' }
    }
    if (rejectedAt >= 0 && index > rejectedAt) {
      return { label, tone: RESTING, state: 'not reached' }
    }
    if (cancelled) return { label, tone: RESTING, state: 'cancelled' }
    if (index < reached) return { label, tone: DONE, state: 'done' }
    if (index === reached) return { label, tone: CURRENT, state: 'in progress' }
    return { label, tone: RESTING, state: 'not reached' }
  })
})
</script>

<template>
  <ol
    class="flex items-stretch gap-1"
    data-testid="step-strip"
  >
    <li
      v-for="(step, index) in steps"
      :key="index"
      class="min-w-0 flex-1"
    >
      <p
        class="truncate rounded-full px-2 py-1 text-center text-xs font-medium"
        :class="step.tone"
        :data-step-state="step.state"
      >
        {{ step.label }}
      </p>
      <!-- The state as a word, for anyone who is not reading the tint. -->
      <span class="sr-only">{{ step.label }}: {{ step.state }}.</span>
    </li>
  </ol>
</template>
