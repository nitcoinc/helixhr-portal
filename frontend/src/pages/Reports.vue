<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import ReportCatalog from '@/components/reports/ReportCatalog.vue'
import ReportFilters from '@/components/reports/ReportFilters.vue'
import ReportTable from '@/components/reports/ReportTable.vue'
import ExportMenu from '@/components/reports/ExportMenu.vue'
import ExportLog from '@/components/reports/ExportLog.vue'
import { session } from '@/lib/session'
import { call } from '@/lib/api'
import { presetRange } from '@/lib/datePresets'
import { today } from '@/lib/dates'
import { fromQuery, toQuery } from '@/lib/reportQuery'

// Plan 2026-10-04-001 U4. Two routes, one page:
//   /reports              the catalog: what `get_report_catalog` says this
//                         caller may run, in families, searchable
//   /reports/<key>        one report: filter bar, Run, the shaped table
//
// The page never decides access. The catalog is the server's answer, and
// `run_report` / `search_report_options` re-check it per call. A key that is
// not in this caller's catalog (an HR Manager's link opened by an HR User)
// gets the same refusal the server would give.
//
// Run is explicit (resolved decision 14). A link carrying report state --
// a shared URL, People.vue's `?employee=` -- runs on open; edits after a run
// mark the results stale until Run is pressed again. Sorting is server-side
// (resolved decision 7) so changing it re-runs; hidden columns are URL state
// only.
const props = defineProps({
  reportKey: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

const catalogResource = createResource({ url: 'helixhr.api.get_report_catalog', auto: true })
const catalog = computed(() => catalogResource.data || [])
const entry = computed(() => catalog.value.find((item) => item.key === props.reportKey) || null)
const refused = computed(() => !!props.reportKey && !!catalogResource.data && !entry.value)

// Carried from People.vue onto whichever report is opened from the catalog.
const carryQuery = computed(() =>
  typeof route.query.employee === 'string' ? { employee: route.query.employee } : {},
)

// --- report state -----------------------------------------------------------

const filters = ref({})
const groupBy = ref([])
const sort = ref(null)
const hidden = ref([])
const lastRun = ref(null)

const reportResource = createResource({ url: 'helixhr.api.run_report', method: 'POST', auto: false })
const result = computed(() => (lastRun.value ? reportResource.data : null))

function defaults(item) {
  const values = {}
  for (const spec of item.filters) {
    if (spec.default !== null && spec.default !== undefined && spec.default !== '') values[spec.name] = spec.default
  }
  const range = item.default_preset ? presetRange(item.default_preset, today()) : null
  return range ? { ...values, ...range } : values
}

function snapshot() {
  return JSON.stringify({ filters: filters.value, groupBy: groupBy.value })
}

const stale = computed(() => !!lastRun.value && lastRun.value !== snapshot())

function syncUrl() {
  router.replace({
    name: 'ReportView',
    params: { reportKey: entry.value.key },
    query: toQuery(
      { filters: filters.value, groupBy: groupBy.value, sort: sort.value, hidden: hidden.value },
      entry.value,
    ),
  })
}

function cleanFilters() {
  return Object.fromEntries(
    Object.entries(filters.value).filter(([, value]) => value !== '' && value !== null && value !== undefined),
  )
}

function run() {
  if (!entry.value) return
  syncUrl()
  lastRun.value = snapshot()
  reportResource.submit({
    report_key: entry.value.key,
    filters: cleanFilters(),
    group_by: groupBy.value,
    sort: sort.value,
  })
}

// U5: an export is the screen's own query (hidden columns left out).
const exportQuery = computed(() => ({
  filters: cleanFilters(),
  group_by: groupBy.value,
  sort: sort.value,
  hidden: hidden.value,
}))

// U5: the export log tab on the catalog page, for HR Manager / System
// Manager -- `canConfigure` is the same `_is_hr` predicate
// `get_export_log` enforces.
const catalogTab = ref('reports')

// (Re)initialise when the report changes -- not on our own URL writes.
watch(
  entry,
  (item, previous) => {
    if (!item || item.key === previous?.key) return
    const parsed = fromQuery(route.query, item)
    filters.value = { ...defaults(item), ...parsed.filters }
    groupBy.value = parsed.groupBy
    sort.value = parsed.sort
    hidden.value = parsed.hidden
    lastRun.value = null
    reportResource.reset?.()
    if (parsed.hasState) run()
  },
  { immediate: true },
)

function onSort(value) {
  sort.value = value
  if (lastRun.value) run()
  else syncUrl()
}

function onHidden(value) {
  hidden.value = value
  syncUrl()
}

// --- result states ----------------------------------------------------------

const dataRows = computed(() => (result.value?.rows || []).filter((row) => row._kind === 'row'))

// A ValidationError from `run_report` is a sentence for the user ("Choose
// Month.", "That report could not run…"), not an outage; a PermissionError
// is AsyncState's own refusal state.
const runMessage = computed(() => {
  const error = reportResource.error
  if (!error || /PermissionError/.test(error.exc_type || '')) return ''
  if (error.exc_type === 'ValidationError') return error.messages?.[0] || 'That report could not run.'
  return ''
})

const narrowed = computed(() => !!entry.value?.filters.some(
  (spec) => !['date', 'month'].includes(spec.type) && spec.type !== 'toggle' && filters.value[spec.name],
))

const isEmpty = computed(
  () => !reportResource.loading && !reportResource.error && !!result.value && dataRows.value.length === 0,
)

const removedLabels = computed(() =>
  (result.value?.filters_removed || []).map(
    (name) => entry.value?.filters.find((spec) => spec.name === name)?.label || name,
  ),
)

// --- the secondary Desk link (System User with admin scope only) ------------
const openError = ref('')
const opening = ref(false)

async function openInDesk() {
  if (!entry.value?.can_open_in_desk) return
  openError.value = ''
  opening.value = true
  try {
    const url = await call('helixhr.api.get_report_link', {
      report: entry.value.report,
      employee: filters.value.employee || undefined,
    })
    window.open(url, '_blank', 'noopener')
  } catch (error) {
    openError.value = error?.messages?.[0] || "That report didn't open. Try again."
  } finally {
    opening.value = false
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="Reports"
      :subtitle="entry ? entry.question : 'Named questions, answered inside the portal.'"
    />

    <AsyncState
      :resource="catalogResource"
      section="report-catalog"
      :empty="false"
      skeleton="card"
      :skeleton-rows="4"
    >
      <!-- Zero granted: the same words as every other refusal. -->
      <div
        v-if="!catalog.length || refused"
        class="surface-card p-5 text-sm text-ink-gray-6"
        role="alert"
      >
        <p class="font-medium text-ink-gray-9">
          You don't have access to this
        </p>
        <p class="mt-1">
          <template v-if="refused">
            That report is not offered to you here.
          </template>
          If you think that's wrong, ask HR to check your access.
        </p>
        <router-link
          v-if="refused && catalog.length"
          :to="{ name: 'Reports' }"
          class="mt-3 inline-flex min-h-11 items-center text-blue-700 underline underline-offset-2"
        >
          &larr; All reports
        </router-link>
      </div>

      <template v-else-if="!entry">
        <nav
          v-if="session.canConfigure"
          class="mb-4 flex gap-1 border-b border-outline-gray-1"
          aria-label="Reports sections"
        >
          <button
            v-for="tab in [{ key: 'reports', label: 'Reports' }, { key: 'exports', label: 'Export log' }]"
            :key="tab.key"
            type="button"
            class="min-h-11 cursor-pointer rounded-t-lg px-3 py-2 text-sm font-medium"
            :aria-current="catalogTab === tab.key ? 'page' : undefined"
            :class="catalogTab === tab.key ? 'border-b-2 border-signal text-ink-gray-9' : 'text-ink-gray-6 hover:text-ink-gray-9'"
            @click="catalogTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </nav>
        <ExportLog v-if="session.canConfigure && catalogTab === 'exports'" />
        <ReportCatalog
          v-else
          :catalog="catalog"
          :carry-query="carryQuery"
        />
      </template>

      <template v-else>
        <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
          <router-link
            :to="{ name: 'Reports' }"
            class="-my-2 inline-flex min-h-11 items-center text-sm text-blue-700 underline underline-offset-2"
          >
            &larr; All reports
          </router-link>

          <!-- Report actions. U12's SavedViews mounts here too. -->
          <div
            class="flex flex-wrap items-center gap-3"
            data-slot="report-actions"
          >
            <ExportMenu
              :report-key="entry.key"
              :can-export="entry.can_export"
              :query="exportQuery"
            />
            <button
              v-if="entry.can_open_in_desk"
              type="button"
              class="-my-2 inline-flex min-h-11 cursor-pointer items-center text-sm text-blue-700 underline underline-offset-2"
              :disabled="opening"
              @click="openInDesk"
            >
              {{ opening ? 'Opening…' : 'Open in Frappe' }}
            </button>
          </div>
        </div>

        <p
          v-if="openError"
          class="surface-alert mb-4 p-3 text-sm"
          role="alert"
        >
          {{ openError }}
        </p>

        <h2 class="type-section mb-3 font-heading text-ink-gray-9">
          {{ entry.label }}
        </h2>

        <ReportFilters
          v-model="filters"
          v-model:group-by="groupBy"
          :entry="entry"
          :running="reportResource.loading"
          @run="run"
        />

        <p
          v-if="!lastRun"
          class="surface-inset p-4 text-sm text-ink-gray-7"
        >
          Choose filters, then press Run report.
        </p>

        <template v-else>
          <p
            v-if="stale && !reportResource.loading"
            class="surface-inset mb-3 p-3 text-sm text-ink-gray-7"
            role="status"
          >
            Filters changed. These results are out of date until you run the report again.
          </p>
          <p
            v-if="removedLabels.length"
            class="surface-inset mb-3 p-3 text-sm text-ink-gray-7"
            role="status"
          >
            {{ removedLabels.join(', ') }}: outside what you can report on, so nothing is shown.
          </p>
          <p
            v-if="runMessage"
            class="surface-alert mb-3 p-3 text-sm"
            role="alert"
          >
            {{ runMessage }}
          </p>

          <AsyncState
            v-if="!runMessage"
            :resource="reportResource"
            section="report-results"
            :empty="isEmpty"
            :empty-title="narrowed ? 'Nothing matched these filters' : 'No data in this period'"
            :empty-body="narrowed ? 'Clear or change a filter, then run again.' : 'Try a wider date range.'"
            skeleton="block"
            skeleton-height="h-48"
          >
            <ReportTable
              v-if="result"
              :caption="entry.label"
              :columns="result.columns"
              :rows="result.rows"
              :total-rows="result.total_rows"
              :truncated="result.truncated"
              :can-export="result.can_export"
              :sort="sort"
              :hidden="hidden"
              :class="stale ? 'opacity-60' : ''"
              @update:sort="onSort"
              @update:hidden="onHidden"
            />
          </AsyncState>
        </template>
      </template>
    </AsyncState>
  </div>
</template>
