<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, createResource } from 'frappe-ui'
import { call } from '@/lib/api'
import { session } from '@/lib/session'
import Icon from '@/components/Icon.vue'

// Plan 2026-10-04-001 U6 (R20, R21, resolved decision 14): who may run and
// export each catalog report. HR Manager and Report Manager always may; HR
// User and Delivery Manager are this matrix. The server is the gate
// (`save_report_access` re-checks every rule and rejects a bad batch whole);
// the coupling below (export needs run) only keeps the draft sensible.
const FAMILIES = [
  { key: 'time', label: 'Time' },
  { key: 'attendance', label: 'Attendance' },
  { key: 'leave', label: 'Leave' },
  { key: 'people', label: 'People' },
]
const CELLS = [
  { flag: 'hr_user_run', tier: 'HR User', right: 'run' },
  { flag: 'hr_user_export', tier: 'HR User', right: 'export' },
  { flag: 'dm_run', tier: 'Delivery Manager', right: 'run' },
  { flag: 'dm_export', tier: 'Delivery Manager', right: 'export' },
]
// AI-05: one footnote under the Delivery Manager header instead of the same
// sentence under 17 report names. Cells it covers render a dash, not a dead
// checkbox, and are described by it.
const NA_NOTE = "— means the report has no project scope, so a Delivery Manager can't be given it."

// Plan 2026-10-19-001 R7: the matrix is one global policy, so changing it
// needs All-orgs authority -- the same predicate as the Email templates page
// (`can_edit_email_templates` is `has_all_orgs_authority`). A named-org
// Portal Admin reads it; the server refuses their save either way.
const readOnly = !session.canEditEmailTemplates

const resource = createResource({ url: 'helixhr.api.get_report_access', auto: true })
const draft = ref([])
const saving = ref(false)
const error = ref('')
const rejectedKey = ref('')
const saved = ref('')

function reset() {
  draft.value = (resource.data || []).map((row) => ({ ...row }))
  error.value = ''
  rejectedKey.value = ''
}
watch(() => resource.data, reset, { immediate: true })

const original = computed(() => Object.fromEntries((resource.data || []).map((row) => [row.key, row])))
const changed = computed(() =>
  draft.value.filter((row) => CELLS.some(({ flag }) => !!row[flag] !== !!original.value[row.key]?.[flag])),
)
const dirty = computed(() => changed.value.length > 0)

const groups = computed(() =>
  FAMILIES.map((family) => ({ ...family, rows: draft.value.filter((row) => row.family === family.key) })).filter(
    (family) => family.rows.length,
  ),
)

function applicable(row, flag) {
  return !flag.startsWith('dm_') || !!row.dm_allowed
}

function setCell(row, flag, on) {
  saved.value = ''
  row[flag] = on ? 1 : 0
  const prefix = flag.startsWith('dm_') ? 'dm' : 'hr_user'
  if (on && flag.endsWith('_export')) row[`${prefix}_run`] = 1
  if (!on && flag.endsWith('_run')) row[`${prefix}_export`] = 0
}

function cellName(row, cell) {
  return `${row.label}: ${cell.tier} ${cell.right}`
}

// AI-05: a select-all per family and column, tri-state when some are on.
// It goes through `setCell`, so export still implies run, and the rows it
// touches reach the server through the same `changed` batch as single cells.
function column(group, cell) {
  const rows = group.rows.filter((row) => applicable(row, cell.flag))
  const on = rows.filter((row) => row[cell.flag]).length
  return { rows, on, all: rows.length > 0 && on === rows.length, some: on > 0 && on < rows.length }
}
function setColumn(group, cell, on) {
  for (const row of column(group, cell).rows) setCell(row, cell.flag, on)
}
function columnName(group, cell) {
  return `All ${group.label} reports: ${cell.tier} ${cell.right}`
}

// AI-06: on a phone each family folds to one line that says where it stands.
function familySummary(group) {
  // Counted on "run": export is never on without it.
  const hr = column(group, CELLS[0])
  const dm = column(group, CELLS[2])
  let text = `${hr.on} of ${hr.rows.length} on for HR User`
  if (dm.rows.length) text += `, ${dm.on} of ${dm.rows.length} for Delivery Manager`
  return text
}
const openFamilies = reactive({})

async function save() {
  saving.value = true
  error.value = ''
  rejectedKey.value = ''
  try {
    const rows = changed.value.map((row) => ({
      key: row.key,
      ...Object.fromEntries(CELLS.map(({ flag }) => [flag, row[flag] ? 1 : 0])),
    }))
    const count = rows.length
    resource.setData(await call('helixhr.api.save_report_access', { rows }))
    saved.value = `Saved ${count} ${count === 1 ? 'report' : 'reports'}.`
  } catch (failure) {
    error.value = failure?.messages?.[0] || "Those changes didn't save. Try again."
    // The server names the report it rejected ("Leave ledger: ...").
    const rejected = draft.value.find((row) => error.value.startsWith(`${row.label}:`))
    rejectedKey.value = rejected?.key || ''
    if (rejected) openFamilies[rejected.family] = true
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="label">
        Report access
      </h2>
      <p class="mt-1 text-sm text-ink-gray-6">
        HR Manager and Report Manager can always run and export every report. Choose what HR User and
        Delivery Manager can do. Delivery Managers only ever see their own projects.
      </p>
      <p
        v-if="readOnly"
        class="surface-inset mt-2 p-3 text-sm text-ink-gray-7"
        data-testid="report-access-read-only"
      >
        You can view report access. Changing it needs access in all orgs, because it applies to every org.
      </p>
    </div>

    <p
      v-if="resource.error"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      Report access didn't load. Reload the page to try again.
    </p>

    <!-- Wide screens: one table, one header row, a select-all row per family. -->
    <div
      v-if="groups.length"
      class="surface-card elev-1 hidden overflow-x-auto sm:block"
    >
      <table class="w-full text-sm">
        <thead class="text-left text-ink-gray-6">
          <tr>
            <th
              scope="col"
              rowspan="2"
              class="px-4 py-2 align-bottom font-medium"
            >
              Report
            </th>
            <th
              scope="colgroup"
              colspan="2"
              class="px-3 pt-2 text-center align-top font-medium"
            >
              HR User
            </th>
            <th
              scope="colgroup"
              colspan="2"
              class="px-3 pt-2 text-center align-top font-medium"
            >
              Delivery Manager
              <p
                id="access-dm-note"
                class="mx-auto max-w-[17rem] text-xs font-normal text-ink-gray-5"
              >
                {{ NA_NOTE }}
              </p>
            </th>
          </tr>
          <tr class="border-b border-outline-gray-1">
            <th
              v-for="cell in CELLS"
              :key="cell.flag"
              scope="col"
              class="px-3 pb-2 text-center font-normal"
            >
              {{ cell.right }}
            </th>
          </tr>
        </thead>
        <tbody
          v-for="group in groups"
          :key="group.key"
          class="divide-y divide-outline-gray-1 border-b border-outline-gray-1 last:border-b-0"
        >
          <tr class="bg-surface-gray-1">
            <th
              :id="`access-family-${group.key}`"
              scope="rowgroup"
              class="label px-4 py-2 text-left text-ink-gray-7"
            >
              {{ group.label }}
            </th>
            <td
              v-for="cell in CELLS"
              :key="cell.flag"
              class="px-3 py-2 text-center"
            >
              <input
                v-if="column(group, cell).rows.length"
                type="checkbox"
                :disabled="readOnly"
                class="size-5 cursor-pointer disabled:cursor-not-allowed"
                :aria-label="columnName(group, cell)"
                :checked="column(group, cell).all"
                :indeterminate="column(group, cell).some"
                :data-testid="`access-all-${group.key}-${cell.flag}`"
                @change="setColumn(group, cell, $event.target.checked)"
              >
              <span
                v-else
                role="img"
                class="text-ink-gray-5"
                :aria-label="`${columnName(group, cell)}: not available`"
                aria-describedby="access-dm-note"
              >—</span>
            </td>
          </tr>
          <tr
            v-for="row in group.rows"
            :key="row.key"
            :class="rejectedKey === row.key ? 'bg-red-50' : ''"
            :data-testid="`access-row-${row.key}`"
          >
            <th
              scope="row"
              class="px-4 py-2 text-left font-medium text-ink-gray-9"
            >
              {{ row.label }}
            </th>
            <td
              v-for="cell in CELLS"
              :key="cell.flag"
              class="px-3 py-2 text-center"
            >
              <input
                v-if="applicable(row, cell.flag)"
                type="checkbox"
                :disabled="readOnly"
                class="size-5 cursor-pointer disabled:cursor-not-allowed"
                :aria-label="cellName(row, cell)"
                :checked="!!row[cell.flag]"
                @change="setCell(row, cell.flag, $event.target.checked)"
              >
              <span
                v-else
                role="img"
                class="text-ink-gray-5"
                :aria-label="`${cellName(row, cell)}: not available`"
                aria-describedby="access-dm-note"
                :data-testid="`access-na-${row.key}-${cell.flag}`"
              >—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Phones: one folded section per family, its count in the summary.
         Delivery Manager controls appear only where they can apply. -->
    <div
      v-if="groups.length"
      class="space-y-2 sm:hidden"
    >
      <p class="text-xs text-ink-gray-5">
        Delivery Manager shows only on reports with project scope.
      </p>
      <details
        v-for="group in groups"
        :key="group.key"
        class="surface-card elev-1"
        :open="!!openFamilies[group.key]"
        :data-testid="`access-family-${group.key}`"
        @toggle="openFamilies[group.key] = $event.target.open"
      >
        <!-- A flex summary loses the native disclosure marker, so the
             chevron says it opens (the My projects "Other projects"
             disclosure); the count runs on as text so a long one wraps
             under the family name, not beside it. -->
        <summary class="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm [&::-webkit-details-marker]:hidden">
          <Icon
            name="chevronRight"
            class="shrink-0 text-ink-gray-5 transition-transform duration-200"
            :class="openFamilies[group.key] ? 'rotate-90' : ''"
          />
          <span class="min-w-0">
            <span class="font-medium text-ink-gray-9">{{ group.label }}</span>
            <span class="text-ink-gray-6"> · {{ familySummary(group) }}</span>
          </span>
        </summary>
        <div class="border-t border-outline-gray-1 px-3 pb-1">
          <p class="label pt-2 text-ink-gray-6">
            All {{ group.label }} reports
          </p>
          <div class="grid grid-cols-2 gap-x-3">
            <template
              v-for="cell in CELLS"
              :key="cell.flag"
            >
              <label
                v-if="column(group, cell).rows.length"
                class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8"
              >
                <input
                  type="checkbox"
                  :disabled="readOnly"
                  class="size-5"
                  :aria-label="columnName(group, cell)"
                  :checked="column(group, cell).all"
                  :indeterminate="column(group, cell).some"
                  @change="setColumn(group, cell, $event.target.checked)"
                >
                {{ cell.tier }} {{ cell.right }}
              </label>
            </template>
          </div>
          <ul class="divide-y divide-outline-gray-1">
            <li
              v-for="row in group.rows"
              :key="row.key"
              class="py-2"
              :class="rejectedKey === row.key ? 'bg-red-50' : ''"
            >
              <p class="text-sm font-medium text-ink-gray-9">
                {{ row.label }}
              </p>
              <div class="grid grid-cols-2 gap-x-3">
                <template
                  v-for="cell in CELLS"
                  :key="cell.flag"
                >
                  <label
                    v-if="applicable(row, cell.flag)"
                    class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8"
                  >
                    <input
                      type="checkbox"
                      :disabled="readOnly"
                      class="size-5"
                      :aria-label="cellName(row, cell)"
                      :checked="!!row[cell.flag]"
                      @change="setCell(row, cell.flag, $event.target.checked)"
                    >
                    {{ cell.tier }} {{ cell.right }}
                  </label>
                </template>
              </div>
            </li>
          </ul>
        </div>
      </details>
    </div>

    <p
      v-if="saved && !dirty"
      class="text-sm text-ink-gray-6"
      role="status"
    >
      {{ saved }}
    </p>

    <div
      v-if="dirty"
      class="surface-card elev-2 sticky bottom-[calc(56px+env(safe-area-inset-bottom))] z-10 flex flex-wrap items-center gap-3 p-3 lg:bottom-0"
      data-testid="access-save-bar"
    >
      <p
        class="mr-auto text-sm text-ink-gray-7"
        role="status"
      >
        {{ changed.length }} unsaved {{ changed.length === 1 ? 'change' : 'changes' }}
      </p>
      <p
        v-if="error"
        class="w-full text-sm text-red-700"
        role="alert"
      >
        {{ error }}
      </p>
      <button
        type="button"
        class="inline-flex min-h-11 cursor-pointer items-center rounded-md px-3 text-sm text-ink-gray-7 hover:bg-surface-gray-2"
        :disabled="saving"
        @click="reset"
      >
        Discard
      </button>
      <Button
        variant="solid"
        theme="blue"
        class="min-h-11"
        :disabled="saving"
        @click="save"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </Button>
    </div>
  </div>
</template>
