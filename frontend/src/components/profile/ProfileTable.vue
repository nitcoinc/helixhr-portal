<script setup>
import { formatDate, isCalendarDate } from '@/lib/dates'
import { displayValue } from '@/lib/profileCorrection'

// Plan 2026-09-29-001 U5. One of the Employee child tables (education, work
// history) as the server projected it: named columns only. A table on a
// wide screen, stacked cards on a phone, so nothing scrolls sideways. One
// correction action for the whole section -- a row-level button would ask
// the employee to describe a row HR can already see.
defineProps({
  table: { type: Object, required: true },
  canCorrect: { type: Boolean, default: true },
})
const emit = defineEmits(['correct'])

function cell(value) {
  return displayValue(value, (v) => (isCalendarDate(v) ? formatDate(v) : String(v)))
}
</script>

<template>
  <section
    :aria-labelledby="`profile-table-${table.fieldname}`"
    :data-testid="`profile-table-${table.fieldname}`"
  >
    <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
      <h3
        :id="`profile-table-${table.fieldname}`"
        class="label"
      >
        {{ table.label }}
      </h3>
      <button
        v-if="canCorrect"
        type="button"
        class="inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-blue-700 underline decoration-dotted underline-offset-4"
        :aria-label="`Request a correction to ${table.label}`"
        @click="emit('correct', { label: table.label, table: true })"
      >
        Request a correction
      </button>
    </div>

    <p
      v-if="!table.rows.length"
      class="surface-card elev-1 px-4 py-3 text-sm text-ink-gray-5"
    >
      Nothing recorded yet.
    </p>

    <template v-else>
      <div class="surface-card elev-1 hidden overflow-hidden sm:block">
        <table class="w-full text-left text-sm">
          <thead class="border-b border-outline-gray-1 text-ink-gray-6">
            <tr>
              <th
                v-for="column in table.columns"
                :key="column.fieldname"
                scope="col"
                class="px-4 py-2 font-medium"
              >
                {{ column.label }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-outline-gray-1">
            <tr
              v-for="(row, index) in table.rows"
              :key="index"
            >
              <td
                v-for="column in table.columns"
                :key="column.fieldname"
                class="px-4 py-2 align-top break-words text-ink-gray-9"
              >
                {{ cell(row[column.fieldname]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <ul class="space-y-2 sm:hidden">
        <li
          v-for="(row, index) in table.rows"
          :key="index"
          class="surface-card elev-1 space-y-1 px-4 py-3"
        >
          <div
            v-for="column in table.columns"
            :key="column.fieldname"
            class="flex justify-between gap-3 text-sm"
          >
            <span class="text-ink-gray-6">{{ column.label }}</span>
            <span class="min-w-0 break-words text-right text-ink-gray-9">{{ cell(row[column.fieldname]) }}</span>
          </div>
        </li>
      </ul>
    </template>
  </section>
</template>
