<script setup>
import { onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { call } from '@/lib/api'

// Plan 2026-10-04-001 U5 (resolved decisions 3 and 14). Export is two calls:
// POST `request_export` builds the file and returns a one-time token, then a
// plain navigation to GET `download_export` saves it -- the same "real
// navigation" a phone browser needs that Payslips.vue relies on.
//
// States: idle (disabled with a reason when this caller may run but not
// export), working (inline spinner), refused (inline sentence from the
// server, e.g. "narrow the filters"). U13: a large export answers
// `{status: 'Queued'}`; the "My exports" panel then polls the caller's own
// background exports (status, rows, expiry, Download) until none is
// preparing, and announces Ready / Failed. The download is an owner-only GET.
const props = defineProps({
  reportKey: { type: String, required: true },
  canExport: { type: Boolean, default: false },
  /** `{filters, group_by, sort, hidden}` -- what the screen shows now. */
  query: { type: Object, required: true },
})

const FORMATS = [
  { value: 'xlsx', label: 'Excel (.xlsx)' },
  { value: 'csv', label: 'CSV (.csv)' },
  { value: 'pdf', label: 'PDF (.pdf)' },
]

const id = useId()
const root = ref(null)
const open = ref(false)
const working = ref('')
const error = ref('')
const notice = ref('')
const toast = ref('')

const exportsOpen = ref(false)
const myExports = ref([])
const exportsError = ref('')
const POLL_MS = 5000
let pollTimer = null

const isActive = (row) => row.status === 'Queued' || row.status === 'Running'

async function loadExports() {
  exportsError.value = ''
  try {
    const before = new Map(myExports.value.map((row) => [row.name, row.status]))
    myExports.value = (await call('helixhr.api.list_my_exports')) || []
    for (const row of myExports.value) {
      const was = before.get(row.name)
      if (was && isActive({ status: was }) && !isActive(row)) {
        toast.value = row.status === 'Ready'
          ? `${row.report_label} is ready to download.`
          : `${row.report_label} export failed. Try again.`
      }
    }
  } catch (failure) {
    exportsError.value = failure?.messages?.[0] || "Your exports didn't load. Try again."
  }
  clearTimeout(pollTimer)
  pollTimer = myExports.value.some(isActive) ? setTimeout(loadExports, POLL_MS) : null
}

function toggleExports() {
  exportsOpen.value = !exportsOpen.value
  if (exportsOpen.value) loadExports()
}

function downloadUrl(row) {
  return `/api/method/helixhr.api.download_report_export?export=${encodeURIComponent(row.name)}`
}

function onDocumentClick(event) {
  if (root.value && !root.value.contains(event.target)) {
    open.value = false
    exportsOpen.value = false
  }
}
function onKeydown(event) {
  if (event.key === 'Escape') {
    open.value = false
    exportsOpen.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  clearTimeout(pollTimer)
})

function toggle() {
  error.value = ''
  exportsOpen.value = false
  open.value = !open.value
}

async function exportAs(format) {
  open.value = false
  error.value = ''
  notice.value = ''
  toast.value = ''
  working.value = format
  try {
    const out = await call('helixhr.api.request_export', {
      report_key: props.reportKey,
      format,
      filters: props.query.filters,
      group_by: props.query.group_by,
      sort: props.query.sort,
      hidden: props.query.hidden,
    })
    if (out?.token) {
      window.location.assign(`/api/method/helixhr.api.download_export?token=${encodeURIComponent(out.token)}`)
      notice.value = `Downloading ${out.filename}.`
    } else if (out?.status === 'Queued') {
      toast.value = "This export is large, so it's being prepared. You'll get a notification when it's ready."
      exportsOpen.value = true
      loadExports()
    }
  } catch (failure) {
    error.value = failure?.messages?.[0] || "That export didn't work. Try again."
  } finally {
    working.value = ''
  }
}
</script>

<template>
  <div
    ref="root"
    class="relative"
    data-testid="export-menu"
    @keydown="onKeydown"
  >
    <button
      type="button"
      class="inline-flex min-h-11 items-center gap-2 rounded-md border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-8 enabled:cursor-pointer enabled:hover:bg-surface-gray-2 disabled:opacity-60 sm:min-h-9"
      aria-haspopup="menu"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="`${id}-menu`"
      :aria-describedby="canExport ? undefined : `${id}-why`"
      :disabled="!canExport || !!working"
      :title="canExport ? undefined : 'Your access lets you run this report, not export it.'"
      @click="toggle"
    >
      <span
        v-if="working"
        class="size-3.5 animate-spin rounded-full border-2 border-outline-gray-3 border-t-ink-gray-7"
        aria-hidden="true"
      />
      {{ working ? 'Exporting…' : 'Export' }}
    </button>
    <button
      v-if="canExport"
      type="button"
      class="ml-2 inline-flex min-h-11 cursor-pointer items-center text-sm text-blue-700 underline underline-offset-2 sm:min-h-9"
      :aria-expanded="exportsOpen ? 'true' : 'false'"
      :aria-controls="`${id}-exports`"
      data-testid="my-exports-toggle"
      @click="toggleExports"
    >
      My exports<span v-if="myExports.some(isActive)">&nbsp;(preparing…)</span>
    </button>
    <span
      v-if="!canExport"
      :id="`${id}-why`"
      class="sr-only"
    >Your access lets you run this report, not export it.</span>

    <div
      v-show="open"
      :id="`${id}-menu`"
      role="menu"
      class="surface-card elev-2 absolute right-0 z-20 mt-1 w-48 p-1"
    >
      <button
        v-for="format in FORMATS"
        :key="format.value"
        type="button"
        role="menuitem"
        class="flex min-h-11 w-full cursor-pointer items-center rounded px-3 text-left text-sm text-ink-gray-8 hover:bg-surface-gray-2 sm:min-h-9"
        @click="exportAs(format.value)"
      >
        {{ format.label }}
      </button>
    </div>

    <section
      v-show="exportsOpen"
      :id="`${id}-exports`"
      class="surface-card elev-2 absolute right-0 z-20 mt-1 w-80 max-w-[calc(100vw-2rem)] p-3"
      aria-label="My exports"
      data-testid="my-exports"
    >
      <p
        v-if="exportsError"
        class="text-sm text-ink-gray-7"
        role="alert"
      >
        {{ exportsError }}
      </p>
      <p
        v-else-if="!myExports.length"
        class="text-sm text-ink-gray-6"
      >
        Large exports you request appear here for 7 days.
      </p>
      <ul
        v-else
        class="max-h-72 space-y-2 overflow-y-auto"
      >
        <li
          v-for="row in myExports"
          :key="row.name"
          class="text-sm"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="text-ink-gray-9">{{ row.report_label }} <span class="uppercase text-ink-gray-6">{{ row.format }}</span></span>
            <a
              v-if="row.status === 'Ready'"
              :href="downloadUrl(row)"
              class="inline-flex min-h-11 items-center text-blue-700 underline underline-offset-2 sm:min-h-9"
            >Download</a>
            <span
              v-else
              class="text-ink-gray-6"
            >{{ isActive(row) ? 'Preparing…' : row.status }}</span>
          </div>
          <p class="text-xs text-ink-gray-6">
            {{ row.row_count }} rows<template v-if="row.expires_on">
              · available until {{ row.expires_on.slice(0, 10) }}
            </template>
          </p>
        </li>
      </ul>
    </section>

    <p
      v-if="toast"
      class="surface-inset mt-2 p-2 text-sm text-ink-gray-8 sm:absolute sm:right-0 sm:z-10 sm:w-72"
      role="status"
      data-testid="export-toast"
    >
      {{ toast }}
    </p>

    <p
      v-if="error"
      class="surface-alert absolute right-0 z-10 mt-1 w-72 p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      class="sr-only"
      role="status"
    >
      {{ notice }}
    </p>
  </div>
</template>
