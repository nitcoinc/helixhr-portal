<script setup>
// Plan 2026-10-04-004 U5 (R9-R12), moved from the Settings page's
// CelebrationsSection (P8-U12) and made per company: the "Celebrations &
// holidays" group on the Email templates page. Each (event, company) has
// its own switch, template and audience -- one row per company, absent
// meaning disabled (R1).
import { computed, reactive, ref, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, FormControl, Button } from 'frappe-ui'
import { session } from '@/lib/session'

const props = defineProps({
  company: { type: String, required: true },
})
const emit = defineEmits(['saved'])

const EVENTS = [
  { key: 'birthday', label: 'Birthday' },
  { key: 'work_anniversary', label: 'Work anniversary' },
  { key: 'holiday', label: 'Holiday' },
]
// The holiday context is the sender's own (`reminders._send_holiday_
// company`); the two celebration events share `CELEBRATION_TEMPLATE_TOKENS`.
const HOLIDAY_TOKENS = ['employee_name', 'holidays', 'company', 'logo_url', 'portal_url', 'date', 'frequency']

const route = useRoute()
const router = useRouter()

const setup = createResource({
  url: 'helixhr.api.get_celebration_setup',
  makeParams: () => ({ company: props.company }),
  auto: true,
})

// The selected event lives in the URL (`?event=birthday`), so a reload and
// a shared link land on the same editor.
const activeEvent = computed(() =>
  EVENTS.some((e) => e.key === route.query.event) ? String(route.query.event) : 'birthday',
)
watch(activeEvent, () => {
  editing.value = ''
  formError.value = ''
})

function chooseEvent(key) {
  router.replace({ query: { ...route.query, event: key } })
}

const editing = ref('')
const form = reactive({
  subject: '',
  body: '',
  is_enabled: false,
  recipient_mode: 'All employees',
  frequency: 'Weekly',
  recipients: /** @type {{name: string, employee_name: string}[]} */ ([]),
})
const formError = ref('')

function rowFor(event) {
  return setup.data?.events?.[event] || {}
}

function edit(event) {
  editing.value = event
  const row = rowFor(event)
  Object.assign(form, {
    subject: row.subject || '',
    body: row.body || '',
    is_enabled: !!row.is_enabled,
    recipient_mode: row.recipient_mode || 'All employees',
    frequency: row.frequency || 'Weekly',
    recipients: (row.recipients || []).map((r) => ({ name: r.employee, employee_name: r.employee_name })),
  })
  formError.value = ''
  refreshPreview()
}

function cancel() {
  editing.value = ''
  formError.value = ''
}

// --- the recipient picker, for "Selected employees" (R12) ------------------
//
// The server searches the selected company's active employees only, inside
// the editor's own scope -- never the whole directory.
const memberQuery = ref('')
const memberSearch = ref('')
const MEMBER_QUERY_MIN = 2
const memberResults = createResource({
  url: 'helixhr.api.search_celebration_recipients',
  makeParams: () => ({ company: props.company, query: memberSearch.value }),
  auto: false,
  transform: (rows) => ({ people: rows || [] }),
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
      company: props.company,
      frequency: editing.value === 'holiday' ? form.frequency : undefined,
    })
    editing.value = ''
    await setup.reload()
    emit('saved')
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}

// --- preview and test send (R11) -------------------------------------------

const preview = reactive({ html: '', subject: '', error: '' })
const previewResource = createResource({ url: 'helixhr.api.preview_celebration', method: 'POST' })
const testResource = createResource({ url: 'helixhr.api.send_test_celebration', method: 'POST' })
const testStatus = ref('')

async function refreshPreview() {
  if (!editing.value) return
  preview.error = ''
  try {
    const result = await previewResource.submit({
      event: editing.value,
      subject: form.subject,
      body: form.body,
      company: props.company,
    })
    preview.html = result.html
    preview.subject = result.subject
  } catch (error) {
    preview.html = ''
    preview.error = error?.messages?.[0] || 'Something went wrong.'
  }
}

async function sendTest() {
  formError.value = ''
  testStatus.value = ''
  try {
    await testResource.submit({
      event: editing.value,
      subject: form.subject,
      body: form.body,
      company: props.company,
    })
    testStatus.value = 'Test sent to your own email address.'
  } catch (error) {
    testStatus.value =
      error?.exc_type === 'RateLimitExceededError'
        ? 'Too many test emails. Wait a few minutes and try again.'
        : `Test not sent: ${error?.messages?.[0] || 'something went wrong.'}`
  }
}

defineExpose({ setup })
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="label">
        Celebrations &amp; holidays
      </h2>
      <p class="text-sm text-ink-gray-6">
        Every company decides its own birthday, anniversary and holiday email. A company
        you have not switched on sends nothing.
      </p>
    </div>

    <div
      v-if="(setup.data?.companies || []).length > 1"
      class="max-w-xs"
      data-testid="celebration-company-select"
    >
      <FormControl
        type="select"
        label="Company"
        :model-value="props.company"
        :options="(setup.data?.companies || []).map((c) => ({ label: c, value: c }))"
        @update:model-value="(value) => router.replace({ query: { ...route.query, company: value, event: 'birthday' } })"
      />
    </div>

    <div
      class="flex gap-1 overflow-x-auto"
      role="tablist"
      aria-label="Celebration events"
    >
      <button
        v-for="event in EVENTS"
        :key="event.key"
        type="button"
        role="tab"
        class="min-h-9 shrink-0 rounded-lg px-3 py-1.5 text-sm"
        :class="activeEvent === event.key ? 'bg-surface-gray-2 font-medium text-ink-gray-9' : 'text-ink-gray-7 hover:bg-surface-gray-1'"
        :aria-selected="activeEvent === event.key ? 'true' : 'false'"
        :data-testid="`email-template-celebration-${event.key}`"
        @click="chooseEvent(event.key)"
      >
        {{ event.label }}
        <span
          class="ml-1 rounded px-1.5 py-0.5 text-xs font-medium"
          :class="rowFor(event.key).is_enabled ? 'bg-surface-blue-2 text-ink-blue-2' : 'bg-surface-gray-3 text-ink-gray-8'"
        >{{ rowFor(event.key).is_enabled ? 'On' : 'Off' }}</span>
      </button>
    </div>

    <div class="surface-card elev-1 p-4">
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate font-medium text-ink-gray-9">
            {{ rowFor(activeEvent).label }}
          </p>
          <p class="text-sm text-ink-gray-6">
            {{ rowFor(activeEvent).is_enabled ? 'Sending' : 'Not sending' }}
            <template v-if="rowFor(activeEvent).is_enabled">
              · {{ rowFor(activeEvent).recipient_mode === 'Selected employees'
                ? `${(rowFor(activeEvent).recipients || []).length} selected`
                : `Everyone in ${props.company}` }}
              <template v-if="activeEvent === 'holiday' && rowFor(activeEvent).frequency">
                · {{ rowFor(activeEvent).frequency }}
              </template>
            </template>
          </p>
        </div>
        <Button
          variant="ghost"
          data-testid="celebration-edit"
          @click="edit(activeEvent)"
        >
          Edit
        </Button>
      </div>

      <div
        v-if="editing"
        class="mt-4 space-y-4 border-t border-outline-gray-1 pt-4"
        data-testid="celebration-form"
      >
        <FormControl
          v-model="form.is_enabled"
          type="checkbox"
          label="Send this reminder"
        />

        <FormControl
          v-if="editing === 'holiday'"
          v-model="form.frequency"
          type="select"
          label="How often"
          :options="[
            { label: 'Weekly (Monday)', value: 'Weekly' },
            { label: 'Monthly (the 1st)', value: 'Monthly' },
          ]"
          data-testid="celebration-frequency"
        />

        <p class="text-xs text-ink-gray-6">
          The email body can use:
          <code
            v-for="token in (editing === 'holiday' ? HOLIDAY_TOKENS : (setup.data?.template_tokens || []))"
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
            for="celebration-recipient-mode"
            class="text-sm text-ink-gray-7"
          >
            Send to
          </label>
          <select
            id="celebration-recipient-mode"
            v-model="form.recipient_mode"
            class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
          >
            <option value="All employees">
              Everyone in {{ props.company }}
            </option>
            <option value="Selected employees">
              Selected people
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
        <p
          v-if="testStatus"
          class="text-sm text-ink-gray-6"
          data-testid="celebration-test-status"
        >
          {{ testStatus }}
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
            :loading="previewResource.loading"
            @click="refreshPreview"
          >
            Preview
          </Button>
          <Button
            variant="ghost"
            :loading="testResource.loading"
            data-testid="celebration-send-test"
            @click="sendTest"
          >
            Send me a test
          </Button>
          <Button
            variant="ghost"
            @click="cancel"
          >
            Cancel
          </Button>
        </div>
        <div
          v-if="preview.html || preview.error"
          class="space-y-1"
        >
          <p class="label">
            Preview — “{{ preview.subject }}”
          </p>
          <p
            v-if="preview.error"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            {{ preview.error }}
          </p>
          <!-- The rendered HTML is HR's own template output; it shows only
			   inside a sandboxed iframe -- no scripts, same origin -- the rule
			   P8-KTD11 set for the message-template preview, carried over. -->
          <iframe
            v-if="preview.html"
            :srcdoc="preview.html"
            sandbox=""
            class="h-96 w-full rounded-lg border border-outline-gray-2 bg-surface-white"
            data-testid="celebration-preview"
          />
        </div>
      </div>
    </div>
  </div>
</template>
