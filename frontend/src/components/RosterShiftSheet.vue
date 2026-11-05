<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Dialog, FormControl, Button } from 'frappe-ui'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { addCalendarDays, formatDate, formatDateRange } from '@/lib/dates'
import { shiftHours } from '@/lib/roster'
import { blocksLabel, opLabel, skipsOffDays } from '@/lib/rosterRequests'
import { session } from '@/lib/session'
import SegmentedChips from '@/components/SegmentedChips.vue'
import RosterPeoplePicker from '@/components/RosterPeoplePicker.vue'

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
//
// Plan 2026-11-03-001 U4 / R2, R6, R8. A new Assign with an end date skips
// the person's weekly offs and holidays unless switched off; the server
// splits it (A11 -- the browser never does) and the draft bar shows the
// shifts it made. "Apply to" sends one operation for the whole team or a
// pick through `proposeBulk`; editing a draft row hides it (A10).
//
// Plan 2026-11-05-001 U5 / A3. Propose mode adds the one-day changes on an
// assigned cell ("Swap Day" / "Clear Day": the usual shift carries on the
// next day) and, on an empty cell, "Just this day": an Assign with To = From
// and no skip (KTD5). HR's own sheet is unchanged.
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
  /** Propose mode: `async (op, { whole_team } | { employees })`, throwing
   * the same way. */
  proposeBulk: { type: Function, default: null },
  /** Propose mode: open straight on the edit form (the draft bar's Edit). */
  startEditing: { type: Boolean, default: false },
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

// 'end' | 'change' | 'cancel' (HR) | 'swap' | 'clear' (propose) for an
// assigned cell; unused for an empty one.
const action = ref('end')
const shiftType = ref('')
const fromDate = ref('')
const toDate = ref('')
const onDate = ref('')
const confirmCancel = ref(false)
const error = ref('')
const sending = ref(false)
const skip = ref(true)
// Propose mode, an empty cell: one day only, To follows From.
const oneDay = ref(false)
// 'one' | 'team' | 'pick', and the picked ids for 'pick'.
const applyTo = ref('one')
const picked = ref([])
// A13: after a refusal, Save stays off until the form changes.
const refusedKey = ref('')

// A proposal starts tomorrow at the earliest (R13): a click on an earlier
// day of an assignment starts from tomorrow instead.
const firstDay = computed(() => {
  const date = props.cell?.date || ''
  if (!proposing.value || !props.today) return date
  const tomorrow = addCalendarDays(props.today, 1)
  return date && date > tomorrow ? date : tomorrow
})

function reset() {
  editing.value = proposing.value && props.startEditing && !!props.proposal
  applyTo.value = 'one'
  picked.value = []
  fill()
}

// A saved row's action as the sheet's chip.
const SHEET_ACTION = { End: 'end', Change: 'change', 'Swap Day': 'swap', 'Clear Day': 'clear' }

function fill() {
  const op = editedOp.value
  action.value = op ? SHEET_ACTION[op.action] || 'change' : proposing.value ? 'change' : 'end'
  shiftType.value = op?.shift_type || props.shiftTypes[0]?.name || ''
  fromDate.value = op?.action === 'Assign' ? op.from_date : firstDay.value
  toDate.value = op?.action === 'Assign' ? op.to_date || '' : ''
  onDate.value = op ? (op.action === 'End' ? op.to_date : op.from_date) || firstDay.value : firstDay.value
  // R1: on by default for a new Assign; an edit keeps the row's own setting.
  skip.value = op ? skipsOffDays(op) : true
  oneDay.value = op?.action === 'Assign' && !!op.to_date && op.to_date === op.from_date && !skip.value
  confirmCancel.value = false
  error.value = ''
  refusedKey.value = ''
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
watch(action, (value) => {
  error.value = ''
  confirmCancel.value = false
  // A swap to the shift the day already has is refused (SAME_SHIFT): start
  // the picker on another one.
  if (value === 'swap' && shiftType.value === props.cell?.shift_type) {
    shiftType.value = props.shiftTypes.find((shift) => shift.name !== props.cell.shift_type)?.name || shiftType.value
  }
})
// Turning "Just this day" off leaves no end date behind.
function toggleOneDay() {
  toDate.value = ''
}
/** The Assign's end date: From itself for "Just this day". */
const endDate = computed(() => (oneDay.value ? fromDate.value : toDate.value))
const ONE_DAY_ACTIONS = ['swap', 'clear']
const oneDayAction = computed(() => assigned.value && ONE_DAY_ACTIONS.includes(action.value))

const HR_ACTIONS = [
  { value: 'end', label: 'End it' },
  { value: 'change', label: 'Change shift' },
  { value: 'cancel', label: 'Cancel it' },
]
// Scope boundary: cancelling stays HR's alone.
const APPLY_TO = [
  { value: 'one', label: 'This person' },
  { value: 'team', label: 'Whole team', testid: 'roster-apply-team' },
  { value: 'pick', label: 'Pick people', testid: 'roster-apply-pick' },
]
const PROPOSE_ACTIONS = [
  { value: 'change', label: 'Change shift' },
  { value: 'swap', label: 'Different shift for one day' },
  { value: 'clear', label: 'No shift that day' },
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
    return assigned.value ? `Propose a change · ${who}, ${when}` : `Propose a shift · ${who}, ${when}`
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

// Only a new Assign with an end date can skip (R2).
const showsSkip = computed(() => proposing.value && !assigned.value && !!endDate.value && !oneDay.value)
const showsApplyTo = computed(() => proposing.value && !editing.value && !!props.proposeBulk)
const bulk = computed(() => showsApplyTo.value && applyTo.value !== 'one')
const editedBlocks = computed(() => (editing.value ? blocksLabel(props.proposal?.blocks) : ''))
const formKey = computed(() =>
  JSON.stringify([action.value, shiftType.value, fromDate.value, toDate.value, onDate.value, skip.value, oneDay.value, applyTo.value, picked.value]),
)

const canSubmit = computed(() => {
  if (sending.value) return false
  if (refusedKey.value && refusedKey.value === formKey.value) return false
  if (bulk.value && applyTo.value === 'pick' && !picked.value.length) return false
  if (!assigned.value) return !!shiftType.value && !!fromDate.value
  if (action.value === 'change' || action.value === 'swap') return !!shiftType.value && !!onDate.value
  if (action.value === 'end' || action.value === 'clear') return !!onDate.value
  return true
})

const submitLabel = computed(() => {
  if (proposing.value && editing.value) return 'Save change'
  if (bulk.value) return applyTo.value === 'team' ? 'Add for the whole team' : `Add for ${picked.value.length} ${picked.value.length === 1 ? 'person' : 'people'}`
  if (proposing.value) return 'Add to request'
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
    return {
      ...base,
      action: 'Assign',
      shift_type: shiftType.value,
      from_date: fromDate.value,
      to_date: endDate.value || null,
      skip_off_days: showsSkip.value && skip.value ? 1 : 0,
    }
  }
  const target = editedOp.value ? editedOp.value.assignment : props.cell.assignment
  if (action.value === 'end') return { ...base, action: 'End', to_date: onDate.value, assignment: target }
  if (action.value === 'swap') {
    return { ...base, action: 'Swap Day', shift_type: shiftType.value, from_date: onDate.value, assignment: target }
  }
  if (action.value === 'clear') return { ...base, action: 'Clear Day', from_date: onDate.value, assignment: target }
  return { ...base, action: 'Change', shift_type: shiftType.value, from_date: onDate.value, assignment: target }
}

async function proposeOrRemove(write) {
  error.value = ''
  refusedKey.value = ''
  sending.value = true
  try {
    await write()
    open.value = false
  } catch (e) {
    error.value = e?.message || FALLBACK_ERROR
    refusedKey.value = formKey.value
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
  if (bulk.value) {
    // Each person's own shift is the server's to find: a Change or End names
    // no assignment, so it acts on whatever covers that person's date.
    const op = { ...proposedOp(), employee: null, assignment: null }
    const target = applyTo.value === 'team' ? { whole_team: 1 } : { employees: [...picked.value] }
    return proposeOrRemove(() => props.proposeBulk(op, target))
  }
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
          <!-- Wraps: four chips do not fit one line of a small sheet. -->
          <SegmentedChips
            v-model="action"
            class="flex-wrap"
            :label="`What to do with ${person?.employee_name || ''}'s shift on ${cell?.date ? formatDate(cell.date) : ''}`"
            :options="ACTIONS"
          />
        </fieldset>

        <!-- A native select, the same reason as Settings' route picker:
             FormControl's select is a combobox in this frappe-ui. -->
        <div
          v-if="!assigned || action === 'change' || action === 'swap'"
          class="space-y-1.5"
        >
          <label
            for="roster-shift-type"
            class="text-sm text-ink-gray-7"
          >
            {{ !assigned ? 'Shift' : action === 'swap' ? 'Shift that day' : 'New shift' }}
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

        <label
          v-if="proposing && !assigned"
          class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8"
        >
          <input
            v-model="oneDay"
            type="checkbox"
            role="switch"
            class="size-5 shrink-0"
            :aria-checked="oneDay ? 'true' : 'false'"
            :disabled="sending"
            data-testid="roster-one-day"
            @change="toggleOneDay"
          >
          Just this day
        </label>
        <div
          v-if="!assigned"
          class="grid gap-3"
          :class="oneDay ? 'grid-cols-1' : 'grid-cols-2'"
        >
          <FormControl
            v-model="fromDate"
            type="date"
            :label="oneDay ? 'Day' : 'From'"
            required
          />
          <FormControl
            v-if="!oneDay"
            v-model="toDate"
            type="date"
            label="To (optional)"
          />
        </div>
        <p
          v-if="!assigned && (oneDay || !toDate)"
          class="-mt-2 text-xs text-ink-gray-5"
        >
          {{ oneDay ? 'One day only. The shift ends the same day.' : 'With no end date, the shift carries on until someone ends it.' }}
        </p>

        <div
          v-if="showsSkip"
          class="space-y-1"
        >
          <label class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8">
            <input
              v-model="skip"
              type="checkbox"
              role="switch"
              class="size-5 shrink-0"
              :aria-checked="skip ? 'true' : 'false'"
              :disabled="sending"
              data-testid="roster-skip-off-days"
            >
            Skip weekly offs and holidays
          </label>
          <p
            class="text-xs text-ink-gray-6"
            aria-live="polite"
            data-testid="roster-skip-summary"
          >
            <template v-if="sending">
              Checking days off…
            </template>
            <template v-else-if="editedBlocks">
              Saved as {{ editedBlocks }}.
            </template>
            <template v-else-if="skip">
              One shift per run of working days. Your draft lists each one once it is saved.
            </template>
            <template v-else>
              One shift over every day in the range, days off included.
            </template>
          </p>
          <p
            v-if="error"
            class="text-sm text-ink-red-4"
            role="alert"
          >
            {{ error }}
          </p>
        </div>

        <fieldset v-if="showsApplyTo">
          <legend class="label mb-2">
            Apply to
          </legend>
          <SegmentedChips
            v-model="applyTo"
            label="Apply to"
            :options="APPLY_TO"
          />
          <p
            v-if="applyTo === 'team'"
            class="mt-2 text-sm text-ink-gray-6"
          >
            Everyone who reports to you. Anyone it can't apply to is skipped and named.
          </p>
          <RosterPeoplePicker
            v-if="applyTo === 'pick'"
            v-model="picked"
            class="mt-2"
            :week-start="today || fromDate || onDate"
            :me="session.employee?.name || ''"
            :disabled="sending"
          />
        </fieldset>

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
        <template v-if="oneDayAction">
          <FormControl
            v-model="onDate"
            type="date"
            label="Day"
            required
          />
          <p class="-mt-2 text-xs text-ink-gray-5">
            {{ action === 'swap' ? 'Only this day changes.' : 'No shift this day.' }} The usual shift continues
            the next day. Tomorrow at the earliest.
          </p>
        </template>

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
          v-if="error && !showsSkip"
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
