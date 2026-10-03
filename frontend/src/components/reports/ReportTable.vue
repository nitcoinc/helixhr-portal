<script setup>
import { computed, ref, useId, watch } from 'vue'
import { formatDate } from '@/lib/dates'
import { roundHours } from '@/lib/hours'

// Plan 2026-10-04-001 U4: one table for every catalog report. A real
// <table> with a caption; sort buttons inside each header with `aria-sort`;
// subtotal rows styled apart; header and grand total stick inside the
// table's own scroller; 50 rows a page client-side within the screen cap;
// result counts announced. Sorting is the server's (resolved decision 7:
// it sorts before the 2,000-row cap), so this only emits `update:sort`.
// Hidden columns (resolved decision 10) are the page's URL state.
const props = defineProps({
  caption: { type: String, required: true },
  columns: { type: Array, required: true },
  /** Shaped rows from `run_report`: `_kind` row / subtotal / total. */
  rows: { type: Array, required: true },
  totalRows: { type: Number, required: true },
  truncated: { type: Boolean, default: false },
  canExport: { type: Boolean, default: false },
  /** `{field, order}` or null. */
  sort: { type: Object, default: null },
  hidden: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:sort', 'update:hidden'])

const PAGE_SIZE = 50
const NUMERIC = new Set(['Int', 'Float', 'Currency', 'Percent', 'Duration'])
const DATES = new Set(['Date', 'Datetime'])

const id = useId()
const page = ref(0)
const columnsOpen = ref(false)

const available = computed(() => props.columns.filter((column) => !column?.hidden))
const visible = computed(() => available.value.filter((column) => !props.hidden.includes(column.fieldname)))
const body = computed(() => props.rows.filter((row) => row._kind !== 'total'))
const total = computed(() => props.rows.find((row) => row._kind === 'total') || null)
const shown = computed(() => body.value.filter((row) => row._kind === 'row').length)
const pages = computed(() => Math.max(1, Math.ceil(body.value.length / PAGE_SIZE)))
const pageRows = computed(() => body.value.slice(page.value * PAGE_SIZE, (page.value + 1) * PAGE_SIZE))

watch(
  () => props.rows,
  () => {
    page.value = 0
  },
)

function isNumeric(column) {
  return NUMERIC.has(column.fieldtype)
}

function cell(row, column) {
  const value = row[column.fieldname]
  if (value === null || value === undefined || value === '') return ''
  if (DATES.has(column.fieldtype)) return formatDate(value)
  if (isNumeric(column)) return roundHours(value)
  return value
}

function ariaSort(column) {
  if (props.sort?.field !== column.fieldname) return 'none'
  return props.sort.order === 'desc' ? 'descending' : 'ascending'
}

function toggleSort(column) {
  const current = props.sort?.field === column.fieldname ? props.sort.order : null
  // Numbers start descending (biggest first); text starts A-Z.
  const firstOrder = isNumeric(column) ? 'desc' : 'asc'
  const secondOrder = firstOrder === 'desc' ? 'asc' : 'desc'
  if (!current) emit('update:sort', { field: column.fieldname, order: firstOrder })
  else if (current === firstOrder) emit('update:sort', { field: column.fieldname, order: secondOrder })
  else emit('update:sort', null)
}

function toggleColumn(field, show) {
  const next = show ? props.hidden.filter((name) => name !== field) : [...props.hidden, field]
  // Never hide the last column.
  if (available.value.every((column) => next.includes(column.fieldname))) return
  emit('update:hidden', next)
}

function subtotalLabel(row) {
  const value = row[row._group_field]
  return `${value === null || value === undefined || value === '' ? 'Blank' : value} subtotal`
}
</script>

<template>
  <div>
    <div class="mb-2 flex flex-wrap items-center justify-between gap-3">
      <p
        class="text-sm text-ink-gray-6"
        aria-live="polite"
      >
        <template v-if="truncated">
          Showing <span class="tabular">{{ shown }}</span> of <span class="tabular">{{ totalRows }}</span> rows.
        </template>
        <template v-else>
          <span class="tabular">{{ totalRows }}</span> {{ totalRows === 1 ? 'row' : 'rows' }}.
        </template>
      </p>

      <div class="relative">
        <button
          type="button"
          class="inline-flex min-h-11 cursor-pointer items-center rounded-md border border-outline-gray-2 px-3 text-sm text-ink-gray-8 sm:min-h-9"
          :aria-expanded="columnsOpen ? 'true' : 'false'"
          :aria-controls="`${id}-columns`"
          @click="columnsOpen = !columnsOpen"
        >
          Columns<span v-if="hidden.length">&nbsp;({{ hidden.length }} hidden)</span>
        </button>
        <fieldset
          v-show="columnsOpen"
          :id="`${id}-columns`"
          class="surface-card elev-2 absolute right-0 z-20 mt-1 max-h-80 w-64 overflow-y-auto p-3"
        >
          <legend class="sr-only">
            Visible columns
          </legend>
          <label
            v-for="column in available"
            :key="column.fieldname"
            class="flex min-h-9 cursor-pointer items-center gap-2 text-sm text-ink-gray-8"
          >
            <input
              type="checkbox"
              class="size-4"
              :checked="!hidden.includes(column.fieldname)"
              @change="toggleColumn(column.fieldname, $event.target.checked)"
            >
            {{ column.label || column.fieldname }}
          </label>
        </fieldset>
      </div>
    </div>

    <p
      v-if="truncated"
      class="surface-inset mb-3 p-3 text-sm text-ink-gray-7"
      role="status"
    >
      Showing first <span class="tabular">{{ shown }}</span> of <span class="tabular">{{ totalRows }}</span> rows.
      Totals cover all <span class="tabular">{{ totalRows }}</span> rows.
      <slot
        v-if="canExport"
        name="truncated-export"
      >
        Export the report to get every row.
      </slot>
      <template v-else>
        Narrow the filters to see every row.
      </template>
    </p>

    <!-- Scrolls inside its own box (phone width included), so the sticky
         header and total row stay in view; scroll-padding keeps a focused
         row from hiding under either. -->
    <div class="surface-card elev-1 max-h-[70vh] scroll-py-12 overflow-auto">
      <table class="w-full min-w-[40rem] border-separate border-spacing-0 text-sm">
        <caption class="sr-only">
          {{ caption }}
        </caption>
        <thead>
          <tr>
            <th
              v-for="column in visible"
              :key="column.fieldname"
              scope="col"
              :aria-sort="ariaSort(column)"
              class="label sticky top-0 z-10 border-b border-outline-gray-2 bg-surface-white px-3 py-2"
              :class="isNumeric(column) ? 'text-right' : 'text-left'"
            >
              <button
                type="button"
                class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                :class="isNumeric(column) ? 'flex-row-reverse' : ''"
                @click="toggleSort(column)"
              >
                {{ column.label || column.fieldname }}
                <span
                  aria-hidden="true"
                  class="w-3"
                >{{ ariaSort(column) === 'ascending' ? '↑' : ariaSort(column) === 'descending' ? '↓' : '' }}</span>
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, index) in pageRows"
            :key="index"
            :class="row._kind === 'subtotal' ? 'bg-surface-gray-2 font-medium text-ink-gray-9' : 'text-ink-gray-7'"
            :data-kind="row._kind"
          >
            <td
              v-for="(column, columnIndex) in visible"
              :key="column.fieldname"
              class="border-b border-outline-gray-2 px-3 py-2"
              :class="isNumeric(column) ? 'tabular text-right' : 'text-left'"
            >
              <span
                v-if="row._kind === 'subtotal' && columnIndex === 0"
                class="sr-only"
              >{{ subtotalLabel(row) }}: </span>{{ cell(row, column) }}
            </td>
          </tr>
        </tbody>
        <tfoot v-if="total">
          <tr
            class="font-semibold text-ink-gray-9"
            data-kind="total"
          >
            <td
              v-for="(column, columnIndex) in visible"
              :key="column.fieldname"
              class="sticky bottom-0 border-t-2 border-outline-gray-3 bg-surface-white px-3 py-2"
              :class="isNumeric(column) ? 'tabular text-right' : 'text-left'"
            >
              <template v-if="columnIndex === 0 && !isNumeric(column)">
                Total
              </template>
              <template v-else>
                {{ cell(total, column) }}
              </template>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>

    <nav
      v-if="pages > 1"
      class="mt-3 flex items-center justify-end gap-3 text-sm text-ink-gray-7"
      aria-label="Table pages"
    >
      <button
        type="button"
        class="min-h-11 cursor-pointer rounded-md border border-outline-gray-2 px-3 disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-9"
        :disabled="page === 0"
        @click="page -= 1"
      >
        Previous
      </button>
      <span class="tabular">Page {{ page + 1 }} of {{ pages }}</span>
      <button
        type="button"
        class="min-h-11 cursor-pointer rounded-md border border-outline-gray-2 px-3 disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-9"
        :disabled="page >= pages - 1"
        @click="page += 1"
      >
        Next
      </button>
    </nav>
  </div>
</template>
