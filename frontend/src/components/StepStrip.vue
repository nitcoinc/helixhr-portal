<script setup>
import { computed } from 'vue'
import { TONE } from '@/lib/statusBadge'

// P3-U6 / P3-R17. The steps an attendance request goes through, drawn as the
// things an employee actually wants to know: that it left, who has it now,
// and that the days count.
//
// Why a strip and not the badge alone: the badge says "Waiting for HR", which
// is true but does not say that the manager already agreed. A request whose
// manager step is done reads very differently from one that has not moved,
// and the difference is the whole reason the employee is looking.
//
// P4-KTD15. The journey is now Sent -> Manager -> Counted: an attendance
// request is approved in one step by the reports-to manager (P4-R6), so HR is
// not a step every request walks through. The HR pill is drawn only for a
// request that is -- or was -- with HR, which is the case a manager created by
// handing it over (P4-R5). A plain status line was considered and declined:
// the strip is how the employee reads *where* a request is, and the
// single-step change makes that shorter, not less useful.
//
// Every step carries its state as a word as well as a tint, so nothing here
// is colour alone (P2-R5).
const props = defineProps({
  /** `workflow_state` (P3-KTD6): Draft, Pending Manager, Pending HR,
   * Approved, Sent Back or Rejected. */
  state: { type: String, default: 'Draft' },
  /** 2 means cancelled, which outranks any state (P3-KTD6). */
  docstatus: { type: Number, default: 0 },
  /** The manager's name, so the second step names a person when we know one. */
  approver: { type: String, default: '' },
  /**
   * That this request went to HR even though it is no longer sitting there
   * -- an approved hand-over. `workflow_state` cannot say so on its own:
   * Pending HR is gone the moment HR decides. Nothing hands this in yet, so
   * the pill is drawn from the live state alone; the upgrade path is one
   * boolean on the request projection, set from the Version rows, once the
   * retrospective half is worth a query.
   */
  viaHr: { type: Boolean, default: false },
})

// The tones are the badge's (`lib/statusBadge.js`), so a step and the badge
// beside it cannot disagree about what "done" or "sent back" looks like
// (P3-U9). The one exception is the resting step: a step that has not been
// reached yet is a *hint* of a step, one shade lighter than the badge's
// resting ink, which reads as a real status.
const DONE = TONE.done
const CURRENT = TONE.waiting
const STOPPED = TONE.sentBack
const RESTING = 'bg-surface-gray-2 text-ink-gray-5'

// The two outcomes that end the journey at the step that decided it, and the
// word each one puts there. Rejected shares the sent-back tone pair on
// purpose: it is the same measured (ink, surface) pair, and P4 adds no fifth
// colour for it (P4-U4).
const STOPS_AT = {
  'Sent Back': 'Sent back',
  Rejected: 'Rejected',
}

const steps = computed(() => {
  const withHr = props.viaHr || props.state === 'Pending HR'
  const labels = ['Sent', props.approver || 'Manager', ...(withHr ? ['HR'] : []), 'Counted']

  // How far along each state is: the index of the step currently in
  // progress, or the number of steps once everything is done.
  const reached = {
    Draft: 0,
    'Pending Manager': 1,
    'Pending HR': 2,
    Approved: labels.length,
  }

  const cancelled = props.docstatus === 2
  // A decided-against request stops at the step that decided it. The stored
  // state cannot say which of the two steps that was, so it stops at the
  // manager's, which is the only one the portal itself can produce for a
  // request that never went to HR. HR's own send-back or reject names HR in
  // the reason shown beside this strip.
  const stopWord = STOPS_AT[props.state]
  const stoppedAt = stopWord ? 1 : -1
  const progress = cancelled ? -1 : (reached[props.state] ?? 0)

  return labels.map((label, index) => {
    if (index === stoppedAt) {
      return { label: stopWord, tone: STOPPED, state: stopWord.toLowerCase() }
    }
    if (stoppedAt >= 0 && index > stoppedAt) {
      return { label, tone: RESTING, state: 'not reached' }
    }
    if (cancelled) return { label, tone: RESTING, state: 'cancelled' }
    if (index < progress) return { label, tone: DONE, state: 'done' }
    if (index === progress) return { label, tone: CURRENT, state: 'in progress' }
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
