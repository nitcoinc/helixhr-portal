<script setup>
import Avatar from '@/components/Avatar.vue'
import { ref, computed, watch, nextTick } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import RosterShiftSheet from '@/components/RosterShiftSheet.vue'
import RosterDraftBar from '@/components/RosterDraftBar.vue'
import RosterRequestStrip from '@/components/RosterRequestStrip.vue'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import { session } from '@/lib/session'
import { addCalendarDays, dateTileParts, formatDateRange, mondayOf, today } from '@/lib/dates'
import { offFirst } from '@/lib/lists'
import { isEmptyWeek, leaveLabel, rosterModes, shiftHours } from '@/lib/roster'
import { cellProposal, checkProposal, coverageFromRows, draftOps, submittedMessage } from '@/lib/rosterRequests'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { notify } from '@/lib/notify'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'
import { WEEKDAYS } from '@/lib/week'

// Plan 2026-09-30-001 U9 / R7, R8, R12. One Monday-first week of shifts:
// one row per person, one cell per day, naming the shift Active that day.
//
// The server decides who is on the grid (`mode` is only a request -- a mode
// the caller does not hold comes back as a PermissionError, which AsyncState
// renders as `forbidden`), what each cell holds, and which days are off for
// whom. This file lays that payload out twice, like Team: a grid on desktop,
// a day list on the phone.

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
const debounced = useDebouncedSearch((value) => {
  start.value = 0
  query.value = value.trim()
})
watch(search, debounced.search)

const days = computed(() => roster.data?.days || [])
const serverRows = computed(() => roster.data?.rows || [])

// Plan 2026-10-17-001 U17 (MH-16), the team view only: people off this week
// first, and "Only people off" to hide the rest -- Team's toggle. Everyone
// (HR) stays in the server's paged name order, where a page-local reorder
// would only confuse the paging.
const onlyOff = ref(false)
const OFF_OPTIONS = [
  { value: false, label: 'Everyone' },
  { value: true, label: 'Only people off', testid: 'roster-only-off' },
]
const anyoneOff = computed(() => serverRows.value.some((row) => row.leaves?.length))
watch([anyoneOff, mode], ([value, current]) => {
  if (!value || current !== 'team') onlyOff.value = false
})
const rows = computed(() => {
  if (mode.value !== 'team') return serverRows.value
  const sorted = offFirst(serverRows.value)
  return onlyOff.value ? sorted.filter((row) => row.leaves?.length) : sorted
})
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

/** HR-21: the phone card's one badge, after the day name -- Weekend, or
 * Holiday when it is a holiday for everyone on the card. A holiday only some
 * people have stays on their own line (`rowOffLabel`). */
function dayLabel(day) {
  if (day.is_weekend) return 'Weekend'
  const list = rows.value
  return list.length && list.every((row) => row.holidays.includes(day.date)) ? 'Holiday' : ''
}

function rowOffLabel(day, row) {
  return dayLabel(day) ? '' : offLabel(day, row)
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
  const proposal = proposalAt(row, cell)
  if (proposal) parts.push(proposal.label)
  return parts.join(', ')
}

/** What pressing a cell does, appended to its accessible name. */
function cellAction(row, cell) {
  if (canEdit.value) return cell.assignment ? '. Edit shift' : '. Assign a shift'
  if (!proposable(row)) return ''
  return proposalAt(row, cell) ? '. Edit or remove the proposed change' : '. Propose a change'
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

// --- a manager's roster request (plan 2026-10-31-001 U10 / R10-R12, R17) ---

// The server's answer: team mode with direct reports. The manager's own row
// is never proposable, nor a row already in a Pending request (R12).
const canRequest = computed(() => mode.value === 'team' && !!roster.data?.can_request)
const myEmployee = computed(() => session.employee?.name || '')
function proposable(row) {
  return canRequest.value && row.employee !== myEmployee.value && !row.pending_request
}
function clickable(row) {
  return canEdit.value || proposable(row)
}

// The saved draft as the server last answered it (`get_roster_week` or a
// save): its rows, and `preview`, the simulated coverage the grid overlays.
const draft = ref(null)
watch(
  () => roster.data,
  (data) => {
    if (data?.can_request) draft.value = data.draft || null
  },
)
const ops = computed(() => draftOps(draft.value))
const draftRows = computed(() => draft.value?.rows || [])
const proposals = computed(() => {
  const map = {}
  if (!canRequest.value || !ops.value.length) return map
  for (const row of serverRows.value) {
    for (const cell of row.cells) {
      const proposal = cellProposal(ops.value, draft.value?.preview, row.employee, cell)
      if (proposal) map[`${row.employee}|${cell.date}`] = proposal
    }
  }
  return map
})
function proposalAt(row, cell) {
  return proposals.value[`${row.employee}|${cell.date}`] || null
}

const note = ref('')
watch(
  () => draft.value?.name,
  () => {
    note.value = draft.value?.note || ''
  },
)

const saveCall = createResource({ url: 'helixhr.api.save_roster_draft', method: 'POST' })
const submitCall = createResource({ url: 'helixhr.api.submit_roster_change', method: 'POST' })
const saving = ref(false)
const saveError = ref('')
const submitting = ref(false)
const submitError = ref('')

function plainError(e) {
  return toPlainMessage(e?.messages?.[0] ?? e?.message) || FALLBACK_ERROR
}

/** Replace the draft with `next`. Throws an Error carrying the server's
 * sentence, which names the person, so the caller shows it where it was
 * asked for: the sheet, or the bar. */
let saveInflight = null
async function saveOps(next) {
  saving.value = true
  const call = saveCall.submit({ rows: JSON.stringify(next), note: note.value })
  saveInflight = call
  try {
    const result = await call
    draft.value = result?.draft || null
    saveError.value = ''
    submitError.value = ''
  } catch (e) {
    throw new Error(plainError(e))
  } finally {
    if (saveInflight === call) {
      saveInflight = null
      saving.value = false
    }
  }
}

/** The sheet's Add / Save: the simulator first, against what this week shows,
 * so an obvious refusal costs no round trip; then the server's own check. */
async function proposeChange(op, index) {
  const next = [...ops.value]
  const at = index == null ? next.length : index
  next[at] = op
  const name = serverRows.value.find((row) => row.employee === op.employee)?.employee_name
  const problem = checkProposal({
    ops: next,
    index: at,
    coverage: coverageFromRows(serverRows.value),
    today: roster.data?.today,
    name,
  })
  if (problem) throw new Error(problem)
  await saveOps(next)
}

function removeChange(index) {
  return saveOps(ops.value.filter((_, position) => position !== index))
}

async function fromBar(write) {
  saveError.value = ''
  try {
    await write()
  } catch (e) {
    saveError.value = e.message
  }
}

function saveNote() {
  if (!draftRows.value.length || note.value === (draft.value?.note || '')) return
  return fromBar(() => saveOps(ops.value))
}

const recent = createResource({ url: 'helixhr.api.get_roster_draft' })
watch(canRequest, (value) => value && recent.fetch(), { immediate: true })

/** The grid and the strip again, without the skeleton. */
async function reloadQuietly() {
  refreshing.value = true
  try {
    await Promise.all([roster.reload(), canRequest.value ? recent.reload() : null])
  } catch {
    // AsyncState shows a failed load.
  } finally {
    refreshing.value = false
  }
}

async function submitDraft() {
  if (submitting.value) return
  submitting.value = true
  submitError.value = ''
  saveError.value = ''
  try {
    // A note saved on blur may still be in flight; the submit reads the
    // draft it writes. Its own failure is already on the bar.
    await Promise.resolve(saveInflight).catch(() => {})
    const result = await submitCall.submit({ note: note.value })
    draft.value = null
    note.value = ''
    notify.success(submittedMessage(result?.names))
  } catch (e) {
    submitError.value = plainError(e)
    return
  } finally {
    submitting.value = false
  }
  await reloadQuietly()
}

function onRequestsChanged() {
  return reloadQuietly()
}

// R19 / R12: who already has a change waiting for HR, by name.
const pendingNames = computed(() =>
  serverRows.value.filter((row) => row.pending_request).map((row) => row.employee_name),
)

const sheetOpen = ref(false)
const selected = ref(null)
// The `data-cell-key` of the control that opened the sheet, so focus goes
// back to it -- after the refetch, which re-renders the cell.
let returnKey = ''

function openCell(row, cell, layout) {
  if (!clickable(row)) return
  returnKey = `${row.employee}|${cell.date}|${layout}`
  const proposal = canRequest.value ? proposalAt(row, cell) : null
  selected.value = {
    person: { employee: row.employee, employee_name: row.employee_name },
    cell,
    proposal: proposal ? { ...proposal, op: ops.value[proposal.index] || null } : null,
  }
  sheetOpen.value = true
}

// The dialog restores focus itself when it unmounts after its leave
// transition, and that can land after a refetch re-rendered the cell -- so
// this waits for the dialog to be gone (bounded, ~1s) before focusing.
function focusReturn(frames = 60) {
  nextTick(() => {
    if (!returnKey) return
    if (document.querySelector('[role="dialog"]') && frames > 0) {
      requestAnimationFrame(() => focusReturn(frames - 1))
      return
    }
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
// MH-17: your own empty week is one card, not seven saying "No shift".
const mineEmpty = computed(() => quietWeek.value && mode.value === 'mine')
const mine = computed(() => rows.value[0] || null)
// The legend's "(default)" note, only when a "(default)" is on screen.
const showsDefault = computed(() =>
  rows.value.some((row) => row.default_shift && row.cells.some((cell) => !cell.shift_type)),
)

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
    <SegmentedChips
      v-if="modes.length > 1"
      class="mb-4"
      label="Whose shifts"
      :options="modes"
      :model-value="mode"
      @update:model-value="setMode"
    />

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

    <!-- Plan 2026-10-31-001 U10: the manager's recent requests, above the
         grid they change. -->
    <RosterRequestStrip
      v-if="mode === 'team'"
      :requests="recent.data?.recent || []"
      @changed="onRequestsChanged"
    />

    <div
      v-if="mode === 'hr'"
      class="mb-4 max-w-sm"
    >
      <SearchField
        v-model="search"
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
      <!-- MH-17: one card for your own empty week, with the way out on it. -->
      <section
        v-if="mineEmpty"
        class="surface-card elev-1 p-4"
        data-testid="roster-quiet"
      >
        <p class="font-medium text-ink-gray-9">
          No shifts are assigned this week.
        </p>
        <p
          v-if="mine?.default_shift"
          class="mt-1 text-sm text-ink-gray-6"
        >
          Check-in uses your default shift,
          <span class="tabular">{{ [mine.default_shift.shift_type, shiftHours(mine.default_shift)].filter(Boolean).join(' ') }}</span>.
        </p>
        <p
          v-for="leave in mine?.leaves || []"
          :key="leave.name"
          class="mt-1 text-sm text-ink-gray-6"
        >
          On leave {{ formatDateRange(leave.start, leave.end) }}: {{ leaveLabel(mine, leave.start) }}.
        </p>
        <button
          type="button"
          class="mt-3 inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
          @click="nextWeek"
        >
          Go to next week
        </button>
      </section>
      <p
        v-else-if="quietWeek"
        class="mb-3 text-sm text-ink-gray-6"
        data-testid="roster-quiet"
      >
        No shifts are assigned this week. Use the arrows to look at another week.
      </p>

      <!-- U17 (MH-16): the team view's toggle, Team's own. -->
      <template v-if="mode === 'team'">
        <SegmentedChips
          v-model="onlyOff"
          class="mb-3"
          label="Whose shifts to show"
          :options="OFF_OPTIONS"
          :disabled="!anyoneOff"
          :aria-describedby="anyoneOff ? undefined : 'roster-nobody-off'"
        />
        <p
          v-if="!anyoneOff"
          id="roster-nobody-off"
          class="mb-3 text-sm text-ink-gray-6"
        >
          Nobody is off this week.
        </p>
        <p
          v-if="canRequest"
          class="mb-3 text-sm text-ink-gray-6"
        >
          Choose a day on someone's row to propose a shift change. Changes go to HR for approval
          and start tomorrow at the earliest.
        </p>
        <!-- R12: one Pending request per person, so their cells take no new
             proposal until HR decides or the request is withdrawn. -->
        <p
          v-if="canRequest && pendingNames.length"
          class="mb-3 text-sm text-ink-gray-7"
          data-testid="roster-pending-note"
        >
          A change for {{ pendingNames.join(', ') }} is already waiting for HR, so you can't propose
          more for {{ pendingNames.length === 1 ? 'them' : 'those people' }} until HR decides or you
          withdraw it.
        </p>
      </template>

      <!-- Desktop: the week across. Scrolls inside its own container, never
           the page. Table roles so a screen reader can move by row and
           column; the visible header row is the column header. -->
      <div
        v-if="!mineEmpty"
        class="hidden lg:block"
      >
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
              <!-- The header keeps its grid cell: an `sr-only` element is
                   absolutely positioned, so on its own it would drop out of
                   the grid and shift every day one column left. -->
              <span role="columnheader"><span class="sr-only">Person</span></span>
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
                  :size="32"
                />
                <span class="min-w-0">
                  <span class="block truncate text-sm font-medium text-ink-gray-9">
                    {{ row.employee_name }}
                  </span>
                  <!-- R19: a manager has asked HR to change this person's shifts. -->
                  <span
                    v-if="row.pending_request"
                    class="mt-0.5 inline-block rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
                    data-testid="roster-pending-pill"
                    :title="mode === 'team' ? 'Waiting for HR. Nothing new can be proposed until HR decides.' : 'A manager has asked to change these shifts. It is waiting in Approvals.'"
                  >Change pending</span>
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
                <!-- Plan 2026-10-31-001 U10: a proposed day carries a dashed
                     edge *and* the words "Proposed: <shift>" -- never the
                     outline alone. -->
                <component
                  :is="clickable(row) ? 'button' : 'div'"
                  :type="clickable(row) ? 'button' : undefined"
                  class="flex h-full min-h-14 w-full flex-col justify-center gap-0.5 rounded border-2 px-2 py-1.5 text-left"
                  :class="[
                    isOff(days[index] || {}, row) ? 'bg-surface-gray-3' : 'bg-surface-gray-1',
                    clickable(row) && 'cursor-pointer hover:ring-1 hover:ring-outline-gray-3',
                    proposalAt(row, cell) ? 'border-dashed border-[color:var(--ink-gray-7)]' : 'border-transparent',
                  ]"
                  :aria-label="cellLabel(row, cell, index) + cellAction(row, cell)"
                  :data-cell-key="`${row.employee}|${cell.date}|grid`"
                  :data-proposed="proposalAt(row, cell) ? '' : undefined"
                  @click="openCell(row, cell, 'grid')"
                >
                  <template v-if="cell.shift_type">
                    <span
                      class="truncate text-xs font-medium text-ink-gray-9"
                      :title="cell.shift_type"
                    >{{ cell.shift_type }}</span>
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
                  <span
                    v-if="proposalAt(row, cell)"
                    class="truncate text-xs font-bold text-ink-gray-9"
                    :title="proposalAt(row, cell).label"
                  >{{ proposalAt(row, cell).label }}</span>
                </component>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Phone: the same week read downwards, one card per day. -->
      <ul
        v-if="!mineEmpty"
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
                v-if="dayLabel(day)"
                class="ml-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
              >{{ dayLabel(day) }}</span>
            </p>
            <ul class="mt-1.5 space-y-1.5">
              <li
                v-for="{ row, cell } in day.people"
                :key="row.employee"
                class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"
              >
                <!-- HR-21: your own avatar on every day says nothing. -->
                <Avatar
                  v-if="mode !== 'mine'"
                  class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  :photo-url="row.photo_url"
                  :initials="row.initials"
                  :size="28"
                />
                <span
                  v-if="mode !== 'mine'"
                  class="min-w-16 flex-1 truncate text-sm text-ink-gray-7"
                >
                  {{ row.employee_name }}
                </span>
                <span
                  class="min-w-0 truncate text-sm"
                  :class="[mode === 'mine' ? 'flex-1' : 'max-w-[60%]', cell.shift_type ? 'font-medium text-ink-gray-9' : 'text-ink-gray-5']"
                >
                  <template v-if="cell.shift_type">
                    {{ cell.shift_type }}
                    <span class="tabular font-normal text-ink-gray-6">{{ shiftHours(cell) }}</span>
                  </template>
                  <template v-else-if="row.default_shift">{{ row.default_shift.shift_type }} (default)</template>
                  <template v-else>No shift</template>
                </span>
                <span
                  v-if="rowOffLabel(day, row)"
                  class="shrink-0 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
                >{{ rowOffLabel(day, row) }}</span>
                <span
                  v-if="leaveLabel(row, day.date)"
                  class="shrink-0 rounded-full bg-surface-green-2 px-2 py-0.5 text-xs font-medium text-ink-green-3"
                >{{ leaveLabel(row, day.date) }}</span>
                <span
                  v-if="row.pending_request"
                  class="shrink-0 rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
                >Change pending</span>
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
                <button
                  v-else-if="proposable(row)"
                  type="button"
                  class="-my-2 inline-flex min-h-11 shrink-0 cursor-pointer items-center rounded px-1 text-sm font-medium text-ink-blue-link underline underline-offset-2"
                  :class="proposalAt(row, cell) ? 'border-2 border-dashed border-[color:var(--ink-gray-7)] no-underline' : ''"
                  :aria-label="
                    proposalAt(row, cell)
                      ? `${proposalAt(row, cell).label}. Edit or remove ${row.employee_name}'s proposed change on ${day.weekday}`
                      : `Propose a change to ${row.employee_name}'s shift on ${day.weekday}`
                  "
                  :data-cell-key="`${row.employee}|${cell.date}|list`"
                  @click="openCell(row, cell, 'list')"
                >
                  {{ proposalAt(row, cell) ? proposalAt(row, cell).label : 'Propose' }}
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

      <!-- HR-21: the phone's badges already say "On leave", "Weekend" and
           "Holiday" in words, so the swatches are the grid's (desktop). -->
      <div
        v-if="!mineEmpty"
        class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-gray-6"
        aria-label="Legend"
      >
        <!-- MH-20: the pastel fills measure ~1.2:1 on paper; each swatch carries
             its own ink edge (>= 3:1), as Attendance's legend does (ED-16). -->
        <span class="hidden items-center gap-1.5 lg:inline-flex">
          <span
            class="h-3 w-6 rounded border bg-surface-green-2 border-[color:var(--ink-green-3)]"
            aria-hidden="true"
          />
          On leave
        </span>
        <span class="hidden items-center gap-1.5 lg:inline-flex">
          <span
            class="h-3 w-6 rounded border bg-surface-gray-3 border-[color:var(--ink-gray-5)]"
            aria-hidden="true"
          />
          Weekend or holiday
        </span>
        <span
          v-if="Object.keys(proposals).length"
          class="inline-flex items-center gap-1.5"
        >
          <span
            class="h-3 w-6 rounded border-2 border-dashed border-[color:var(--ink-gray-7)]"
            aria-hidden="true"
          />
          Proposed in your draft, not yet approved
        </span>
        <span v-if="showsDefault">“(default)” is the shift used when none is assigned.</span>
      </div>

      <!-- MH-17: with the switcher on screen, "Your own shifts" only repeats
           the chip that is on. -->
      <p
        v-if="mode !== 'mine' || modes.length === 1"
        class="mt-3 text-sm text-ink-gray-5"
      >
        {{ scopeLine }} Only this week.
      </p>
    </AsyncState>

    <RosterShiftSheet
      v-if="canEdit || canRequest"
      v-model="sheetOpen"
      :mode="canEdit ? 'hr' : 'propose'"
      :person="selected?.person"
      :cell="selected?.cell"
      :proposal="selected?.proposal"
      :shift-types="roster.data?.shift_types || []"
      :today="roster.data?.today || ''"
      :propose-change="proposeChange"
      :remove-change="removeChange"
      @changed="onChanged"
    />

    <RosterDraftBar
      v-if="canRequest && draftRows.length"
      v-model:note="note"
      :rows="draftRows"
      :problems="draft?.problems || []"
      :saving="saving"
      :save-error="saveError"
      :submitting="submitting"
      :submit-error="submitError"
      @remove="(index) => fromBar(() => removeChange(index))"
      @discard="fromBar(() => saveOps([]))"
      @submit="submitDraft"
      @save-note="saveNote"
    />
  </div>
</template>
