<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { call } from '@/lib/api'
import { session } from '@/lib/session'
import { formatDate } from '@/lib/dates'
import { roundHours } from '@/lib/hours'

// P7-U9 / P7-R12, P7-R13, P7-R15. Reports render inside the portal now --
// this screen used to be a launcher that opened Frappe's own report view in
// a new tab (P6-U6) and refused everyone who could not reach Desk (P6-R12).
// That gate is gone: `run_report` (plan 2026-10-04-001 U2, which replaced
// P7-U8's two methods) is the server's own gate now, so a caller who cannot reach Desk still
// gets rows here (R15) -- Desk stays only as a secondary export affordance
// for a System User (see `openInDesk` below).
//
// Still a curated list, not a report tree (P6-R9-R11's posture, carried
// forward): the seven HR reports below, plus one new entry -- billable
// hours -- that is not a Frappe Report at all (KTD3a) and is offered only
// to a caller `resolve_project_scope` grants something to.
const REPORTS = [
  {
    key: 'leave_balance',
    report: 'Employee Leave Balance',
    label: 'Leave balance',
    question: 'How much leave does someone have left, by type?',
  },
  {
    key: 'leave_balance_summary',
    report: 'Employee Leave Balance Summary',
    label: 'Leave balance summary',
    question: 'Leave balances across the whole company, one row per person.',
  },
  {
    key: 'monthly_attendance',
    report: 'Monthly Attendance Sheet',
    label: 'Monthly attendance',
    question: 'A month of attendance, one row per person per day.',
  },
  {
    key: 'shift_attendance',
    report: 'Shift Attendance',
    label: 'Shift attendance',
    question: 'Who was on which shift, and when.',
  },
  {
    key: 'leave_ledger',
    report: 'Leave Ledger',
    label: 'Leave ledger',
    question: 'Every leave transaction that moved a balance.',
  },
  {
    // A HelixHR query (plan 2026-10-04-001 U2), so no Desk hand-off.
    key: 'employee_directory',
    report: null,
    label: 'Employee directory',
    question: 'Who works here, in which department, branch and role?',
  },
  {
    key: 'employee_exits',
    report: 'Employee Exits',
    label: 'Employee exits',
    question: 'Who has left, and when.',
  },
]

const route = useRoute()
const router = useRouter()

// Arrived from a person's view (People.vue) with ?employee=<id>: opening any
// report pre-fills the employee filter with them (P6-R10's rule, still
// true now that opening means running the report rather than a Desk URL).
const employee = computed(() => (typeof route.query.employee === 'string' ? route.query.employee : ''))

function clearEmployee() {
  router.replace({ name: 'Reports' })
}

// --- picker / detail state --------------------------------------------------
//
// `active` is null on the picker screen, or names which report is open:
// `{ kind: 'curated', report }` for one of the seven HR reports, or
// `{ kind: 'billable-hours' }` for the new one (R13).
const active = ref(null)

const filters = reactive({ employee: '', project: '', task: '', from_date: '', to_date: '' })

function resetFilters() {
  filters.employee = employee.value || ''
  filters.project = ''
  filters.task = ''
  filters.from_date = ''
  filters.to_date = ''
}

// Minimal bridge onto `run_report` (U2); the catalog-driven page is U4.
const reportResource = createResource({ url: 'helixhr.api.run_report', method: 'POST', auto: false })
const activeResource = computed(() => reportResource)

function runActive() {
  if (!active.value) return
  const isHours = active.value.kind === 'billable-hours'
  // Keys a report does not declare are dropped server-side.
  reportResource.submit({
    report_key: isHours ? 'hours_by_project' : active.value.key,
    filters: {
      employee: filters.employee || undefined,
      project: isHours ? filters.project || undefined : undefined,
      task: isHours ? filters.task || undefined : undefined,
      from_date: filters.from_date || undefined,
      to_date: filters.to_date || undefined,
    },
  })
}

function openCurated(entry) {
  active.value = { kind: 'curated', key: entry.key, report: entry.report, label: entry.label }
  resetFilters()
  runActive()
}

function openBillableHours() {
  active.value = { kind: 'billable-hours' }
  resetFilters()
  runActive()
}

function backToList() {
  active.value = null
}

// Changing a filter re-runs the active report (debounced, the same
// 250ms-ish pattern Projects.vue's member search already uses) rather than
// waiting for an explicit "Run" click.
let pending = null
watch(
  () => [filters.employee, filters.project, filters.task, filters.from_date, filters.to_date],
  () => {
    if (!active.value) return
    clearTimeout(pending)
    pending = setTimeout(runActive, 300)
  },
)
onUnmounted(() => clearTimeout(pending))

// --- rendering server-declared columns --------------------------------------
//
// `run_report` returns dict columns for every engine, and shaped rows typed
// by `_kind`; this bridge shows data rows only (totals arrive with U4).
const NUMERIC_FIELDTYPES = new Set(['Int', 'Float', 'Currency', 'Percent', 'Duration'])
const DATE_FIELDTYPES = new Set(['Date', 'Datetime'])

const columns = computed(() =>
  active.value ? (reportResource.data?.columns || []).filter((column) => !column?.hidden) : [],
)

const allRows = computed(() =>
  active.value ? (reportResource.data?.rows || []).filter((row) => row._kind === 'row') : [],
)

// A stated ceiling rather than an unbounded table (P7-U9), settled against
// how the approval queue treats its own cap (P2-U4's `_QUEUE_LIMIT`): show a
// bounded page and say how many more there are, rather than paging or
// rendering everything the query returned. A report table can reasonably
// hold more rows than a work queue, so the number is larger, but the shape
// -- a hard slice plus a stated remainder -- is the same one.
const ROW_CAP = 100
const rows = computed(() => allRows.value.slice(0, ROW_CAP))
const overflow = computed(() => Math.max(0, allRows.value.length - ROW_CAP))

// "Empty" is this page's own answer, computed from what the request
// resolved to (AsyncState's contract) -- never true while a request is
// still in flight or has failed, so a refusal or an outage can never be
// mistaken for "no rows" (P7-U9's own test scenario).
const isEmpty = computed(
  () => !activeResource.value.loading && !activeResource.value.error && allRows.value.length === 0,
)

function isNumericColumn(column) {
  return NUMERIC_FIELDTYPES.has(column.fieldtype)
}

function formatCell(row, column) {
  const value = row[column.fieldname]
  if (value === null || value === undefined || value === '') return ''
  if (DATE_FIELDTYPES.has(column.fieldtype)) return formatDate(value)
  if (NUMERIC_FIELDTYPES.has(column.fieldtype)) return roundHours(value)
  return value
}

// --- the secondary Desk link (P7-R12, System User only) --------------------
//
// No longer the primary path -- `run_report` already rendered the
// report above -- kept only for what the portal deliberately does not
// cover (Frappe's own export toolbar). Absent entirely for anyone Desk
// would not load for (`session.canOpenDesk` mirrors `_can_open_desk`
// server-side, P6-KTD4), including a HelixHR Delivery Manager, and never
// offered for billable hours -- it is not a Frappe Report, so there is no
// Desk view to hand off to (KTD3a).
const openError = ref('')
const opening = ref(false)

async function openInDesk() {
  if (active.value?.kind !== 'curated' || !active.value.report) return
  openError.value = ''
  opening.value = true
  try {
    const url = await call('helixhr.api.get_report_link', {
      report: active.value.report,
      employee: filters.employee || undefined,
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
      subtitle="Named questions, answered inside the portal -- no hand-off to Frappe required."
    />

    <div
      v-if="!session.canSeePeople && !session.canSeeProjects"
      class="surface-card p-5 text-sm text-ink-gray-6"
      role="alert"
    >
      <p class="font-medium text-ink-gray-9">
        You don't have access to this
      </p>
      <p class="mt-1">
        If you think that's wrong, ask HR to check your access.
      </p>
    </div>

    <template v-else>
      <!-- The picker. -->
      <template v-if="!active">
        <p
          v-if="employee"
          class="surface-inset mb-4 flex items-center justify-between gap-3 p-3 text-sm text-ink-gray-7"
        >
          Filtered to one person.
          <button
            type="button"
            class="cursor-pointer text-blue-700 underline underline-offset-2"
            @click="clearEmployee"
          >
            Clear
          </button>
        </p>

        <ul
          v-if="session.canSeePeople"
          class="space-y-2 lg:grid lg:grid-cols-2 lg:gap-3 lg:space-y-0"
        >
          <li
            v-for="entry in REPORTS"
            :key="entry.key"
          >
            <button
              type="button"
              class="surface-card elev-1 flex h-full w-full flex-col items-start gap-1 p-4 text-left"
              @click="openCurated(entry)"
            >
              <span class="font-medium text-ink-gray-9">{{ entry.label }}</span>
              <span class="text-sm text-ink-gray-6">{{ entry.question }}</span>
            </button>
          </li>
        </ul>

        <!-- The one report this plan adds (R13): its own scoped query, not
             a curated Frappe report -- offered to whoever
             `resolve_project_scope` grants something to, which is not
             necessarily who `resolve_admin_scope` does (a HelixHR Delivery
             Manager has one, never the other). -->
        <button
          v-if="session.canSeeProjects"
          type="button"
          class="surface-card elev-1 mt-3 flex w-full flex-col items-start gap-1 p-4 text-left lg:max-w-md"
          @click="openBillableHours"
        >
          <span class="font-medium text-ink-gray-9">Hours by project</span>
          <span class="text-sm text-ink-gray-6">
            Approved hours logged against projects and tasks, by employee and date. Hours only --
            no rate, no amount.
          </span>
        </button>
      </template>

      <!-- The report itself: filters, and the table it produced. -->
      <template v-else>
        <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            class="cursor-pointer text-sm text-blue-700 underline underline-offset-2"
            @click="backToList"
          >
            &larr; Back to reports
          </button>

          <div v-if="active.kind === 'curated' && active.report && session.canOpenDesk">
            <button
              type="button"
              class="cursor-pointer text-sm text-blue-700 underline underline-offset-2"
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
          {{ active.kind === 'billable-hours' ? 'Hours by project' : active.label }}
        </h2>

        <div class="mb-4 flex flex-wrap gap-3">
          <FormControl
            v-model="filters.employee"
            label="Employee"
            placeholder="Employee ID"
            class="w-48"
          />
          <template v-if="active.kind === 'billable-hours'">
            <FormControl
              v-model="filters.project"
              label="Project"
              placeholder="Project ID"
              class="w-48"
            />
            <FormControl
              v-model="filters.task"
              label="Task"
              placeholder="Task ID"
              class="w-48"
            />
          </template>
          <FormControl
            v-model="filters.from_date"
            type="date"
            label="From"
            class="w-40"
          />
          <FormControl
            v-model="filters.to_date"
            type="date"
            label="To"
            class="w-40"
          />
        </div>

        <AsyncState
          :resource="activeResource"
          section="report-results"
          :empty="isEmpty"
          empty-title="No matching rows"
          empty-body="Try widening or clearing the filters."
          skeleton="block"
          skeleton-height="h-48"
        >
          <!-- Scrolls inside its own container rather than the page
               (P2-R3), the same treatment the timesheet-detail table in
               Approvals.vue already gets. -->
          <div class="surface-card elev-1 overflow-x-auto p-4">
            <table class="w-full min-w-[40rem] text-sm">
              <thead>
                <tr class="border-b border-outline-gray-2">
                  <th
                    v-for="column in columns"
                    :key="column.fieldname"
                    scope="col"
                    class="label py-2"
                    :class="isNumericColumn(column) ? 'text-right' : 'text-left'"
                  >
                    {{ column.label || column.fieldname }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, index) in rows"
                  :key="index"
                  class="border-b border-outline-gray-2"
                >
                  <td
                    v-for="column in columns"
                    :key="column.fieldname"
                    class="py-2 pr-3 text-ink-gray-7"
                    :class="isNumericColumn(column) ? 'tabular text-right' : 'text-left'"
                  >
                    {{ formatCell(row, column) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p
            v-if="overflow > 0"
            class="mt-2 text-sm text-ink-gray-6"
          >
            Showing the first {{ ROW_CAP }} rows.
            <span class="tabular font-medium">{{ overflow }}</span>
            more not shown here -- narrow the filters to see them.
          </p>
        </AsyncState>
      </template>
    </template>
  </div>
</template>
