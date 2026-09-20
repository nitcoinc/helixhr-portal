<script setup>
import { computed, reactive, ref, onUnmounted } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

// P8-U12. Birthday and work-anniversary email, authored from the portal --
// what `docs/deployment.md`'s P4-U6 originally shipped as a Desk-only
// Email Template plus two HR Settings checkboxes now has a portal home,
// and HRMS's own checkboxes are Desk-read-only fixtures (P8-KTD8) so the
// duplicate-send hazard they created is never offered as an option.
const props = defineProps({
  celebrations: { type: Object, required: true },
  templateTokens: { type: Array, required: true },
})
const emit = defineEmits(['saved'])

const EVENTS = [
  { key: 'birthday', label: 'Birthday' },
  { key: 'work_anniversary', label: 'Work anniversary' },
]

const editing = ref('')
const form = reactive({
  subject: '',
  body: '',
  is_enabled: false,
  recipient_mode: 'All employees',
  recipients: /** @type {{name: string, employee_name: string}[]} */ ([]),
})
const formError = ref('')

function rowFor(event) {
  return props.celebrations[event] || {}
}

function edit(event) {
  editing.value = event
  const row = rowFor(event)
  Object.assign(form, {
    subject: row.subject || '',
    body: row.body || '',
    is_enabled: !!row.is_enabled,
    recipient_mode: row.recipient_mode || 'All employees',
    recipients: (row.recipients || []).map((r) => ({ name: r.employee, employee_name: r.employee_name })),
  })
  formError.value = ''
}

function cancel() {
  editing.value = ''
  formError.value = ''
}

// --- the recipient picker, for "Selected employees" -----------------------
//
// Reuses get_directory (P8-U3's own corrected search) the same way the
// project member picker does -- every employee's own company, no admin
// permission required beyond what Settings already gates this whole page on.
const memberQuery = ref('')
const memberSearch = ref('')
const MEMBER_QUERY_MIN = 2
const memberResults = createResource({
  url: 'helixhr.api.get_directory',
  makeParams: () => ({ query: memberSearch.value, limit: 20 }),
  auto: false,
})
let memberPending = null
function onMemberQuery(value) {
  clearTimeout(memberPending)
  const trimmed = value.trim()
  if (trimmed.length < MEMBER_QUERY_MIN) {
    memberSearch.value = ''
    memberResults.data = null
    return
  }
  memberPending = setTimeout(() => {
    memberSearch.value = trimmed
    memberResults.reload()
  }, 250)
}
onUnmounted(() => clearTimeout(memberPending))

const selectedIds = computed(() => form.recipients.map((r) => r.name))
const memberMatches = computed(() =>
  (memberResults.data?.people || []).filter((person) => !selectedIds.value.includes(person.name)),
)
const memberSearchActive = computed(() => memberQuery.value.trim().length >= MEMBER_QUERY_MIN)

function addRecipient(person) {
  form.recipients.push({ name: person.name, employee_name: person.employee_name })
  memberQuery.value = ''
  memberSearch.value = ''
  memberResults.data = null
}

function removeRecipient(person) {
  form.recipients = form.recipients.filter((r) => r.name !== person.name)
}

// `{{ '{{ ' + token + ' }}' }}` in the template itself trips Vue's parser --
// it scans for the *first* `}}` to close the interpolation and finds the
// inner one first. A plain function sidesteps the ambiguity.
function tokenLabel(token) {
  return `{{ ${token} }}`
}

const save = createResource({ url: 'helixhr.api.save_celebration_reminder', method: 'POST' })

async function submit() {
  formError.value = ''
  if (form.recipient_mode === 'Selected employees' && !form.recipients.length) {
    formError.value = 'Pick at least one employee, or switch back to "All employees".'
    return
  }
  try {
    await save.submit({
      event: editing.value,
      subject: form.subject,
      body: form.body,
      is_enabled: form.is_enabled ? 1 : 0,
      recipient_mode: form.recipient_mode,
      recipients: selectedIds.value,
    })
    editing.value = ''
    emit('saved')
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div class="space-y-4">
    <h2 class="label">
      Celebrations
    </h2>
    <p class="text-sm text-ink-gray-6">
      The birthday and work-anniversary email, worded and sent from here. HRMS's own reminders
      are switched off in Desk so the two can never both send.
    </p>

    <div class="space-y-3">
      <div
        v-for="event in EVENTS"
        :key="event.key"
        class="surface-card elev-1 p-4"
        data-testid="settings-celebration-row"
      >
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <p class="truncate font-medium text-ink-gray-9">
              {{ event.label }}
            </p>
            <p class="text-sm text-ink-gray-6">
              {{ rowFor(event.key).is_enabled ? 'Sending' : 'Not sending' }}
              <template v-if="rowFor(event.key).is_enabled">
                · {{ rowFor(event.key).recipient_mode === 'Selected employees'
                  ? `${(rowFor(event.key).recipients || []).length} selected`
                  : 'All employees' }}
              </template>
            </p>
          </div>
          <Button
            variant="ghost"
            :data-testid="`settings-celebration-edit-${event.key}`"
            @click="edit(event.key)"
          >
            Edit
          </Button>
        </div>

        <div
          v-if="editing === event.key"
          class="mt-4 space-y-4 border-t border-outline-gray-1 pt-4"
          data-testid="settings-celebration-form"
        >
          <FormControl
            v-model="form.is_enabled"
            type="checkbox"
            label="Send this reminder"
          />

          <p class="text-xs text-ink-gray-6">
            The email body can use:
            <code
              v-for="token in templateTokens"
              :key="token"
              class="ml-1 rounded bg-surface-gray-2 px-1.5 py-0.5"
            >{{ tokenLabel(token) }}</code>
          </p>
          <FormControl
            v-model="form.subject"
            label="Subject"
          />
          <FormControl
            v-model="form.body"
            type="textarea"
            label="Body"
          />

          <div>
            <label
              :for="`celebration-recipient-mode-${event.key}`"
              class="text-sm text-ink-gray-7"
            >
              Send to
            </label>
            <select
              :id="`celebration-recipient-mode-${event.key}`"
              v-model="form.recipient_mode"
              class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            >
              <option value="All employees">
                All employees
              </option>
              <option value="Selected employees">
                Selected employees
              </option>
            </select>
          </div>

          <div v-if="form.recipient_mode === 'Selected employees'">
            <ul
              v-if="form.recipients.length"
              class="surface-card elev-1 divide-y divide-outline-gray-1"
            >
              <li
                v-for="person in form.recipients"
                :key="person.name"
                class="flex items-center justify-between gap-3 px-3 py-2"
              >
                <span class="min-w-0 flex-1 truncate text-sm text-ink-gray-8">
                  {{ person.employee_name }}
                </span>
                <Button
                  variant="ghost"
                  @click="removeRecipient(person)"
                >
                  Remove
                </Button>
              </li>
            </ul>
            <p
              v-else
              class="text-sm text-ink-gray-6"
            >
              Nobody selected yet.
            </p>

            <div class="mt-3">
              <FormControl
                v-model="memberQuery"
                type="text"
                label="Add a person"
                placeholder="Name, employee ID or work email"
                maxlength="60"
                @input="onMemberQuery(memberQuery)"
              />
              <div
                v-if="memberSearchActive"
                class="surface-card elev-1 mt-2 divide-y divide-outline-gray-1"
              >
                <p
                  v-if="memberResults.loading"
                  class="p-2.5 text-sm text-ink-gray-6"
                >
                  Searching…
                </p>
                <ul
                  v-else-if="memberMatches.length"
                  class="divide-y divide-outline-gray-1"
                >
                  <li
                    v-for="person in memberMatches"
                    :key="person.name"
                  >
                    <button
                      type="button"
                      class="flex w-full min-w-0 items-center gap-3 p-2.5 text-left"
                      @click="addRecipient(person)"
                    >
                      <span class="min-w-0 flex-1 truncate text-sm text-ink-gray-8">
                        {{ person.employee_name }}
                      </span>
                    </button>
                  </li>
                </ul>
                <p
                  v-else
                  class="p-2.5 text-sm text-ink-gray-6"
                >
                  Nobody matches that.
                </p>
              </div>
            </div>
          </div>

          <p
            v-if="formError"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            {{ formError }}
          </p>
          <div class="flex items-center gap-2">
            <Button
              variant="solid"
              theme="blue"
              :loading="save.loading"
              @click="submit"
            >
              Save
            </Button>
            <Button
              variant="ghost"
              @click="cancel"
            >
              Cancel
            </Button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
