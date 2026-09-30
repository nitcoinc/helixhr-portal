<script setup>
import Avatar from '@/components/Avatar.vue'
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { createResource, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import RosterShiftSheet from '@/components/RosterShiftSheet.vue'
import { session } from '@/lib/session'
import { addCalendarDays, dateTileParts, formatDateRange, mondayOf, today } from '@/lib/dates'
import { isEmptyWeek, leaveLabel, rosterModes, shiftHours } from '@/lib/roster'

// Plan 2026-09-30-001 U9 / R7, R8, R12. One Monday-first week of shifts:
// one row per person, one cell per day, naming the shift Active that day.
//
// The server decides who is on the grid (`mode` is only a request -- a mode
// the caller does not hold comes back as a PermissionError, which AsyncState
// renders as `forbidden`), what each cell holds, and which days are off for
// whom. This file lays that payload out twice, like Team: a grid on desktop,
// a day list on the phone.
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const modes = computed(() => rosterModes(session))
const mode = ref(modes.value[0]?.value || 'mine')

// Component state, not a route parameter, for Team's reason: a roster week
// is nobody's document.
const weekStart = ref(mondayOf(today()))
const weekEnd = computed(() => addCalendarDays(weekStart.value, 6))

// HR only: the server pages the rows at 50 with an exact total.
const search = ref('')
const query = ref('')
const start = ref(0)

const roster = createResource({
  url: 'helixhr.api.get_roster_week',
  makeParams: () => ({
    week_start: weekStart.value,
    mode: mode.value,
    search: mode.value === 'hr' ? query.value || undefined : undefined,
    start: start.value,
  }),
  auto: true,
})
watch([weekStart, mode, query, start], () => roster.fetch())

function setMode(value) {
  if (mode.value === value) return
  search.value = ''
  query.value = ''
  start.value = 0
  mode.value = value
}

// Debounced like Directory's search, so a name is not asked for per key.
let searchTimer = null
watch(search, (value) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    start.value = 0
    query.value = value.trim()
  }, 250)
})
onUnmounted(() => clearTimeout(searchTimer))

const days = computed(() => roster.data?.days || [])
const rows = computed(() => roster.data?.rows || [])
const total = computed(() => roster.data?.total || 0)
const limit = computed(() => roster.data?.limit || 50)
const isCurrentWeek = computed(() =>
  roster.data ? roster.data.is_current_week : weekStart.value === mondayOf(today()),
)

function prevWeek() {
  weekStart.value = addCalendarDays(weekStart.value, -7)
}
function nextWeek() {
  weekStart.value = addCalendarDays(weekStart.value, 7)
}
function thisWeek() {
  weekStart.value = mondayOf(today())
}

const pageFrom = computed(() => (total.value ? start.value + 1 : 0))
const pageTo = computed(() => start.value + rows.value.length)
const hasPrevPage = computed(() => start.value > 0)
const hasNextPage = computed(() => pageTo.value < total.value)
function prevPage() {
  start.value = Math.max(0, start.value - limit.value)
}
function nextPage() {
  start.value += limit.value
}

// --- one cell -----------------------------------------------------------

function isOff(day, row) {
  return day.is_weekend || row.holidays.includes(day.date)
}

function offLabel(day, row) {
  if (row.holidays.includes(day.date)) return 'Holiday'
  return day.is_weekend ? 'Weekend' : ''
}

/** What an assistive reader hears for one cell: the person, the day and
 * everything drawn in it, in one sentence. */
function cellLabel(row, cell, index) {
  const parts = [`${row.employee_name}, ${WEEKDAYS[index]} ${tile(cell.date).day}`]
  if (cell.shift_type) parts.push(`${cell.shift_type} ${shiftHours(cell)}`.trim())
  else if (row.default_shift) parts.push(`no shift assigned, default ${row.default_shift.shift_type}`)
  else parts.push('no shift')
  const leave = leaveLabel(row, cell.date)
  if (leave) parts.push(`on leave: ${leave}`)
  const off = offLabel(days.value[index] || {}, row)
  if (off) parts.push(off)
  return parts.join(', ')
}

function tile(date) {
  return dateTileParts(date) || {}
}

// --- the phone's day-first shape ----------------------------------------

const dayRows = computed(() =>
  days.value.map((day, index) => ({
    ...day,
    index,
    people: rows.value.map((row) => ({ row, cell: row.cells[index] })),
  })),
)

// --- HR editing (U10) ----------------------------------------------------

// `can_edit` is the server's answer (HR mode plus Shift Assignment create);
// without it no cell is a button, so an employee has nothing to press.
const canEdit = computed(() => !!roster.data?.can_edit)
const sheetOpen = ref(false)
const selected = ref(null)
// The `data-cell-key` of the control that opened the sheet, so focus goes
// back to it -- after the refetch, which re-renders the cell.
let returnKey = ''

function openCell(row, cell, layout) {
  returnKey = `${row.employee}|${cell.date}|${layout}`
  selected.value = { person: { employee: row.employee, employee_name: row.employee_name }, cell }
  sheetOpen.value = true
}

function focusReturn() {
  nextTick(() => {
    if (!returnKey) return
    document.querySelector(`[data-cell-key="${CSS.escape(returnKey)}"]`)?.focus()
  })
}

// No optimistic edit: the grid is whatever the server says after the write.
// The refetch keeps the grid mounted (no skeleton), so the cell that opened
// the sheet is still the element focus returns to.
const refreshing = ref(false)
async function onChanged() {
  refreshing.value = true
  try {
    await roster.reload()
  } finally {
    refreshing.value = false
  }
  focusReturn()
}
watch(sheetOpen, (value) => {
  if (!value) focusReturn()
})

// --- empty and quiet ----------------------------------------------------

const noRows = computed(() => !!roster.data && rows.value.length === 0)
const emptyTitle = computed(() =>
  query.value ? 'Nobody matches that search' : 'Nobody to show on this roster',
)
const emptyBody = computed(() =>
  query.value ? 'Try a different name or employee ID.' : 'Ask HR if that looks wrong.',
)
const quietWeek = computed(() => !noRows.value && isEmptyWeek(rows.value))

const scopeLine = computed(() => {
  if (mode.value === 'hr') return 'Everyone active in the companies you look after.'
  if (mode.value === 'team') return 'You and the people who report to you.'
  return 'Your own shifts.'
})
</script>

<template>
  <div>
    <PageHeader title="Roster" />

    <!-- Whose shifts. Only the modes this session holds; one mode draws no
         switcher at all. -->
    <div
      v-if="modes.length > 1"
      class="mb-4 flex flex-wrap gap-2"
      role="group"
      aria-label="Whose shifts"
    >
      <button
        v-for="option in modes"
        :key="option.value"
        type="button"
        class="min-h-11 cursor-pointer rounded-full border px-4 text-sm font-medium transition-colors duration-200"
        :class="
          mode === option.value
            ? 'border-field bg-field text-white'
            : 'border-outline-gray-2 bg-surface-white text-ink-gray-8 hover:bg-surface-gray-2'
        "
        :aria-pressed="mode === option.value"
        @click="setMode(option.value)"
      >
        {{ option.label }}
      </button>
    </div>

    <!-- Week navigation, outside the async region like Team's. -->
    <div class="mb-4 flex items-center gap-2">
      <button
        class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2"
        type="button"
        aria-label="Previous week"
        @click="prevWeek"
      >
        <Icon name="chevronLeft" />
      </button>
      <button
        class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2"
        type="button"
        aria-label="Next week"
        @click="nextWeek"
      >
        <Icon name="chevronRight" />
      </button>
      <h2 class="type-section tabular ml-1 min-w-0 truncate text-ink-gray-9">
        {{ formatDateRange(weekStart, weekEnd) }}
      </h2>
      <button
        v-if="!isCurrentWeek"
        class="-my-2 ml-auto inline-flex min-h-11 shrink-0 cursor-pointer items-center text-sm font-medium text-ink-blue-link underline underline-offset-2"
        type="button"
        @click="thisWeek"
      >
        This week
      </button>
    </div>

    <div
      v-if="mode === 'hr'"
      class="mb-4 max-w-sm"
    >
      <FormControl
        v-model="search"
        type="text"
        label="Search"
        placeholder="Name or employee ID"
      />
    </div>

    <AsyncState
      section="roster"
      :resource="roster"
      :loading="roster.loading && !refreshing"
      :empty="noRows"
      :empty-title="emptyTitle"
      :empty-body="emptyBody"
      skeleton="card"
      skeleton-height="h-40"
    >
      <p
        v-if="quietWeek"
        class="mb-3 text-sm text-ink-gray-6"
        data-testid="roster-quiet"
      >
        No shifts are assigned this week. Use the arrows to look at another week.
      </p>

      <!-- Desktop: the week across. Scrolls inside its own container, never
           the page. Table roles so a screen reader can move by row and
           column; the visible header row is the column header. -->
      <div class="hidden lg:block">
        <div class="surface-card elev-1 overflow-x-auto p-4">
          <div
            class="min-w-[48rem]"
            role="table"
            :aria-label="`Shifts, ${formatDateRange(weekStart, weekEnd)}`"
          >
            <div
              class="grid grid-cols-[11rem_repeat(7,minmax(0,1fr))] gap-1"
              role="row"
            >
              <span
                role="columnheader"
                class="sr-only"
              >Person</span>
              <div
                v-for="(day, index) in days"
                :key="day.date"
                class="text-center"
                role="columnheader"
              >
                <p class="label !text-ink-gray-5">
                  {{ WEEKDAYS[index] }}
                </p>
                <p
                  class="tabular text-sm font-medium"
                  :class="day.is_weekend ? 'text-ink-gray-5' : 'text-ink-gray-9'"
                >
                  {{ tile(day.date).day }}
                </p>
              </div>
            </div>

            <div
              v-for="row in rows"
              :key="row.employee"
              class="mt-2 grid grid-cols-[11rem_repeat(7,minmax(0,1fr))] items-stretch gap-1 border-t border-outline-gray-2 pt-2"
              role="row"
              data-testid="roster-row"
              :data-employee="row.employee_name"
            >
              <div
                class="flex min-w-0 items-center gap-2 pr-2"
                role="rowheader"
              >
                <Avatar
                  class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  :photo-url="row.photo_url"
                  :initials="row.initials"
                  :name="row.employee_name"
                  :size="32"
                />
                <span class="min-w-0 truncate text-sm font-medium text-ink-gray-9">
                  {{ row.employee_name }}
                </span>
              </div>

              <div
                v-for="(cell, index) in row.cells"
                :key="cell.date"
                role="cell"
                class="min-w-0"
                data-testid="roster-cell"
                :data-date="cell.date"
              >
                <component
                  :is="canEdit ? 'button' : 'div'"
                  :type="canEdit ? 'button' : undefined"
                  class="flex h-full min-h-14 w-full flex-col justify-center gap-0.5 rounded px-2 py-1.5 text-left"
                  :class="[
                    isOff(days[index] || {}, row) ? 'bg-surface-gray-3' : 'bg-surface-gray-1',
                    canEdit && 'cursor-pointer hover:ring-1 hover:ring-outline-gray-3',
                  ]"
                  :aria-label="cellLabel(row, cell, index) + (canEdit ? (cell.assignment ? '. Edit shift' : '. Assign a shift') : '')"
                  :data-cell-key="`${row.employee}|${cell.date}|grid`"
                  @click="canEdit && openCell(row, cell, 'grid')"
                >
                  <template v-if="cell.shift_type">
                    <span class="truncate text-xs font-medium text-ink-gray-9">{{ cell.shift_type }}</span>
                    <span class="tabular truncate text-xs text-ink-gray-6">{{ shiftHours(cell) }}</span>
                  </template>
                  <span
                    v-else-if="row.default_shift"
                    class="truncate text-xs text-ink-gray-5"
                    :title="`Default shift: ${row.default_shift.shift_type}`"
                  >{{ row.default_shift.shift_type }} (default)</span>
                  <span
                    v-else
                    class="text-xs text-ink-gray-5"
                    aria-hidden="true"
                  >—</span>
                  <span
                    v-if="leaveLabel(row, cell.date)"
                    class="truncate rounded bg-surface-green-2 px-1.5 text-xs font-medium text-ink-green-3"
                  >{{ leaveLabel(row, cell.date) }}</span>
                </component>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Phone: the same week read downwards, one card per day. -->
      <ul
        class="space-y-2 lg:hidden"
        data-testid="roster-days"
      >
        <li
          v-for="day in dayRows"
          :key="day.date"
          class="surface-card elev-1 flex gap-3 p-3"
          data-testid="roster-day"
          :data-date="day.date"
        >
          <span
            class="date-tile mt-0.5"
            aria-hidden="true"
          >
            <span class="date-tile-month">{{ tile(day.date).month }}</span>
            <span class="date-tile-day">{{ tile(day.date).day }}</span>
          </span>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-ink-gray-9">
              {{ day.weekday }}
              <span
                v-if="day.is_weekend"
                class="ml-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
              >Weekend</span>
            </p>
            <ul class="mt-1.5 space-y-1.5">
              <li
                v-for="{ row, cell } in day.people"
                :key="row.employee"
                class="flex min-w-0 items-center gap-2"
              >
                <Avatar
                  class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  :photo-url="row.photo_url"
                  :initials="row.initials"
                  :name="row.employee_name"
                  :size="28"
                />
                <span
                  v-if="mode !== 'mine'"
                  class="min-w-0 flex-1 truncate text-sm text-ink-gray-7"
                >
                  {{ row.employee_name }}
                </span>
                <span
                  class="min-w-0 truncate text-sm"
                  :class="[mode === 'mine' ? 'flex-1' : 'shrink-0', cell.shift_type ? 'font-medium text-ink-gray-9' : 'text-ink-gray-5']"
                >
                  <template v-if="cell.shift_type">
                    {{ cell.shift_type }}
                    <span class="tabular font-normal text-ink-gray-6">{{ shiftHours(cell) }}</span>
                  </template>
                  <template v-else-if="row.default_shift">{{ row.default_shift.shift_type }} (default)</template>
                  <template v-else>No shift</template>
                </span>
                <span
                  v-if="offLabel(day, row) && !day.is_weekend"
                  class="shrink-0 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
                >{{ offLabel(day, row) }}</span>
                <span
                  v-if="leaveLabel(row, day.date)"
                  class="shrink-0 rounded-full bg-surface-green-2 px-2 py-0.5 text-xs font-medium text-ink-green-3"
                >{{ leaveLabel(row, day.date) }}</span>
                <button
                  v-if="canEdit"
                  type="button"
                  class="-my-2 inline-flex min-h-11 shrink-0 cursor-pointer items-center text-sm font-medium text-ink-blue-link underline underline-offset-2"
                  :aria-label="`${cell.assignment ? 'Edit' : 'Assign'} ${row.employee_name}'s shift on ${day.weekday}`"
                  :data-cell-key="`${row.employee}|${cell.date}|list`"
                  @click="openCell(row, cell, 'list')"
                >
                  {{ cell.assignment ? 'Edit' : 'Assign' }}
                </button>
              </li>
            </ul>
          </div>
        </li>
      </ul>

      <!-- HR paging: the server's exact total, 50 rows at a time. -->
      <div
        v-if="mode === 'hr' && total > limit"
        class="mt-4 flex flex-wrap items-center gap-3 text-sm text-ink-gray-7"
      >
        <span class="tabular">{{ pageFrom }}–{{ pageTo }} of {{ total }}</span>
        <button
          type="button"
          class="min-h-11 cursor-pointer rounded-lg border border-outline-gray-2 px-3 hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!hasPrevPage"
          @click="prevPage"
        >
          Previous
        </button>
        <button
          type="button"
          class="min-h-11 cursor-pointer rounded-lg border border-outline-gray-2 px-3 hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!hasNextPage"
          @click="nextPage"
        >
          Next
        </button>
      </div>

      <div
        class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-gray-6"
        aria-label="Legend"
      >
        <span class="inline-flex items-center gap-1.5">
          <span
            class="h-3 w-6 rounded bg-surface-green-2"
            aria-hidden="true"
          />
          On leave
        </span>
        <span class="inline-flex items-center gap-1.5">
          <span
            class="h-3 w-6 rounded bg-surface-gray-3"
            aria-hidden="true"
          />
          Weekend or holiday
        </span>
        <span>“(default)” is the shift used when none is assigned.</span>
      </div>

      <p class="mt-3 text-sm text-ink-gray-5">
        {{ scopeLine }} Only this week.
      </p>
    </AsyncState>

    <RosterShiftSheet
      v-if="canEdit"
      v-model="sheetOpen"
      :person="selected?.person"
      :cell="selected?.cell"
      :shift-types="roster.data?.shift_types || []"
      :today="roster.data?.today || ''"
      @changed="onChanged"
    />
  </div>
</template>
