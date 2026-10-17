<script setup>
import { computed, reactive, ref, useId } from 'vue'
import SearchField from '@/components/SearchField.vue'
import { call } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'
import { summarizeExportFilters } from '@/lib/exportFilters'

// Plan 2026-10-04-001 U5 (resolved decision 14): who exported what, for HR
// Manager, System Manager and Portal Admin. `get_export_log` is the gate and returns
// metadata only -- never a file. Newest first, 50 at a time.
//
// Plan 2026-10-17-001 U11 (AI-08, AI-10, AI-11): two-line rows below `md`
// with the filters in a third line that opens on tap; the table stays from
// `md` up. The Report and Person filters narrow the rows already loaded --
// the endpoint pages but does not filter. Upgrade path if the log outgrows
// that: `report_key` / `owner` arguments on `get_export_log`.
const PAGE = 50
const FORMAT_LABELS = { csv: 'CSV', xlsx: 'Excel', pdf: 'PDF' }

const id = useId()
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

const reportKey = ref('')
const person = ref('')
const reportOptions = computed(() => {
  const labels = new Map()
  for (const row of rows.value) labels.set(row.report_key, row.report_label || row.report_key)
  return [...labels].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
})
const filtering = computed(() => !!reportKey.value || !!person.value.trim())
const shown = computed(() => {
  const needle = person.value.trim().toLowerCase()
  return rows.value.filter(
    (row) =>
      (!reportKey.value || row.report_key === reportKey.value) &&
      (!needle || `${row.user_name || ''} ${row.owner || ''}`.toLowerCase().includes(needle)),
  )
})

function clearFilters() {
  reportKey.value = ''
  person.value = ''
}

function formatOf(row) {
  const label = FORMAT_LABELS[row.format] || row.format
  return row.status !== 'Ready' ? `${label} (${row.status})` : label
}
function rowsOf(row) {
  return `${row.row_count} ${row.row_count === 1 ? 'row' : 'rows'}`
}
function filtersOf(row) {
  return summarizeExportFilters(row.filters, row.group_by)
}

// Phone rows: the filters line opens in place, one row at a time or several.
const open = reactive({})
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

    <template v-else-if="rows.length">
      <div class="mb-3 flex flex-wrap items-end gap-3">
        <div class="w-full sm:w-auto">
          <label
            :for="`${id}-report`"
            class="mb-1 block text-sm text-ink-gray-7"
          >Report</label>
          <select
            :id="`${id}-report`"
            v-model="reportKey"
            class="block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8 sm:w-56"
            data-testid="export-log-report"
          >
            <option value="">
              All reports
            </option>
            <option
              v-for="option in reportOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
        <div class="w-full sm:w-64">
          <SearchField
            v-model="person"
            label="Person"
            placeholder="Name or email"
            maxlength="140"
            data-testid="export-log-person"
          />
        </div>
      </div>
      <p
        class="text-sm text-ink-gray-6"
        :class="filtering ? 'mb-3' : ''"
        role="status"
      >
        <template v-if="filtering">
          {{ shown.length }} of {{ rows.length }} loaded {{ rows.length === 1 ? 'export' : 'exports' }}<template v-if="hasMore">
            — load more to search further back
          </template>
        </template>
      </p>

      <div
        v-if="!shown.length"
        class="surface-inset p-4 text-sm text-ink-gray-7"
      >
        No loaded export matches these filters.
        <button
          type="button"
          class="-my-2 inline-flex min-h-11 cursor-pointer items-center text-blue-700 underline underline-offset-2"
          @click="clearFilters"
        >
          Clear filters
        </button>
      </div>

      <template v-else>
        <!-- Below md: two lines per export, the filters a third on tap. -->
        <ul
          class="surface-card divide-y divide-outline-gray-1 md:hidden"
          aria-label="Report exports, newest first"
        >
          <li
            v-for="row in shown"
            :key="row.name"
            class="px-3 py-2"
            data-testid="export-log-item"
          >
            <p class="text-sm font-medium text-ink-gray-9">
              {{ row.report_label || row.report_key }} · {{ formatOf(row) }} · <span class="tabular">{{ rowsOf(row) }}</span>
            </p>
            <p class="text-sm text-ink-gray-6">
              {{ row.user_name }} · <span class="tabular">{{ formatDateTime(row.creation) }}</span>
            </p>
            <button
              v-if="filtersOf(row)"
              type="button"
              class="-mb-2 flex min-h-11 w-full cursor-pointer items-start pt-0.5 text-left text-xs text-ink-gray-5"
              :aria-expanded="open[row.name] ? 'true' : 'false'"
              data-testid="export-log-filters"
              @click="open[row.name] = !open[row.name]"
            >
              <span :class="open[row.name] ? '' : 'line-clamp-1'">{{ filtersOf(row) }}</span>
            </button>
          </li>
        </ul>

        <div class="hidden overflow-x-auto rounded-lg border border-outline-gray-1 md:block">
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
                v-for="row in shown"
                :key="row.name"
                data-testid="export-log-row"
              >
                <td class="whitespace-nowrap px-3 py-2 tabular">
                  {{ formatDateTime(row.creation) }}
                </td>
                <td class="px-3 py-2">
                  {{ row.user_name }}
                </td>
                <td class="px-3 py-2">
                  {{ row.report_label || row.report_key }}
                </td>
                <td class="whitespace-nowrap px-3 py-2">
                  {{ formatOf(row) }}
                </td>
                <td class="px-3 py-2 text-right tabular">
                  {{ row.row_count }}
                </td>
                <td class="max-w-xs px-3 py-2 text-ink-gray-6">
                  <span
                    class="line-clamp-2"
                    :title="filtersOf(row)"
                  >{{ filtersOf(row) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
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
