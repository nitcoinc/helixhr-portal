<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Dialog, FormControl, Button } from 'frappe-ui'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { formatDate, formatDateRange } from '@/lib/dates'
import { shiftHours } from '@/lib/roster'

// Plan 2026-09-30-001 U10 / R9, R10. HR's one sheet on a roster cell, the
// same Dialog pattern as AttendanceRequestSheet: an empty cell assigns, an
// assigned cell ends, changes from a date, or cancels. Every rule -- overlap,
// cancel blocked by check-ins, a change on the first day -- is the server's
// (`assign_shift` and siblings), and its refusal is shown as the sentence it
// sent. Nothing is edited in place: the page refetches after a write.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** `{ employee, employee_name }` of the row. */
  person: { type: Object, default: null },
  /** The cell from `get_roster_week` (with the `assignment*` keys HR gets). */
  cell: { type: Object, default: null },
  shiftTypes: { type: Array, default: () => [] },
  /** The site's today, from the payload. */
  today: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const assigned = computed(() => !!props.cell?.assignment)

// 'end' | 'change' | 'cancel' for an assigned cell; unused for an empty one.
const action = ref('end')
const shiftType = ref('')
const fromDate = ref('')
const toDate = ref('')
const onDate = ref('')
const confirmCancel = ref(false)
const error = ref('')
const sending = ref(false)

function reset() {
  action.value = 'end'
  shiftType.value = props.shiftTypes[0]?.name || ''
  fromDate.value = props.cell?.date || ''
  toDate.value = ''
  onDate.value = props.cell?.date || ''
  confirmCancel.value = false
  error.value = ''
}
watch(
  () => props.modelValue,
  (value) => value && reset(),
  { immediate: true },
)
watch(action, () => {
  error.value = ''
  confirmCancel.value = false
})

const ACTIONS = [
  { value: 'end', label: 'End it' },
  { value: 'change', label: 'Change shift' },
  { value: 'cancel', label: 'Cancel it' },
]

/** The date this write takes effect on, for the today warning. */
const editedDate = computed(() => {
  if (!assigned.value) return fromDate.value
  return action.value === 'cancel' ? '' : onDate.value
})
// Check-ins already recorded today were matched to the old shift window;
// see docs/runbook.md (roster: check-in interplay).
const touchesToday = computed(
  () =>
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
  return assigned.value ? `${who}'s shift` : `Assign a shift · ${who}, ${when}`
})

const assignment = computed(() => {
  if (!assigned.value) return ''
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

function submit() {
  if (!canSubmit.value) return
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
      <form
        class="space-y-4"
        data-testid="roster-sheet"
        @submit.prevent="submit"
      >
        <dl
          v-if="assigned"
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
          <div class="flex flex-wrap gap-2">
            <button
              v-for="choice in ACTIONS"
              :key="choice.value"
              type="button"
              class="min-h-11 cursor-pointer rounded-full border px-4 text-sm font-medium transition-colors duration-200"
              :class="
                action === choice.value
                  ? 'border-field bg-field text-white'
                  : 'border-outline-gray-2 bg-surface-white text-ink-gray-8 hover:bg-surface-gray-2'
              "
              :aria-pressed="action === choice.value"
              @click="action = choice.value"
            >
              {{ choice.label }}
            </button>
          </div>
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
