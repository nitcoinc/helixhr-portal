<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { createResource, Dialog, Button } from 'frappe-ui'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { formatDate, today } from '@/lib/dates'

// Plan 2026-10-22-001 U5 (R9, R10, R12, R14, R18). HR relieves a person, or
// changes the leaving already set, in the same Dialog-as-sheet RosterShiftSheet
// uses. Two steps: the form, then a confirm that says what happens and when,
// because a leaving on or before today applies at once and can't be undone
// here. Every rule -- self-relieve, the new manager, the date -- is the
// server's (`relieve_employee`), and its refusal is shown as the sentence it
// sent.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** `get_person`'s `employee` block. */
  person: { type: Object, default: null },
  /** The open leaving being changed, or null for a new one. */
  leaving: { type: Object, default: null },
  /** Active people reporting to this person now. */
  directReports: { type: Number, default: 0 },
  /** `{name, employee_name}` of everyone who could take the team. */
  people: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const step = ref('form')
const form = reactive({ leaving_date: '', reason: '', new_manager: '' })
const find = ref('')
const errors = reactive({})
const error = ref('')

const relieve = createResource({ url: 'helixhr.api.relieve_employee', method: 'POST' })

function reset() {
  step.value = 'form'
  form.leaving_date = props.leaving?.leaving_date || ''
  form.reason = props.leaving?.reason || ''
  form.new_manager = props.leaving?.new_manager || ''
  find.value = ''
  for (const key of Object.keys(errors)) delete errors[key]
  error.value = ''
}
watch(
  () => props.modelValue,
  (value) => value && reset(),
  { immediate: true },
)

const name = computed(() => props.person?.employee_name || '')
const title = computed(() => (props.leaving ? `Change leaving · ${name.value}` : `Relieve ${name.value}`))

// The leaver can't take their own team.
const candidates = computed(() => props.people.filter((option) => option.name !== props.person?.name))
const shownCandidates = computed(() => {
  const needle = find.value.trim().toLowerCase()
  const matches = needle
    ? candidates.value.filter((option) => option.employee_name.toLowerCase().includes(needle))
    : candidates.value
  // The chosen person stays in the list, so the select never shows blank.
  const chosen = candidates.value.find((option) => option.name === form.new_manager)
  return chosen && !matches.includes(chosen) ? [chosen, ...matches] : matches
})
const managerName = computed(
  () => candidates.value.find((option) => option.name === form.new_manager)?.employee_name || '',
)

const reportsLine = computed(() => {
  const count = props.directReports
  return `${count} ${count === 1 ? 'person reports' : 'people report'} to ${name.value}; choose who they report to next.`
})

// On or before today it applies now; the server decides by the company's own
// today, and its answer (`applied`) is what the toast reports.
const appliesNow = computed(() => !!form.leaving_date && form.leaving_date <= today())

function validate() {
  for (const key of Object.keys(errors)) delete errors[key]
  if (!form.leaving_date) errors.leaving_date = 'Choose the leaving date.'
  if (!form.reason.trim()) errors.reason = `Say why ${name.value} is leaving.`
  if (props.directReports > 0 && !form.new_manager) errors.new_manager = 'Choose who their team reports to next.'
  return Object.keys(errors).length === 0
}

function fieldAttrs(field) {
  return {
    id: `relieve-${field}`,
    'aria-invalid': errors[field] ? 'true' : undefined,
    'aria-describedby': [`relieve-${field}-help`, errors[field] ? `relieve-${field}-error` : '']
      .filter(Boolean)
      .join(' '),
  }
}

async function next() {
  error.value = ''
  if (!validate()) {
    await nextTick()
    document.querySelector('[data-testid="relieve-sheet"] [aria-invalid="true"]')?.focus()
    return
  }
  step.value = 'confirm'
}

async function save() {
  error.value = ''
  try {
    const result = await relieve.submit({
      employee: props.person.name,
      leaving_date: form.leaving_date,
      reason: form.reason.trim(),
      new_manager: props.directReports > 0 ? form.new_manager : undefined,
      expected_modified: props.leaving?.modified || undefined,
    })
    emit('saved', { ...result, changed: !!props.leaving })
    open.value = false
  } catch (e) {
    // Back to the form, so whatever the server refused can be fixed there.
    error.value = toPlainMessage(e?.messages?.[0] ?? e?.message) || FALLBACK_ERROR
    step.value = 'form'
  }
}

const inputClass =
  'mt-1 block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8'
const labelClass = 'text-sm text-ink-gray-7'
const helpClass = 'mt-1 text-xs text-ink-gray-5'
const errorClass = 'mt-1 text-sm text-ink-red-4'
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title, size: 'sm' }"
  >
    <template #body-content>
      <form
        class="space-y-4"
        novalidate
        data-testid="relieve-sheet"
        @submit.prevent="step === 'form' ? next() : save()"
      >
        <template v-if="step === 'form'">
          <div>
            <label
              for="relieve-leaving_date"
              :class="labelClass"
            >Leaving date</label>
            <input
              v-bind="fieldAttrs('leaving_date')"
              v-model="form.leaving_date"
              type="date"
              :min="person?.date_of_joining || undefined"
              :class="inputClass"
            >
            <p
              id="relieve-leaving_date-help"
              :class="helpClass"
            >
              The first day {{ name }} no longer has access. Today or earlier relieves them now; a later date waits until that day.
            </p>
            <p
              v-if="errors.leaving_date"
              id="relieve-leaving_date-error"
              :class="errorClass"
            >
              {{ errors.leaving_date }}
            </p>
          </div>

          <div>
            <label
              for="relieve-reason"
              :class="labelClass"
            >Reason</label>
            <textarea
              v-bind="fieldAttrs('reason')"
              v-model="form.reason"
              rows="2"
              maxlength="500"
              :class="inputClass"
            />
            <p
              id="relieve-reason-help"
              :class="helpClass"
            >
              Kept on their record.
            </p>
            <p
              v-if="errors.reason"
              id="relieve-reason-error"
              :class="errorClass"
            >
              {{ errors.reason }}
            </p>
          </div>

          <!-- A text box narrows a native select: the select stays the one
               labelled control, and a phone gets its own picker for it. -->
          <fieldset v-if="directReports > 0">
            <legend class="text-sm font-medium text-ink-gray-9">
              {{ reportsLine }}
            </legend>
            <label
              for="relieve-find"
              :class="[labelClass, 'mt-2 block']"
            >Find a person</label>
            <input
              id="relieve-find"
              v-model="find"
              type="search"
              autocomplete="off"
              maxlength="60"
              :class="inputClass"
            >
            <label
              for="relieve-new_manager"
              :class="[labelClass, 'mt-3 block']"
            >New manager</label>
            <select
              v-bind="fieldAttrs('new_manager')"
              v-model="form.new_manager"
              :class="inputClass"
            >
              <option value="">
                {{ shownCandidates.length ? 'Choose' : 'Nobody matches' }}
              </option>
              <option
                v-for="option in shownCandidates"
                :key="option.name"
                :value="option.name"
              >
                {{ option.employee_name }}
              </option>
            </select>
            <p
              id="relieve-new_manager-help"
              :class="helpClass"
            >
              Their leave, expense and shift requests go to this person from the leaving date.
            </p>
            <p
              v-if="errors.new_manager"
              id="relieve-new_manager-error"
              :class="errorClass"
            >
              {{ errors.new_manager }}
            </p>
          </fieldset>
        </template>

        <!-- The confirm step: what happens, and when, before anything saves. -->
        <div
          v-else
          class="space-y-3 text-sm text-ink-gray-8"
          data-testid="relieve-confirm"
        >
          <template v-if="appliesNow">
            <p class="font-medium text-ink-gray-9">
              {{ name }} is relieved now, with {{ formatDate(form.leaving_date) }} as their leaving date.
            </p>
            <ul class="list-disc space-y-1 pl-5">
              <li>Their status becomes Left and their login is turned off straight away.</li>
              <li v-if="directReports > 0">
                {{ directReports }} {{ directReports === 1 ? 'person' : 'people' }} who report to them will report to {{ managerName }}.
              </li>
            </ul>
            <p
              class="surface-alert p-3"
              role="alert"
            >
              This can't be undone here.
            </p>
          </template>
          <template v-else>
            <p class="font-medium text-ink-gray-9">
              {{ name }} leaves on {{ formatDate(form.leaving_date) }}.
            </p>
            <ul class="list-disc space-y-1 pl-5">
              <li>Until then nothing changes: they keep their login and their team.</li>
              <li>On {{ formatDate(form.leaving_date) }} their status becomes Left and their login is turned off.</li>
              <li v-if="directReports > 0">
                Everyone reporting to them that day will report to {{ managerName }}.
              </li>
              <li>You can change or cancel this until then.</li>
            </ul>
          </template>
        </div>

        <p
          v-if="error"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ error }}
        </p>

        <div
          class="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3"
        >
          <Button
            v-if="step === 'form'"
            variant="solid"
            theme="blue"
            type="submit"
          >
            Continue
          </Button>
          <Button
            v-else
            variant="solid"
            :theme="appliesNow ? 'red' : 'blue'"
            type="submit"
            :loading="relieve.loading"
          >
            {{ appliesNow ? `Relieve ${name} now` : leaving ? 'Save new leaving' : 'Schedule leaving' }}
          </Button>
          <Button
            variant="subtle"
            type="button"
            @click="step === 'confirm' ? (step = 'form') : (open = false)"
          >
            {{ step === 'confirm' ? 'Back' : 'Close' }}
          </Button>
        </div>
      </form>
    </template>
  </Dialog>
</template>
