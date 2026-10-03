<script setup>
import { computed, ref } from 'vue'
import { call } from '@/lib/api'
import { formatDate } from '@/lib/dates'

// Plan 2026-10-04-001 U5 (resolved decision 14): who exported what, for HR
// Manager and System Manager. `get_export_log` is the gate and returns
// metadata only -- never a file. Newest first, 50 at a time.
const PAGE = 50
const FORMAT_LABELS = { csv: 'CSV', xlsx: 'Excel', pdf: 'PDF' }

const rows = ref([])
const hasMore = ref(false)
const loading = ref(false)
const error = ref('')
const loaded = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const out = await call('helixhr.api.get_export_log', { start: rows.value.length, page_length: PAGE })
    rows.value = [...rows.value, ...out.rows]
    hasMore.value = out.has_more
    loaded.value = true
  } catch (failure) {
    error.value = failure?.messages?.[0] || "The export log didn't load. Try again."
  } finally {
    loading.value = false
  }
}
load()

const empty = computed(() => loaded.value && !rows.value.length)

function filterSummary(filters) {
  return Object.entries(filters || {})
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`)
    .join(', ')
}
</script>

<template>
  <section aria-labelledby="export-log-heading">
    <h2
      id="export-log-heading"
      class="type-section mb-3 font-heading text-ink-gray-9"
    >
      Export log
    </h2>
    <p
      v-if="empty"
      class="surface-inset p-4 text-sm text-ink-gray-7"
    >
      Nobody has exported a report yet.
    </p>
    <div
      v-else-if="rows.length"
      class="overflow-x-auto rounded-lg border border-outline-gray-1"
    >
      <table class="w-full text-sm">
        <caption class="sr-only">
          Report exports, newest first
        </caption>
        <thead class="bg-surface-gray-1 text-left text-ink-gray-6">
          <tr>
            <th
              scope="col"
              class="px-3 py-2 font-medium"
            >
              When
            </th>
            <th
              scope="col"
              class="px-3 py-2 font-medium"
            >
              Who
            </th>
            <th
              scope="col"
              class="px-3 py-2 font-medium"
            >
              Report
            </th>
            <th
              scope="col"
              class="px-3 py-2 font-medium"
            >
              Format
            </th>
            <th
              scope="col"
              class="px-3 py-2 text-right font-medium"
            >
              Rows
            </th>
            <th
              scope="col"
              class="px-3 py-2 font-medium"
            >
              Filters
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-outline-gray-1">
          <tr
            v-for="row in rows"
            :key="row.name"
            data-testid="export-log-row"
          >
            <td class="whitespace-nowrap px-3 py-2 tabular">
              {{ formatDate(row.creation) }} {{ String(row.creation).slice(11, 16) }}
            </td>
            <td class="px-3 py-2">
              {{ row.user_name }}
            </td>
            <td class="px-3 py-2">
              {{ row.report_label || row.report_key }}
            </td>
            <td class="px-3 py-2">
              {{ FORMAT_LABELS[row.format] || row.format }}<span
                v-if="row.status !== 'Ready'"
                class="text-ink-gray-5"
              > ({{ row.status }})</span>
            </td>
            <td class="px-3 py-2 text-right tabular">
              {{ row.row_count }}
            </td>
            <td class="px-3 py-2 text-ink-gray-6">
              {{ filterSummary(row.filters) }}<template v-if="row.group_by">
                ; grouped by {{ row.group_by }}
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p
      v-if="error"
      class="surface-alert mt-3 p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </p>
    <button
      v-if="hasMore || error"
      type="button"
      class="mt-3 inline-flex min-h-11 cursor-pointer items-center rounded-md border border-outline-gray-2 px-3 text-sm text-ink-gray-8"
      :disabled="loading"
      @click="load"
    >
      {{ loading ? 'Loading…' : error ? 'Try again' : 'Show more' }}
    </button>
  </section>
</template>
