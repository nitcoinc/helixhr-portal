<script setup>
import { onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { call } from '@/lib/api'

// Plan 2026-10-04-001 U12 (resolved decision 10). A saved view is the
// report's URL state (`lib/reportQuery.js` toQuery shape). The server lists
// the caller's own views plus views shared within their company; applying
// one is an ordinary run as the viewer, so a value outside the viewer's
// scope comes back as `filters_removed` -- never a wider result.
const props = defineProps({
  reportKey: { type: String, required: true },
  /** The current state as a route query (toQuery output). */
  currentQuery: { type: Object, required: true },
})
const emit = defineEmits(['apply'])

const id = useId()
const root = ref(null)
const open = ref(false)
const views = ref([])
const loading = ref(false)
const error = ref('')
const label = ref('')
const shared = ref(false)
const saving = ref(false)
const status = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    views.value = (await call('helixhr.api.list_report_views', { report_key: props.reportKey })) || []
  } catch (failure) {
    views.value = []
    error.value = failure?.messages?.[0] || "Saved views didn't load. Try again."
  } finally {
    loading.value = false
  }
}

watch(() => props.reportKey, load, { immediate: true })

function onDocumentClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}
function onKeydown(event) {
  if (event.key === 'Escape') open.value = false
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))

function apply(view) {
  open.value = false
  status.value = `Opened view ${view.label}.`
  emit('apply', view.query || {})
}

async function save() {
  error.value = ''
  if (!label.value.trim()) {
    error.value = 'Give the view a name.'
    return
  }
  saving.value = true
  try {
    await call('helixhr.api.save_report_view', {
      report_key: props.reportKey,
      label: label.value.trim(),
      query: props.currentQuery,
      visibility: shared.value ? 'Shared' : 'Private',
    })
    status.value = `Saved view ${label.value.trim()}.`
    label.value = ''
    shared.value = false
    await load()
  } catch (failure) {
    error.value = failure?.messages?.[0] || "That view didn't save. Try again."
  } finally {
    saving.value = false
  }
}

async function remove(view) {
  error.value = ''
  try {
    await call('helixhr.api.delete_report_view', { name: view.name })
    status.value = `Deleted view ${view.label}.`
    await load()
  } catch (failure) {
    error.value = failure?.messages?.[0] || "That view didn't delete. Try again."
  }
}
</script>

<template>
  <div
    ref="root"
    class="relative"
    data-testid="saved-views"
    @keydown="onKeydown"
  >
    <button
      type="button"
      class="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-md border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2 sm:min-h-9"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="`${id}-views`"
      @click="open = !open"
    >
      Saved views<span v-if="views.length">&nbsp;({{ views.length }})</span>
    </button>

    <div
      v-show="open"
      :id="`${id}-views`"
      class="surface-card elev-2 absolute right-0 z-20 mt-1 w-80 max-w-[calc(100vw-2rem)] p-3"
    >
      <p
        v-if="loading"
        class="text-sm text-ink-gray-6"
      >
        Loading views…
      </p>
      <p
        v-else-if="!views.length"
        class="text-sm text-ink-gray-6"
      >
        No saved views for this report yet.
      </p>
      <ul
        v-else
        class="max-h-60 overflow-y-auto"
        aria-label="Saved views"
      >
        <li
          v-for="view in views"
          :key="view.name"
          class="flex items-center gap-2"
        >
          <button
            type="button"
            class="flex min-h-11 flex-1 cursor-pointer flex-col items-start justify-center rounded px-2 text-left hover:bg-surface-gray-2 sm:min-h-9"
            @click="apply(view)"
          >
            <span class="text-sm text-ink-gray-9">{{ view.label }}</span>
            <span
              v-if="view.visibility === 'Shared'"
              class="text-xs text-ink-gray-6"
            >Shared{{ view.is_owner ? '' : ` by ${view.owner_name}` }}</span>
          </button>
          <button
            v-if="view.can_delete"
            type="button"
            class="min-h-11 cursor-pointer rounded px-2 text-xs text-ink-gray-6 underline underline-offset-2 hover:text-ink-gray-9 sm:min-h-9"
            :aria-label="`Delete view ${view.label}`"
            @click="remove(view)"
          >
            Delete
          </button>
        </li>
      </ul>

      <form
        class="mt-3 border-t border-outline-gray-1 pt-3"
        @submit.prevent="save"
      >
        <label
          :for="`${id}-label`"
          class="block text-xs font-medium text-ink-gray-7"
        >Save the current filters as</label>
        <input
          :id="`${id}-label`"
          v-model="label"
          type="text"
          maxlength="80"
          class="mt-1 w-full rounded-md border border-outline-gray-2 px-2 py-1.5 text-sm"
          placeholder="View name"
        >
        <label class="mt-2 flex min-h-9 cursor-pointer items-center gap-2 text-sm text-ink-gray-8">
          <input
            v-model="shared"
            type="checkbox"
            class="size-4"
          >
          Share with my company
        </label>
        <button
          type="submit"
          class="mt-2 inline-flex min-h-11 cursor-pointer items-center rounded-md bg-surface-gray-7 px-3 text-sm font-medium text-ink-white disabled:opacity-60 sm:min-h-9"
          :disabled="saving"
        >
          {{ saving ? 'Saving…' : 'Save view' }}
        </button>
      </form>

      <p
        v-if="error"
        class="surface-alert mt-2 p-2 text-sm"
        role="alert"
      >
        {{ error }}
      </p>
    </div>
    <p
      class="sr-only"
      role="status"
    >
      {{ status }}
    </p>
  </div>
</template>
