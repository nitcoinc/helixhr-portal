<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { useIsDesktop } from '@/lib/useIsDesktop'

// Plan 2026-10-02-001 U10 (R15, R18). The plan calls this page
// NotificationSetup.vue; U7 already shipped the route and nav name as Email
// templates, so the page keeps that one name. Every method behind it is gated
// server-side by `_assert_can_manage_notifications` -- an HR Manager who
// reaches the URL gets AsyncState's 'forbidden' region, not the editor.

const AUDIENCES = ['Employee', 'Approver', 'HR', 'Route role', 'Security']

const isDesktop = useIsDesktop()

const setup = createResource({ url: 'helixhr.api.get_notification_setup', auto: true })
const saveResource = createResource({ url: 'helixhr.api.save_message_template', method: 'POST' })
const resetResource = createResource({ url: 'helixhr.api.reset_message_template', method: 'POST' })
const previewResource = createResource({ url: 'helixhr.api.preview_message_template', method: 'POST' })
const testResource = createResource({ url: 'helixhr.api.send_test_message', method: 'POST' })

const events = computed(() => setup.data?.events || [])
const groups = computed(() => {
  const order = [...AUDIENCES, ...new Set(events.value.map((e) => e.audience))]
  return [...new Set(order)]
    .map((audience) => ({ audience, items: events.value.filter((e) => e.audience === audience) }))
    .filter((group) => group.items.length)
})

const selectedKey = ref('')
const selected = computed(() => events.value.find((e) => e.key === selectedKey.value) || null)
const draft = reactive({ subject: '', body: '', is_enabled: true })
const errors = reactive({ subject: '', body: '', general: '' })
const status = ref('')
const preview = reactive({ html: '', subject: '', error: '' })

const dirty = computed(() => {
  const event = selected.value
  if (!event) return false
  return (
    draft.subject !== event.subject ||
    draft.body !== event.body ||
    draft.is_enabled !== (event.state !== 'Off')
  )
})

function clearMessages() {
  errors.subject = ''
  errors.body = ''
  errors.general = ''
  status.value = ''
}

function open(key) {
  const event = events.value.find((e) => e.key === key)
  if (!event) return
  selectedKey.value = key
  draft.subject = event.subject
  draft.body = event.body
  draft.is_enabled = event.state !== 'Off'
  clearMessages()
  refreshPreview()
}

// Unsaved-changes guard: switching events (or leaving the page) with an edit
// pending asks first rather than silently dropping it.
const pendingKey = ref(null)
function choose(key) {
  if (key === selectedKey.value) return
  if (dirty.value) {
    pendingKey.value = key
    return
  }
  open(key)
}
function discardAndContinue() {
  const key = pendingKey.value
  pendingKey.value = null
  if (key === '') selectedKey.value = ''
  else open(key)
}
function back() {
  if (dirty.value) pendingKey.value = ''
  else selectedKey.value = ''
}
onBeforeRouteLeave(() =>
  dirty.value ? window.confirm('You have unsaved changes to this email. Leave anyway?') : true,
)

function messageOf(error, fallback) {
  const text = error?.messages?.[0] || error?.message || fallback
  return String(text).replace(/<[^>]*>/g, '')
}

// A save refusal names its field ("Subject: ..." / "Body, line 3: ...");
// it is shown under that field and Save stays enabled.
function showRefusal(error) {
  const text = messageOf(error, 'Could not save that. Please try again.')
  if (/^Subject\b/.test(text)) errors.subject = text
  else if (/^Body\b/.test(text)) errors.body = text
  else errors.general = text
}

function payload() {
  return { template_key: selectedKey.value, subject: draft.subject, body: draft.body }
}

async function save() {
  clearMessages()
  const key = selectedKey.value
  try {
    await saveResource.submit({ ...payload(), is_enabled: draft.is_enabled ? 1 : 0 })
    await setup.reload()
    open(key)
    status.value = 'Saved.'
  } catch (error) {
    showRefusal(error)
  }
}

const confirmReset = ref(false)
async function reset() {
  clearMessages()
  const key = selectedKey.value
  try {
    await resetResource.submit({ template_key: key })
    confirmReset.value = false
    await setup.reload()
    open(key)
    status.value = 'Back to the default wording.'
  } catch (error) {
    confirmReset.value = false
    errors.general = messageOf(error, 'Could not reset that. Please try again.')
  }
}

async function refreshPreview() {
  preview.error = ''
  try {
    const result = await previewResource.submit(payload())
    preview.html = result.html
    preview.subject = result.subject
  } catch (error) {
    preview.html = ''
    preview.error = messageOf(error, 'Something went wrong.')
  }
}

async function sendTest() {
  clearMessages()
  try {
    const result = await testResource.submit(payload())
    status.value = `Test sent to ${result.sent_to}.`
  } catch (error) {
    status.value =
      error?.exc_type === 'RateLimitExceededError'
        ? 'Too many test emails. Wait a few minutes and try again.'
        : `Test not sent: ${messageOf(error, 'something went wrong.')}`
  }
}

// Variable insertion goes to whichever field last had focus, at its caret,
// and focus returns there so the keyboard user keeps typing.
const subjectInput = ref(null)
const bodyInput = ref(null)
const lastField = ref('body')
async function insert(text) {
  const field = lastField.value
  const el = field === 'subject' ? subjectInput.value : bodyInput.value
  const value = draft[field]
  const start = el?.selectionStart ?? value.length
  const end = el?.selectionEnd ?? value.length
  draft[field] = value.slice(0, start) + text + value.slice(end)
  await nextTick()
  el?.focus()
  el?.setSelectionRange(start + text.length, start + text.length)
}

function sampleText(sample) {
  if (Array.isArray(sample)) return `${sample.length} item(s)`
  if (sample === true || sample === false) return sample ? 'yes' : 'no'
  return String(sample)
}

// Copyable snippets built from this event's own variables, so every example
// is one the save validation accepts.
const examples = computed(() => {
  const variables = selected.value?.variables || []
  const shared = new Set(['company', 'portal_url', 'logo_url', 'recipient_first_name'])
  const own = variables.filter((v) => !shared.has(v.name))
  const list = own.find((v) => Array.isArray(v.sample))
  const plain = own.find((v) => !Array.isArray(v.sample))
  const snippets = ['<p>Hi {{ recipient_first_name }},</p>']
  if (plain) snippets.push(`{% if ${plain.name} %}<p>{{ ${plain.name} }}</p>{% endif %}`)
  if (list) {
    const keys = Object.keys(list.sample?.[0] || {}).slice(0, 2)
    const fields = keys.map((k) => `{{ item.${k} }}`).join(', ')
    snippets.push(`<ul>{% for item in ${list.name} %}<li>${fields}</li>{% endfor %}</ul>`)
  }
  return snippets
})
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    status.value = 'Example copied.'
  } catch {
    insert(text)
  }
}

const BADGE = {
  Default: 'bg-surface-gray-3 text-ink-gray-8',
  Custom: 'bg-surface-blue-2 text-ink-blue-2',
  Off: 'bg-surface-red-2 text-ink-red-4',
}
const showList = computed(() => isDesktop.value || !selected.value)
const showEditor = computed(() => !!selected.value)
</script>

<template>
  <div>
    <PageHeader
      title="Email templates"
      subtitle="Edit the wording of every email the portal sends."
    />

    <AsyncState
      section="email-templates"
      :resource="setup"
      :empty="!events.length"
      empty-title="No emails to edit yet"
      empty-body="The portal has no email events registered."
      skeleton="block"
      skeleton-height="h-96"
    >
      <div class="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)_minmax(0,1fr)]">
        <nav
          v-if="showList"
          aria-label="Emails"
          class="space-y-4"
          data-testid="email-template-list"
        >
          <div
            v-for="group in groups"
            :key="group.audience"
          >
            <h2 class="label mb-2">
              {{ group.audience }}
            </h2>
            <ul class="space-y-1">
              <li
                v-for="event in group.items"
                :key="event.key"
              >
                <button
                  type="button"
                  class="flex min-h-11 w-full cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-surface-gray-2"
                  :class="selectedKey === event.key ? 'bg-surface-gray-2 font-medium text-ink-gray-9' : 'text-ink-gray-7'"
                  :aria-current="selectedKey === event.key ? 'true' : undefined"
                  :data-testid="`email-template-${event.key}`"
                  @click="choose(event.key)"
                >
                  <span class="min-w-0 truncate">{{ event.label }}</span>
                  <span
                    class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium"
                    :class="BADGE[event.state]"
                    data-testid="email-template-state"
                  >{{ event.state }}</span>
                </button>
              </li>
            </ul>
          </div>
        </nav>

        <p
          v-if="isDesktop && !selected"
          class="text-sm text-ink-gray-6 lg:col-span-2"
        >
          Choose an email on the left to edit its wording.
        </p>

        <section
          v-if="showEditor"
          class="space-y-4"
          aria-labelledby="email-template-editor-title"
          data-testid="email-template-editor"
        >
          <button
            v-if="!isDesktop"
            type="button"
            class="min-h-11 text-sm font-medium text-ink-gray-7 hover:text-ink-gray-9"
            @click="back"
          >
            ← All emails
          </button>
          <div>
            <h2
              id="email-template-editor-title"
              class="type-section"
            >
              {{ selected.label }}
            </h2>
            <p class="text-sm text-ink-gray-6">
              {{ selected.audience }} · {{ selected.state }}
            </p>
          </div>

          <p
            v-if="selected.locked"
            class="surface-inset p-3 text-sm text-ink-gray-7"
          >
            Security notice: always sent, and its subject and main sentence are fixed.
            The body below is an optional extra paragraph.
          </p>

          <div>
            <label
              for="email-template-subject"
              class="mb-1 block text-sm font-medium text-ink-gray-8"
            >Subject</label>
            <input
              id="email-template-subject"
              ref="subjectInput"
              v-model="draft.subject"
              type="text"
              class="w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8 focus:border-outline-gray-4 disabled:bg-surface-gray-1"
              :disabled="selected.locked"
              :maxlength="setup.data?.subject_max"
              :aria-invalid="errors.subject ? 'true' : undefined"
              :aria-describedby="errors.subject ? 'email-template-subject-error' : undefined"
              @focus="lastField = 'subject'"
            >
            <p
              v-if="errors.subject"
              id="email-template-subject-error"
              class="mt-1 text-sm text-ink-red-4"
              role="alert"
            >
              {{ errors.subject }}
            </p>
          </div>

          <div>
            <label
              for="email-template-body"
              class="mb-1 block text-sm font-medium text-ink-gray-8"
            >Body</label>
            <textarea
              id="email-template-body"
              ref="bodyInput"
              v-model="draft.body"
              rows="10"
              class="w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 font-mono text-sm text-ink-gray-8 focus:border-outline-gray-4"
              :aria-invalid="errors.body ? 'true' : undefined"
              :aria-describedby="errors.body ? 'email-template-body-error' : undefined"
              @focus="lastField = 'body'"
            />
            <p
              v-if="errors.body"
              id="email-template-body-error"
              class="mt-1 text-sm text-ink-red-4"
              role="alert"
            >
              {{ errors.body }}
            </p>
          </div>

          <label
            v-if="!selected.locked"
            class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8"
          >
            <input
              v-model="draft.is_enabled"
              type="checkbox"
              class="size-4"
              data-testid="email-template-enabled"
            >
            Send this email (unticked switches it off)
          </label>

          <p
            v-if="errors.general"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            {{ errors.general }}
          </p>
          <p
            class="text-sm text-ink-gray-7"
            role="status"
            data-testid="email-template-status"
          >
            {{ status }}
          </p>

          <div class="flex flex-wrap items-center gap-2">
            <Button
              variant="solid"
              :loading="saveResource.loading"
              @click="save"
            >
              Save
            </Button>
            <Button
              variant="subtle"
              :loading="testResource.loading"
              @click="sendTest"
            >
              Send test to me
            </Button>
            <Button
              v-if="selected.state !== 'Default'"
              variant="ghost"
              @click="confirmReset = true"
            >
              Reset to default
            </Button>
          </div>

          <div class="space-y-2">
            <h3 class="label">
              Variables
            </h3>
            <p class="text-sm text-ink-gray-6">
              Click a variable to insert it where the cursor is. Only these names work here.
            </p>
            <ul class="space-y-1">
              <li
                v-for="variable in selected.variables"
                :key="variable.name"
                class="flex flex-wrap items-baseline gap-x-2 text-sm"
              >
                <button
                  type="button"
                  class="min-h-8 rounded bg-surface-gray-2 px-1.5 font-mono text-xs text-ink-gray-9 hover:bg-surface-gray-3"
                  :aria-label="`Insert ${variable.name}`"
                  @mousedown.prevent
                  @click="insert(`{{ ${variable.name} }}`)"
                  v-text="`{{ ${variable.name} }}`"
                />
                <span class="text-ink-gray-7">{{ variable.description }}</span>
                <span class="text-ink-gray-5">e.g. {{ sampleText(variable.sample) }}</span>
              </li>
            </ul>
            <h3 class="label pt-2">
              Examples
            </h3>
            <ul class="space-y-1">
              <li
                v-for="example in examples"
                :key="example"
                class="flex items-start gap-2"
              >
                <code class="surface-inset min-w-0 flex-1 break-all p-2 text-xs">{{ example }}</code>
                <Button
                  variant="ghost"
                  size="sm"
                  :aria-label="`Copy example ${example}`"
                  @click="copy(example)"
                >
                  Copy
                </Button>
              </li>
            </ul>
          </div>
        </section>

        <section
          v-if="showEditor"
          class="space-y-2"
          aria-label="Preview"
          data-testid="email-template-preview"
        >
          <div class="flex items-center justify-between gap-2">
            <h2 class="label">
              Preview, with sample data
            </h2>
            <Button
              variant="ghost"
              size="sm"
              :loading="previewResource.loading"
              @click="refreshPreview"
            >
              Update preview
            </Button>
          </div>
          <p
            v-if="preview.error"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            Preview failed: {{ preview.error }}
          </p>
          <template v-else>
            <p class="text-sm font-medium text-ink-gray-8">
              {{ preview.subject }}
            </p>
            <!-- KTD11: srcdoc + an empty sandbox, never v-html -- a template
                 cannot script the portal origin. -->
            <iframe
              v-if="preview.html"
              :title="`Preview of ${selected.label}`"
              sandbox=""
              :srcdoc="preview.html"
              class="h-[32rem] w-full rounded-lg border border-outline-gray-1 bg-white"
            />
            <div
              v-else
              class="h-[32rem] animate-pulse rounded-lg bg-surface-gray-2"
              aria-hidden="true"
            />
          </template>
        </section>
      </div>
    </AsyncState>

    <Dialog
      :model-value="confirmReset"
      :options="{ title: 'Reset to the default wording?', size: 'sm' }"
      @update:model-value="(value) => (confirmReset = value)"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Your wording for this email is removed and the default is sent from now on.
        </p>
        <div class="mt-4 flex items-center gap-2">
          <Button
            variant="solid"
            theme="red"
            :loading="resetResource.loading"
            @click="reset"
          >
            Reset
          </Button>
          <Button
            variant="subtle"
            @click="confirmReset = false"
          >
            Keep my wording
          </Button>
        </div>
      </template>
    </Dialog>

    <Dialog
      :model-value="pendingKey !== null"
      :options="{ title: 'Discard your changes?', size: 'sm' }"
      @update:model-value="(value) => !value && (pendingKey = null)"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          This email has edits you have not saved.
        </p>
        <div class="mt-4 flex items-center gap-2">
          <Button
            variant="solid"
            theme="red"
            @click="discardAndContinue"
          >
            Discard
          </Button>
          <Button
            variant="subtle"
            @click="pendingKey = null"
          >
            Keep editing
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
