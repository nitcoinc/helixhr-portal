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
// server, e.g. "narrow the filters"). U13 adds queued + failed toasts and the
// "My exports" panel: a `{status: 'Queued'}` answer is already announced.
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

function onDocumentClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}
function onKeydown(event) {
  if (event.key === 'Escape') open.value = false
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))

function toggle() {
  error.value = ''
  open.value = !open.value
}

async function exportAs(format) {
  open.value = false
  error.value = ''
  notice.value = ''
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
      notice.value = "This export is large, so it's being prepared. You'll get a notification when it's ready."
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
