<script setup>
import { ref, computed, reactive, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { session } from '@/lib/session'
import { call, saveDocumentWithFile } from '@/lib/api'
import { formatDate, today } from '@/lib/dates'
import { groupDocuments } from '@/lib/documentGroups'

// P2-U8 step 6 / P2-R19 / P2-AE2. One session-scoped read.
//
// This page used to send its own `or_filters` to `frappe.client.get_list`:
// the browser named the company it wanted to see. It asks
// `helixhr.api.get_my_documents` instead, which resolves the employee and
// their company from the session and sends no filter at all. The rows are
// newest first; `category` splits them into the two tabs and
// `groupDocuments` cuts each tab into day / month / year buckets.
const documents = createResource({
  url: 'helixhr.api.get_my_documents',
  auto: true,
})

const route = useRoute()
const router = useRouter()

const rows = computed(() => documents.data || [])
const company = computed(() => session.employee?.company)

const TABS = [
  { name: 'important', label: 'Important', category: 'Important' },
  { name: 'general', label: 'General', category: 'General' },
]
const tab = computed(() => (route.query.tab === 'general' ? 'general' : 'important'))

function showTab(name) {
  router.replace({ query: { ...route.query, tab: name === 'important' ? undefined : name } })
}

/** Arrow keys move between the tabs, the way a `role="tablist"` is expected
 * to behave; focus follows the selection. */
function cycleTab(delta) {
  const names = TABS.map((item) => item.name)
  const next = names[(names.indexOf(tab.value) + delta + names.length) % names.length]
  showTab(next)
  nextTick(() => document.getElementById(`documents-tab-${next}`)?.focus())
}

const query = ref('')

/** The host, so a link says where it is about to send you. Anything that
 * does not parse is shown as nothing rather than as a broken string --
 * P2-U1 already refuses non-HTTP(S) schemes at the server. */
function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** Where the row opens: the uploaded (private) file, else the link. */
function hrefOf(row) {
  return row.file || row.url
}

/** "PDF", "DOCX"... from the file or link path, or '' when it has none. */
function kindOf(row) {
  let path = row.file || ''
  if (!path) {
    try {
      path = new URL(row.url).pathname
    } catch {
      return ''
    }
  }
  const match = /\.([a-z0-9]{2,5})$/i.exec(path)
  return match ? match[1].toUpperCase() : ''
}

function sourceOf(row) {
  return row.file ? 'File' : hostOf(row.url) || 'Link'
}

const matches = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return rows.value
  return rows.value.filter((row) =>
    [row.title, row.description, row.url && hostOf(row.url)]
      .filter(Boolean)
      .some((field) => field.toLowerCase().includes(needle)),
  )
})

const counts = computed(() =>
  Object.fromEntries(
    TABS.map((item) => [item.name, matches.value.filter((row) => row.category === item.category).length]),
  ),
)
const activeCategory = computed(() => TABS.find((item) => item.name === tab.value).category)
const groups = computed(() =>
  groupDocuments(
    matches.value.filter((row) => row.category === activeCategory.value),
    today(),
  ),
)
const tabIsEmpty = computed(() => !rows.value.some((row) => row.category === activeCategory.value))

// Where "Ask HR" goes: one request, with the search that found nothing
// already written into its subject.
const askHr = computed(() => ({
  name: 'Requests',
  query: {
    category: 'Other',
    subject: query.value.trim()
      ? `Looking for a document: ${query.value.trim()}`
      : 'Looking for a document',
  },
}))

// ── HR: upload, edit, delete ────────────────────────────────────────────
// `session.canManageDocuments` only decides whether the controls render;
// `save_document_link` / `delete_document_link` are the real gate. Type and
// size are checked here for a quick answer and again on the server, by
// signature.
const DOCUMENT_ACCEPT = '.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg'
const DOCUMENT_EXTENSIONS = /\.(pdf|docx|xlsx|pptx|png|jpe?g)$/i
const DOCUMENT_MAX_BYTES = 20 * 1024 * 1024

const options = createResource({ url: 'helixhr.api.get_document_admin_options' })
const companyChoices = computed(() => {
  const list = (options.data?.companies || []).map((name) => ({ label: name, value: name }))
  return options.data?.allow_global ? [{ label: 'Everyone', value: '' }, ...list] : list
})

const editorOpen = ref(false)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  name: null,
  title: '',
  category: 'Important',
  published_on: '',
  company: '',
  description: '',
  source: 'file',
  url: '',
  file: null,
  currentFile: '',
})

function openEditor(row = null) {
  formError.value = ''
  Object.assign(form, {
    name: row?.name || null,
    title: row?.title || '',
    category: row?.category || activeCategory.value,
    published_on: String(row?.published_on || today()).slice(0, 10),
    company: row ? row.company || '' : '',
    description: row?.description || '',
    source: row && !row.file ? 'link' : 'file',
    url: row?.url || '',
    file: null,
    currentFile: row?.file || '',
  })
  editorOpen.value = true
  options.fetch().then(() => {
    // A new document defaults to the first company the caller may publish
    // for (Everyone, when that is theirs).
    if (!row && !companyChoices.value.some((c) => c.value === form.company)) {
      form.company = companyChoices.value[0]?.value ?? ''
    }
  })
}

function onFilePicked(event) {
  const file = event.target.files?.[0] || null
  formError.value = ''
  if (file && !DOCUMENT_EXTENSIONS.test(file.name)) {
    formError.value = 'A document must be a PDF, a Word, Excel or PowerPoint file, or a PNG or JPEG image.'
    event.target.value = ''
    form.file = null
    return
  }
  if (file && file.size > DOCUMENT_MAX_BYTES) {
    formError.value = 'That file is bigger than 20 MB. Pick a smaller one.'
    event.target.value = ''
    form.file = null
    return
  }
  form.file = file
}

async function saveDocument() {
  formError.value = ''
  if (!form.title.trim()) {
    formError.value = 'Give the document a title.'
    return
  }
  if (form.source === 'file' && !form.file && !form.currentFile) {
    formError.value = 'Pick a file to upload.'
    return
  }
  if (form.source === 'link' && !form.url.trim()) {
    formError.value = 'Add the link, starting with https://.'
    return
  }
  const params = {
    name: form.name,
    title: form.title.trim(),
    category: form.category,
    published_on: form.published_on || null,
    company: form.company || null,
    description: form.description.trim(),
    url: form.source === 'link' ? form.url.trim() : null,
  }
  saving.value = true
  try {
    if (form.source === 'file' && form.file) await saveDocumentWithFile(form.file, params)
    else await call('helixhr.api.save_document_link', params)
    editorOpen.value = false
    await documents.reload()
    showTab(form.category === 'General' ? 'general' : 'important')
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not save the document. Please try again.'
  } finally {
    saving.value = false
  }
}

const pendingDelete = ref(null)
const deleting = ref(false)
const deleteError = ref('')

async function confirmDelete() {
  deleteError.value = ''
  deleting.value = true
  try {
    await call('helixhr.api.delete_document_link', { name: pendingDelete.value.name })
    pendingDelete.value = null
    await documents.reload()
  } catch (error) {
    deleteError.value = error?.messages?.[0] || 'Could not delete the document. Please try again.'
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div>
    <PageHeader title="Documents">
      <template
        v-if="session.canManageDocuments"
        #actions
      >
        <Button
          variant="solid"
          data-testid="documents-upload"
          @click="openEditor()"
        >
          Upload document
        </Button>
      </template>
    </PageHeader>

    <!-- Search first in the DOM, so a phone gets the control before the
         explanation; `flex-row-reverse` puts it back top-right at lg:, which
         is where the desktop artboard has it. -->
    <div class="mb-4 lg:flex lg:flex-row-reverse lg:items-center lg:justify-between lg:gap-6">
      <div class="lg:w-80 lg:shrink-0">
        <FormControl
          v-model="query"
          type="text"
          label="Search"
          placeholder="Policies and forms"
        />
      </div>
      <p class="mt-3 text-sm text-ink-gray-6 lg:mt-0">
        Policies and forms HR keeps for you. Documents open in a new tab. Missing something?
        <router-link
          class="cursor-pointer text-blue-700 underline underline-offset-2"
          :to="askHr"
        >
          Ask HR
        </router-link>.
      </p>
    </div>

    <div
      class="mb-4 flex gap-2 border-b border-outline-gray-2"
      role="tablist"
      aria-label="Document categories"
      data-testid="documents-tabs"
    >
      <button
        v-for="item in TABS"
        :id="`documents-tab-${item.name}`"
        :key="item.name"
        type="button"
        role="tab"
        aria-controls="documents-panel"
        class="-mb-px min-h-11 border-b-2 px-3 text-sm font-medium"
        :class="
          tab === item.name
            ? 'border-ink-gray-9 text-ink-gray-9'
            : 'border-transparent text-ink-gray-6 hover:text-ink-gray-9'
        "
        :aria-selected="tab === item.name"
        :tabindex="tab === item.name ? 0 : -1"
        @click="showTab(item.name)"
        @keydown.right.prevent="cycleTab(1)"
        @keydown.left.prevent="cycleTab(-1)"
      >
        {{ item.label }} ({{ counts[item.name] }})
      </button>
    </div>

    <div
      id="documents-panel"
      role="tabpanel"
      :aria-labelledby="`documents-tab-${tab}`"
    >
      <AsyncState
        section="documents"
        :resource="documents"
        :empty="rows.length === 0"
        empty-title="No documents yet"
        empty-body="HR adds handbooks, policies and forms here. Ask HR if you're looking for something."
        skeleton="row"
        :skeleton-rows="4"
      >
        <div class="space-y-6">
          <section
            v-for="group in groups"
            :key="group.key"
            :aria-label="group.label"
          >
            <h2 class="label mb-2">
              {{ group.label }}
            </h2>
            <!-- Three columns at desktop widths, one on a phone. Same card
                 either way; only how many fit on a line changes. -->
            <ul class="space-y-2 lg:grid lg:grid-cols-3 lg:gap-3 lg:space-y-0">
              <li
                v-for="row in group.rows"
                :key="row.name"
                class="surface-card elev-1 flex h-full items-start gap-1"
              >
                <a
                  :href="hrefOf(row)"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="flex min-w-0 flex-1 items-start gap-3 p-3"
                >
                  <span
                    class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-green-2 text-ink-green-3"
                    aria-hidden="true"
                  >
                    <Icon
                      :name="kindOf(row) ? 'requests' : 'documents'"
                      size="h-4 w-4"
                    />
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="block font-medium text-ink-gray-9">{{ row.title }}</span>
                    <span
                      v-if="row.description"
                      class="block text-sm text-ink-gray-6"
                    >
                      {{ row.description }}
                    </span>
                    <span class="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-ink-gray-5">
                      <Icon
                        name="chevronRight"
                        size="h-3 w-3"
                        class="shrink-0"
                      />
                      {{ sourceOf(row) }}<template v-if="kindOf(row)"> · {{ kindOf(row) }}</template>
                      · {{ formatDate(row.published_on) }}
                      · {{ row.company ? (row.company === company ? 'Your company' : row.company) : 'For everyone' }}
                      <span class="sr-only">(opens in a new tab)</span>
                    </span>
                  </span>
                </a>
                <div
                  v-if="session.canManageDocuments"
                  class="flex shrink-0 flex-col p-1"
                >
                  <Button
                    variant="ghost"
                    :aria-label="`Edit ${row.title}`"
                    @click="openEditor(row)"
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    theme="red"
                    :aria-label="`Delete ${row.title}`"
                    @click="(deleteError = ''), (pendingDelete = row)"
                  >
                    Delete
                  </Button>
                </div>
              </li>
            </ul>
          </section>

          <!-- This tab has nothing at all: its own empty state, not the
               search's "no match" (P2-R2). -->
          <p
            v-if="tabIsEmpty"
            class="surface-card p-5 text-sm text-ink-gray-6"
            :data-testid="`documents-${tab}-empty`"
          >
            {{ tab === 'important' ? 'No important documents right now.' : 'No general documents yet.' }}
            <template v-if="session.canManageDocuments">
              Use “Upload document” to add one.
            </template>
          </p>
          <!-- A search that matched nothing is not an empty catalogue, and must
               not borrow the empty state's words (P2-R2). -->
          <p
            v-else-if="!groups.length"
            class="surface-card p-5 text-sm text-ink-gray-6"
            data-testid="documents-no-match"
          >
            Nothing here matches “{{ query.trim() }}”.
            <router-link
              class="cursor-pointer text-blue-700 underline underline-offset-2"
              :to="askHr"
            >
              Ask HR
            </router-link>
            if it should be.
          </p>
        </div>
      </AsyncState>
    </div>

    <Dialog
      :model-value="editorOpen"
      :options="{ title: form.name ? 'Edit document' : 'Upload document', size: 'lg' }"
      @update:model-value="(value) => !saving && (editorOpen = value)"
    >
      <template #body-content>
        <form
          class="space-y-4"
          data-testid="document-editor"
          @submit.prevent="saveDocument"
        >
          <FormControl
            v-model="form.title"
            type="text"
            label="Title"
            maxlength="140"
            required
          />
          <div class="grid gap-4 sm:grid-cols-2">
            <div class="space-y-1.5">
              <label
                for="document-category"
                class="text-sm text-ink-gray-7"
              >Category</label>
              <!-- Native <select>, as in CategoriesSection.vue. -->
              <select
                id="document-category"
                v-model="form.category"
                class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
              >
                <option value="Important">
                  Important
                </option>
                <option value="General">
                  General
                </option>
              </select>
            </div>
            <FormControl
              v-model="form.published_on"
              type="date"
              label="Date"
            />
          </div>
          <div
            v-if="companyChoices.length > 1"
            class="space-y-1.5"
          >
            <label
              for="document-company"
              class="text-sm text-ink-gray-7"
            >Who sees it</label>
            <select
              id="document-company"
              v-model="form.company"
              class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            >
              <option
                v-for="choice in companyChoices"
                :key="choice.value"
                :value="choice.value"
              >
                {{ choice.label }}
              </option>
            </select>
          </div>
          <p
            v-else-if="companyChoices.length === 1"
            class="text-sm text-ink-gray-6"
          >
            Shown to {{ companyChoices[0].label === 'Everyone' ? 'everyone' : `everyone at ${companyChoices[0].label}` }}.
          </p>
          <FormControl
            v-model="form.description"
            type="textarea"
            label="Description (optional)"
            maxlength="1000"
          />

          <fieldset class="space-y-2">
            <legend class="text-sm text-ink-gray-7">
              Document
            </legend>
            <div class="flex flex-wrap gap-4">
              <label class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8">
                <input
                  v-model="form.source"
                  type="radio"
                  value="file"
                  name="document-source"
                >
                Upload a file
              </label>
              <label class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8">
                <input
                  v-model="form.source"
                  type="radio"
                  value="link"
                  name="document-source"
                >
                Link to a web page
              </label>
            </div>
            <div
              v-if="form.source === 'file'"
              class="space-y-1.5"
            >
              <label
                for="document-file"
                class="text-sm text-ink-gray-7"
              >File</label>
              <input
                id="document-file"
                type="file"
                :accept="DOCUMENT_ACCEPT"
                class="block w-full text-sm text-ink-gray-8"
                aria-describedby="document-file-help"
                @change="onFilePicked"
              >
              <p
                id="document-file-help"
                class="text-xs text-ink-gray-6"
              >
                PDF, Word, Excel, PowerPoint, PNG or JPEG, up to 20 MB.
                <template v-if="form.currentFile && !form.file">
                  Leave empty to keep the current file.
                </template>
              </p>
            </div>
            <FormControl
              v-else
              v-model="form.url"
              type="url"
              label="Link"
              placeholder="https://"
            />
          </fieldset>

          <p
            v-if="formError"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            {{ formError }}
          </p>
          <div class="flex flex-wrap items-center gap-2">
            <Button
              variant="solid"
              type="submit"
              :loading="saving"
            >
              {{ form.name ? 'Save changes' : 'Upload' }}
            </Button>
            <Button
              variant="subtle"
              type="button"
              :disabled="saving"
              @click="editorOpen = false"
            >
              Cancel
            </Button>
          </div>
        </form>
      </template>
    </Dialog>

    <Dialog
      :model-value="pendingDelete !== null"
      :options="{ title: 'Delete this document?', size: 'sm' }"
      @update:model-value="(value) => !value && !deleting && (pendingDelete = null)"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          “{{ pendingDelete?.title }}” is removed for everyone who can see it{{ pendingDelete?.file ? ', and its file is deleted' : '' }}.
        </p>
        <p
          v-if="deleteError"
          class="surface-alert mt-3 p-3 text-sm"
          role="alert"
        >
          {{ deleteError }}
        </p>
        <div class="mt-4 flex items-center gap-2">
          <Button
            variant="solid"
            theme="red"
            :loading="deleting"
            @click="confirmDelete"
          >
            Delete
          </Button>
          <Button
            variant="subtle"
            @click="pendingDelete = null"
          >
            Keep it
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
