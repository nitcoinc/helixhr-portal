<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Dialog, FormControl, Button } from 'frappe-ui'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { addCalendarDays, formatDate, formatDateRange } from '@/lib/dates'
import { shiftHours } from '@/lib/roster'
import { opLabel } from '@/lib/rosterRequests'
import SegmentedChips from '@/components/SegmentedChips.vue'

// Plan 2026-09-30-001 U10 / R9, R10. HR's one sheet on a roster cell, the
// same Dialog pattern as AttendanceRequestSheet: an empty cell assigns, an
// assigned cell ends, changes from a date, or cancels. Every rule -- overlap,
// cancel blocked by check-ins, a change on the first day -- is the server's
// (`assign_shift` and siblings), and its refusal is shown as the sentence it
// sent. Nothing is edited in place: the page refetches after a write.
//
// Plan 2026-10-31-001 U10 / R10: `mode="propose"` is a manager's sheet on a
// report's cell. It writes nothing to the roster: it hands one operation --
// assign a range, change from a date, end on a date; never cancel -- to the
// page's `proposeChange`, which checks it and saves the draft, and shows that
// refusal here. A cell the draft already changes opens on that operation,
// with Edit and Remove.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** `{ employee, employee_name }` of the row. */
  person: { type: Object, default: null },
  /** The cell from `get_roster_week` (with the `assignment*` keys HR gets). */
  cell: { type: Object, default: null },
  shiftTypes: { type: Array, default: () => [] },
  /** The site's today, from the payload. */
  today: { type: String, default: '' },
  /** 'hr' writes the roster; 'propose' adds to the manager's draft. */
  mode: { type: String, default: 'hr' },
  /** Propose mode: `{ index, op, label }` when the draft already changes
   * this cell (`rosterRequests.cellProposal` plus its operation). */
  proposal: { type: Object, default: null },
  /** Propose mode: `async (op, index)` -- `index` null appends. Throws an
   * Error whose message is the refusal to show. */
  proposeChange: { type: Function, default: null },
  /** Propose mode: `async (index)`, throwing the same way. */
  removeChange: { type: Function, default: null },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const proposing = computed(() => props.mode === 'propose')
// Propose mode, a cell the draft already changes: its summary first, and the
// form only after Edit.
const editing = ref(false)
const showsProposal = computed(() => proposing.value && !!props.proposal && !editing.value)
// The operation being edited, whatever cell it was opened from: an Assign
// edits as an Assign even on a day that has a shift today.
const editedOp = computed(() => (proposing.value && editing.value ? props.proposal?.op || null : null))
const assigned = computed(() =>
  editedOp.value ? editedOp.value.action !== 'Assign' : !!props.cell?.assignment,
)

// 'end' | 'change' | 'cancel' for an assigned cell; unused for an empty one.
const action = ref('end')
const shiftType = ref('')
const fromDate = ref('')
const toDate = ref('')
const onDate = ref('')
const confirmCancel = ref(false)
const error = ref('')
const sending = ref(false)

// A proposal starts tomorrow at the earliest (R13): a click on an earlier
// day of an assignment starts from tomorrow instead.
const firstDay = computed(() => {
  const date = props.cell?.date || ''
  if (!proposing.value || !props.today) return date
  const tomorrow = addCalendarDays(props.today, 1)
  return date && date > tomorrow ? date : tomorrow
})

function reset() {
  editing.value = false
  fill()
}

function fill() {
  const op = editedOp.value
  action.value = op ? (op.action === 'End' ? 'end' : 'change') : proposing.value ? 'change' : 'end'
  shiftType.value = op?.shift_type || props.shiftTypes[0]?.name || ''
  fromDate.value = op?.action === 'Assign' ? op.from_date : firstDay.value
  toDate.value = op?.action === 'Assign' ? op.to_date || '' : ''
  onDate.value = op ? (op.action === 'End' ? op.to_date : op.from_date) || firstDay.value : firstDay.value
  confirmCancel.value = false
  error.value = ''
}
watch(
  () => props.modelValue,
  (value) => value && reset(),
  { immediate: true },
)

function startEdit() {
  editing.value = true
  fill()
}
watch(action, () => {
  error.value = ''
  confirmCancel.value = false
})

const HR_ACTIONS = [
  { value: 'end', label: 'End it' },
  { value: 'change', label: 'Change shift' },
  { value: 'cancel', label: 'Cancel it' },
]
// Scope boundary: cancelling stays HR's alone.
const PROPOSE_ACTIONS = [
  { value: 'change', label: 'Change shift' },
  { value: 'end', label: 'End it' },
]
const ACTIONS = computed(() => (proposing.value ? PROPOSE_ACTIONS : HR_ACTIONS))

/** The date this write takes effect on, for the today warning. */
const editedDate = computed(() => {
  if (!assigned.value) return fromDate.value
  return action.value === 'cancel' ? '' : onDate.value
})
// Check-ins already recorded today were matched to the old shift window;
// see docs/runbook.md (roster: check-in interplay).
const touchesToday = computed(
  () =>
    !proposing.value &&
    !!props.today &&
    (editedDate.value === props.today ||
      (action.value === 'cancel' &&
        assigned.value &&
        props.cell.assignment_start <= props.today &&
        (!props.cell.assignment_end || props.cell.assignment_end >= props.today))),
)

const title = computed(() => {
  const who = props.person?.employee_name || ''
  const when = props.cell?.date ? formatDate(props.cell.date) : ''
  if (proposing.value) {
    if (props.proposal) return `Proposed change · ${who}`
    return assigned.value ? `Propose a change · ${who}` : `Propose a shift · ${who}, ${when}`
  }
  return assigned.value ? `${who}'s shift` : `Assign a shift · ${who}, ${when}`
})

const assignment = computed(() => {
  if (!assigned.value || !props.cell?.assignment) return ''
  const range = props.cell.assignment_end
    ? formatDateRange(props.cell.assignment_start, props.cell.assignment_end)
    : `From ${formatDate(props.cell.assignment_start)}, no end date`
  return range
})

const assign = createResource({ url: 'helixhr.api.assign_shift', method: 'POST' })
const end = createResource({ url: 'helixhr.api.end_shift_assignment', method: 'POST' })
const change = createResource({ url: 'helixhr.api.change_shift_assignment', method: 'POST' })
const cancel = createResource({ url: 'helixhr.api.cancel_shift_assignment', method: 'POST' })

const canSubmit = computed(() => {
  if (sending.value) return false
  if (!assigned.value) return !!shiftType.value && !!fromDate.value
  if (action.value === 'change') return !!shiftType.value && !!onDate.value
  if (action.value === 'end') return !!onDate.value
  return true
})

const submitLabel = computed(() => {
  if (proposing.value) return editing.value ? 'Save change' : 'Add to request'
  if (!assigned.value) return 'Assign shift'
  if (action.value === 'change') return 'Change shift'
  if (action.value === 'end') return 'End shift'
  return 'Cancel shift'
})

async function run(write) {
  error.value = ''
  sending.value = true
  try {
    await write()
    emit('changed')
    open.value = false
  } catch (e) {
    error.value = toPlainMessage(e?.messages?.[0] ?? e?.message) || FALLBACK_ERROR
  } finally {
    sending.value = false
  }
}

/** Propose mode: the one operation this form describes, in the draft's
 * shape. Change and End act on the cell's own assignment (or, editing, the
 * one the operation already named). */
function proposedOp() {
  const base = { employee: props.person.employee, shift_type: null, from_date: null, to_date: null, assignment: null }
  if (!assigned.value) {
    return { ...base, action: 'Assign', shift_type: shiftType.value, from_date: fromDate.value, to_date: toDate.value || null }
  }
  const target = editedOp.value ? editedOp.value.assignment : props.cell.assignment
  if (action.value === 'end') return { ...base, action: 'End', to_date: onDate.value, assignment: target }
  return { ...base, action: 'Change', shift_type: shiftType.value, from_date: onDate.value, assignment: target }
}

async function proposeOrRemove(write) {
  error.value = ''
  sending.value = true
  try {
    await write()
    open.value = false
  } catch (e) {
    error.value = e?.message || FALLBACK_ERROR
  } finally {
    sending.value = false
  }
}

function remove() {
  if (sending.value || !props.proposal || props.proposal.index < 0) return
  return proposeOrRemove(() => props.removeChange(props.proposal.index))
}

function submit() {
  if (!canSubmit.value) return
  if (proposing.value) {
    const index = editing.value ? props.proposal?.index ?? null : null
    return proposeOrRemove(() => props.proposeChange(proposedOp(), index))
  }
  if (!assigned.value) {
    return run(() =>
      assign.submit({
        employee: props.person.employee,
        shift_type: shiftType.value,
        start_date: fromDate.value,
        end_date: toDate.value || undefined,
      }),
    )
  }
  const name = props.cell.assignment
  if (action.value === 'end') {
    return run(() => end.submit({ assignment: name, end_date: onDate.value }))
  }
  if (action.value === 'change') {
    return run(() =>
      change.submit({ assignment: name, from_date: onDate.value, shift_type: shiftType.value }),
    )
  }
  // Cancel is destructive: the first press asks, the second does it.
  if (!confirmCancel.value) {
    confirmCancel.value = true
    return
  }
  return run(() => cancel.submit({ assignment: name }))
}
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title, size: 'sm' }"
  >
    <template #body-content>
      <!-- Propose mode, a cell the draft already changes: what is proposed,
           then Edit or Remove. -->
      <div
        v-if="showsProposal"
        class="space-y-4"
        data-testid="roster-sheet"
      >
        <div class="rounded border-2 border-dashed border-[color:var(--ink-gray-7)] p-3 text-sm">
          <p class="font-medium text-ink-gray-9">
            {{ proposal.label }} on {{ formatDate(cell.date) }}
          </p>
          <p
            v-if="proposal.op"
            class="mt-1 text-ink-gray-7"
          >
            {{ opLabel(proposal.op) }}
          </p>
        </div>
        <p class="text-sm text-ink-gray-6">
          In your draft. Nothing changes until HR approves it.
        </p>
        <p
          v-if="proposal.index < 0"
          class="text-sm text-ink-gray-6"
        >
          Change or remove it from the list in the draft bar.
        </p>
        <p
          v-if="error"
          class="text-sm text-ink-red-4"
          role="alert"
        >
          {{ error }}
        </p>
        <div class="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3">
          <Button
            v-if="proposal.index >= 0"
            variant="solid"
            theme="blue"
            type="button"
            :disabled="sending"
            @click="startEdit"
          >
            Edit
          </Button>
          <Button
            v-if="proposal.index >= 0"
            variant="subtle"
            theme="red"
            type="button"
            :loading="sending"
            :disabled="sending"
            @click="remove"
          >
            Remove
          </Button>
          <Button
            variant="subtle"
            type="button"
            @click="open = false"
          >
            Close
          </Button>
        </div>
      </div>
      <form
        v-else
        class="space-y-4"
        data-testid="roster-sheet"
        @submit.prevent="submit"
      >
        <dl
          v-if="assigned && assignment"
          class="text-sm"
        >
          <div class="flex justify-between gap-3">
            <dt class="text-ink-gray-6">
              {{ cell.shift_type }}
            </dt>
            <dd class="tabular text-ink-gray-9">
              {{ shiftHours(cell) }}
            </dd>
          </div>
          <div class="mt-1 flex justify-between gap-3">
            <dt class="text-ink-gray-6">
              Assigned
            </dt>
            <dd class="text-ink-gray-9">
              {{ assignment }}
            </dd>
          </div>
        </dl>

        <fieldset v-if="assigned">
          <legend class="label mb-2">
            What to do
          </legend>
          <SegmentedChips
            v-model="action"
            label="What to do"
            :options="ACTIONS"
          />
        </fieldset>

        <!-- A native select, the same reason as Settings' route picker:
             FormControl's select is a combobox in this frappe-ui. -->
        <div
          v-if="!assigned || action === 'change'"
          class="space-y-1.5"
        >
          <label
            for="roster-shift-type"
            class="text-sm text-ink-gray-7"
          >
            {{ assigned ? 'New shift' : 'Shift' }}
          </label>
          <select
            id="roster-shift-type"
            v-model="shiftType"
            class="block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            required
          >
            <option
              v-for="shift in shiftTypes"
              :key="shift.name"
              :value="shift.name"
            >
              {{ shift.name }}{{ shiftHours(shift) ? ` (${shiftHours(shift)})` : '' }}
            </option>
          </select>
        </div>

        <div
          v-if="!assigned"
          class="grid grid-cols-2 gap-3"
        >
          <FormControl
            v-model="fromDate"
            type="date"
            label="From"
            required
          />
          <FormControl
            v-model="toDate"
            type="date"
            label="To (optional)"
          />
        </div>
        <p
          v-if="!assigned && !toDate"
          class="-mt-2 text-xs text-ink-gray-5"
        >
          With no end date, the shift carries on until someone ends it.
        </p>

        <FormControl
          v-if="assigned && action === 'end'"
          v-model="onDate"
          type="date"
          label="Last day on this shift"
          required
        />
        <FormControl
          v-if="assigned && action === 'change'"
          v-model="onDate"
          type="date"
          label="New shift starts on"
          required
        />

        <p
          v-if="assigned && action === 'cancel'"
          class="text-sm text-ink-gray-7"
        >
          Cancelling removes the whole assignment, every day of it. HRMS refuses it once anyone has
          checked in against it — end it on a date instead.
        </p>

        <p
          v-if="proposing"
          class="text-sm text-ink-gray-6"
        >
          This joins your draft. Nothing changes until HR approves it.
        </p>

        <p
          v-if="touchesToday"
          class="surface-alert p-3 text-sm"
          role="status"
          data-testid="roster-today-warning"
        >
          This changes today. A check-in already made today stays matched to the shift it was made
          under, and later punches may be marked off-shift.
        </p>

        <p
          v-if="error"
          class="text-sm text-ink-red-4"
          role="alert"
        >
          {{ error }}
        </p>

        <p
          v-if="confirmCancel"
          class="text-sm font-medium text-ink-gray-9"
          role="alert"
        >
          Cancel this shift assignment? This can't be undone.
        </p>

        <div
          class="sticky bottom-0 -mx-1 flex items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3"
        >
          <Button
            variant="solid"
            :theme="assigned && action === 'cancel' ? 'red' : 'blue'"
            type="submit"
            :loading="sending"
            :disabled="!canSubmit"
          >
            {{ confirmCancel ? 'Yes, cancel it' : submitLabel }}
          </Button>
          <Button
            variant="subtle"
            type="button"
            @click="confirmCancel ? (confirmCancel = false) : (open = false)"
          >
            {{ confirmCancel ? 'Keep it' : 'Close' }}
          </Button>
        </div>
      </form>
    </template>
  </Dialog>
</template>
