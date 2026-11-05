<script setup>
// Plan 2026-10-31-001 U10 / R10-R12. A manager's draft roster request as a
// sticky bar under the grid: how many changes for how many people, the
// changes themselves (expandable, each removable), a note to HR, Submit and
// Discard. The page owns every write; this only renders the state it is
// handed and says which button was pressed.
//
// Plan 2026-11-03-001 U4 / R6, R7: each change has Edit (the page opens the
// sheet on it); Discard is "Clear all", still asking once; a change that
// skips days off lists the shifts the server split it into. Plan
// 2026-11-06-002 U4: the pill names which skip (weekends, holidays, both).
import { ref, computed, watch, useId } from 'vue'
import { Button } from 'frappe-ui'
import { blocksLabel, dayOpsOnLine, draftSummary, opLabel, skipLabel } from '@/lib/rosterRequests'

const props = defineProps({
  /** The draft's rows, in order (`employee_name` plus the operation). */
  rows: { type: Array, default: () => [] },
  /** What would refuse the saved draft now -- coverage can move under it --
   * as the server words it, `{ index, employee, message }`. */
  problems: { type: Array, default: () => [] },
  /** Plan 2026-11-04-001 U2: non-blocking run notes, same shape, already
   * naming the person. Shown under their change; they never block Submit. */
  notes: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false },
  /** The last save's refusal, naming the person (the server's sentence). */
  saveError: { type: String, default: '' },
  submitting: { type: Boolean, default: false },
  /** The last submit's refusal (e.g. someone already waiting for HR). */
  submitError: { type: String, default: '' },
})

const note = defineModel('note', { type: String, default: '' })
const emit = defineEmits(['edit', 'remove', 'submit', 'discard', 'save-note'])

const listId = useId()
const noteId = useId()
const expanded = ref(false)
const confirmDiscard = ref(false)
// Plan 2026-11-06-002 R12: Remove on a line that carries one-day changes
// takes them along, so the first press names them and the second removes.
const confirmRemove = ref(null)
watch(
  () => props.rows.length,
  (count) => {
    if (!count) confirmDiscard.value = false
    confirmRemove.value = null
  },
)

/** "no shift on 14 Mar 2031; Night on 21 Mar 2031 only", or ''. */
function carried(index) {
  return dayOpsOnLine(props.rows, index)
    .map((position) => opLabel(props.rows[position]).replace(/^No shift/, 'no shift'))
    .join('; ')
}

function remove(index) {
  if (carried(index) && confirmRemove.value !== index) {
    confirmRemove.value = index
    return
  }
  confirmRemove.value = null
  emit('remove', index)
}

const summary = computed(() => draftSummary(props.rows))
const busy = computed(() => props.saving || props.submitting)
// Submit stays live while a save runs: pressing it straight after typing the
// note blurs the note, which saves it, and a button disabled by that save
// would swallow the press. The page waits for the save before sending.
/** What the live region says: the count, or the save in flight. */
const status = computed(() => (props.saving ? `Saving… ${summary.value}` : summary.value))

function discard() {
  if (!confirmDiscard.value) {
    confirmDiscard.value = true
    return
  }
  confirmDiscard.value = false
  emit('discard')
}
</script>

<template>
  <section
    class="action-bar mt-4"
    aria-label="Draft roster request"
    data-testid="roster-draft-bar"
  >
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
      <!-- Always rendered: a live region inserted with its text already in it
           is often not announced. -->
      <p
        class="min-w-0 flex-1 text-sm font-medium text-ink-gray-9"
        role="status"
        aria-live="polite"
        data-testid="roster-draft-summary"
      >
        {{ status }}
      </p>
      <button
        type="button"
        class="inline-flex min-h-11 shrink-0 cursor-pointer items-center text-sm font-medium text-ink-blue-link underline underline-offset-2"
        :aria-expanded="expanded"
        :aria-controls="listId"
        @click="expanded = !expanded"
      >
        {{ expanded ? 'Hide changes' : 'Show changes' }}
      </button>
    </div>
    <!-- R4: the notes live in the list; say they exist while it is closed. -->
    <p
      v-if="notes.length && !expanded"
      class="mt-1 text-xs text-ink-blue-3"
      role="status"
      data-testid="roster-draft-notes-hint"
    >
      {{ notes.length === 1 ? '1 note' : `${notes.length} notes` }} on these changes. Show changes to read {{ notes.length === 1 ? 'it' : 'them' }}.
    </p>

    <div
      v-show="expanded"
      :id="listId"
      class="mt-2 max-h-[40vh] space-y-3 overflow-y-auto"
    >
      <ol
        class="space-y-1"
        data-testid="roster-draft-list"
      >
        <li
          v-for="(row, index) in rows"
          :key="index"
          class="flex items-center gap-2 text-sm"
          data-testid="roster-draft-item"
        >
          <span class="tabular w-5 shrink-0 text-right text-ink-gray-5">{{ index + 1 }}.</span>
          <span class="min-w-0 flex-1 break-words text-ink-gray-7">
            <span class="font-medium text-ink-gray-9">{{ row.employee_name || row.employee }}</span>
            · {{ opLabel(row) }}
            <span
              v-if="skipLabel(row)"
              class="ml-1 inline-block rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7 first-letter:uppercase"
              data-testid="roster-draft-skip"
            >{{ skipLabel(row) }}</span>
            <span
              v-if="skipLabel(row) && row.blocks?.length"
              class="block text-xs text-ink-gray-6"
              data-testid="roster-draft-blocks"
            >{{ blocksLabel(row.blocks) }}</span>
            <span
              v-for="item in notes.filter((n) => n.index === index)"
              :key="item.message"
              class="mt-0.5 block text-xs text-ink-blue-3"
              role="status"
              data-testid="roster-draft-note"
            >{{ item.message }}</span>
            <span
              v-if="confirmRemove === index"
              class="mt-0.5 block text-xs text-ink-red-4"
              role="alert"
              data-testid="roster-draft-remove-confirm"
            >Also removes: {{ carried(index) }}. Press Remove again to remove them all.</span>
          </span>
          <button
            type="button"
            class="inline-flex min-h-11 shrink-0 cursor-pointer items-center rounded px-2 text-sm font-medium text-ink-blue-link hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-50"
            :aria-label="`Edit change ${index + 1}: ${row.employee_name || row.employee}, ${opLabel(row)}`"
            :data-cell-key="`draft-edit|${index}`"
            :disabled="busy"
            @click="emit('edit', index)"
          >
            Edit
          </button>
          <button
            type="button"
            class="inline-flex min-h-11 shrink-0 cursor-pointer items-center rounded px-2 text-sm font-medium text-ink-red-4 hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-50"
            :aria-label="`Remove change ${index + 1}: ${row.employee_name || row.employee}, ${opLabel(row)}`"
            :disabled="busy"
            @click="remove(index)"
          >
            Remove
          </button>
        </li>
      </ol>
      <div>
        <label
          :for="noteId"
          class="text-sm text-ink-gray-7"
        >Note to HR (optional)</label>
        <textarea
          :id="noteId"
          v-model="note"
          rows="2"
          maxlength="1000"
          class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
          placeholder="Why these changes, if HR needs to know"
          @blur="emit('save-note')"
        />
      </div>
    </div>

    <div
      v-if="problems.length"
      class="mt-2 text-sm text-ink-red-4"
      data-testid="roster-draft-problems"
    >
      <p class="font-medium">
        HR would not get this as it stands:
      </p>
      <ul class="list-disc pl-5">
        <li
          v-for="problem in problems"
          :key="`${problem.index}|${problem.message}`"
        >
          Change {{ problem.index + 1 }}: {{ problem.message }}
        </li>
      </ul>
    </div>
    <p
      v-if="saveError"
      class="mt-2 text-sm font-medium text-ink-red-4"
      role="alert"
      data-testid="roster-draft-error"
    >
      Not saved. {{ saveError }}
    </p>
    <p
      v-if="submitError"
      class="mt-2 text-sm font-medium text-ink-red-4"
      role="alert"
      data-testid="roster-submit-error"
    >
      Not sent. {{ submitError }}
    </p>
    <p
      v-if="confirmDiscard"
      class="mt-2 text-sm font-medium text-ink-gray-9"
      role="alert"
    >
      Clear all {{ summary }}? This can't be undone.
    </p>

    <div class="mt-2 flex flex-wrap items-center gap-2">
      <Button
        variant="solid"
        theme="blue"
        :loading="submitting"
        :disabled="submitting || !rows.length"
        data-testid="roster-submit"
        @click="emit('submit')"
      >
        Submit for HR approval
      </Button>
      <Button
        variant="subtle"
        :theme="confirmDiscard ? 'red' : undefined"
        :disabled="busy"
        data-testid="roster-discard"
        @click="discard"
      >
        {{ confirmDiscard ? 'Yes, clear all' : 'Clear all' }}
      </Button>
      <Button
        v-if="confirmDiscard"
        variant="ghost"
        @click="confirmDiscard = false"
      >
        Keep it
      </Button>
    </div>
  </section>
</template>
