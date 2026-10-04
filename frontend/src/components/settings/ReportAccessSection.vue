<script setup>
import { computed, ref, watch } from 'vue'
import { createResource } from 'frappe-ui'
import { call } from '@/lib/api'

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
const DM_REASON = 'Not for Delivery Manager: this report has no project scope.'

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

function disabled(row, flag) {
  return flag.startsWith('dm_') && !row.dm_allowed
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
    rejectedKey.value = draft.value.find((row) => error.value.startsWith(`${row.label}:`))?.key || ''
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
    </div>

    <p
      v-if="resource.error"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      Report access didn't load. Reload the page to try again.
    </p>

    <section
      v-for="group in groups"
      :key="group.key"
      :aria-labelledby="`access-family-${group.key}`"
    >
      <h3
        :id="`access-family-${group.key}`"
        class="mb-2 text-sm font-medium text-ink-gray-7"
      >
        {{ group.label }}
      </h3>

      <!-- Wide screens: one table row per report. -->
      <div class="surface-card elev-1 hidden overflow-x-auto sm:block">
        <table class="w-full text-sm">
          <thead class="text-left text-ink-gray-6">
            <tr class="border-b border-outline-gray-1">
              <th
                scope="col"
                class="px-4 py-2 font-medium"
              >
                Report
              </th>
              <th
                v-for="cell in CELLS"
                :key="cell.flag"
                scope="col"
                class="px-3 py-2 text-center font-medium"
              >
                {{ cell.tier }}<br><span class="font-normal">{{ cell.right }}</span>
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-outline-gray-1">
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
                <p
                  v-if="!row.dm_allowed"
                  :id="`access-why-${row.key}`"
                  class="text-xs font-normal text-ink-gray-5"
                >
                  {{ DM_REASON }}
                </p>
              </th>
              <td
                v-for="cell in CELLS"
                :key="cell.flag"
                class="px-3 py-2 text-center"
              >
                <input
                  type="checkbox"
                  class="size-5 cursor-pointer disabled:cursor-not-allowed"
                  :aria-label="cellName(row, cell)"
                  :aria-describedby="disabled(row, cell.flag) ? `access-why-${row.key}` : undefined"
                  :checked="!!row[cell.flag]"
                  :disabled="disabled(row, cell.flag)"
                  @change="setCell(row, cell.flag, $event.target.checked)"
                >
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Phones: one card per report. -->
      <ul class="space-y-2 sm:hidden">
        <li
          v-for="row in group.rows"
          :key="row.key"
          class="surface-card elev-1 p-3"
          :class="rejectedKey === row.key ? 'ring-2 ring-red-400' : ''"
        >
          <p class="font-medium text-ink-gray-9">
            {{ row.label }}
          </p>
          <p
            v-if="!row.dm_allowed"
            :id="`access-why-m-${row.key}`"
            class="text-xs text-ink-gray-5"
          >
            {{ DM_REASON }}
          </p>
          <div class="mt-2 grid grid-cols-2 gap-1">
            <label
              v-for="cell in CELLS"
              :key="cell.flag"
              class="flex min-h-11 items-center gap-2 text-sm"
              :class="disabled(row, cell.flag) ? 'text-ink-gray-4' : 'text-ink-gray-8'"
            >
              <input
                type="checkbox"
                class="size-5"
                :aria-label="cellName(row, cell)"
                :aria-describedby="disabled(row, cell.flag) ? `access-why-m-${row.key}` : undefined"
                :checked="!!row[cell.flag]"
                :disabled="disabled(row, cell.flag)"
                @change="setCell(row, cell.flag, $event.target.checked)"
              >
              {{ cell.tier }} {{ cell.right }}
            </label>
          </div>
        </li>
      </ul>
    </section>

    <p
      v-if="saved && !dirty"
      class="text-sm text-ink-gray-6"
      role="status"
    >
      {{ saved }}
    </p>

    <div
      v-if="dirty"
      class="surface-card elev-2 sticky bottom-0 z-10 flex flex-wrap items-center gap-3 p-3"
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
      <button
        type="button"
        class="inline-flex min-h-11 cursor-pointer items-center rounded-md bg-surface-gray-7 px-4 text-sm font-medium text-ink-white disabled:opacity-60"
        :disabled="saving"
        @click="save"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
    </div>
  </div>
</template>
