<script setup>
import Avatar from '@/components/Avatar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ref, computed, watch } from 'vue'
import { createResource, Dialog, Button } from 'frappe-ui'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { addCalendarDays, dateTileParts, formatDateRange, mondayOf, today } from '@/lib/dates'

// P3-U7 / P3-R20, P3-R21. One week of the team's leave, and nothing else.
//
// The page asks one question -- "who is out, and when" -- so it renders one
// week at a time and never a reason. The server decides who is on the grid
// (active direct reports only), which days are non-working, and where each
// bar starts and stops inside the week; this file lays that out twice, once
// per device, from the same payload. There is no leave arithmetic here.
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Plan 2026-10-04-003 R1: the Team page is two tabs -- Leaves | Timesheets
// -- and both the tab and the week live in the URL, so a manager can send
// "the timesheets tab, last week" as a link and land on exactly that.
const route = useRoute()
const router = useRouter()
const TABS = [
  { name: 'leaves', label: 'Leaves' },
  { name: 'timesheets', label: 'Timesheets' },
]
const tab = computed(() => (route.query.tab === 'timesheets' ? 'timesheets' : 'leaves'))
const weekStart = computed(() => {
  const asked = typeof route.query.week === 'string' ? route.query.week : ''
  return /^\d{4}-\d{2}-\d{2}$/.test(asked) ? mondayOf(asked) : mondayOf(today())
})
const weekEnd = computed(() => addCalendarDays(weekStart.value, 6))

function pushQuery(query) {
  router.replace({ query })
}
function showTab(name) {
  pushQuery({ ...route.query, tab: name, week: weekStart.value })
}
/** Arrow keys move between the two tabs, the way a `role="tablist"` is
 * expected to (the same pattern the Requests page uses). */
function cycleTab(delta) {
  const names = TABS.map((item) => item.name)
  showTab(names[(names.indexOf(tab.value) + delta + names.length) % names.length])
}
function goToWeek(iso) {
  pushQuery({ ...route.query, tab: tab.value, week: iso })
}
function prevWeek() {
  goToWeek(addCalendarDays(weekStart.value, -7))
}
function nextWeek() {
  goToWeek(addCalendarDays(weekStart.value, 7))
}
function thisWeek() {
  goToWeek(mondayOf(today()))
}

const team = createResource({
  url: 'helixhr.api.get_my_team_week',
  makeParams: () => ({ week_start: weekStart.value }),
  auto: true,
})
watch(weekStart, () => team.fetch())
const teamTimesheets = createResource({
  url: 'helixhr.api.get_my_team_timesheets',
  makeParams: () => ({ week_start: weekStart.value }),
})
// The timesheet half loads only when its tab is on screen: a manager who
// lives on the leave grid never pays for the other feed.
watch(
  [tab, weekStart],
  ([active]) => {
    if (active === 'timesheets') teamTimesheets.fetch()
  },
  { immediate: true },
)

const days = computed(() => team.data?.days || [])
const reports = computed(() => team.data?.reports || [])
const outToday = computed(() => team.data?.out_today || [])
const waitingCount = computed(() => team.data?.waiting_count || 0)
const totalReports = computed(() => team.data?.total_reports || 0)
// The server's own answer once it has one -- it decided `out_today` from the
// same comparison -- and this page's local calendar until then, so the
// arrows and the label are right on the first painted frame.
const isCurrentWeek = computed(() =>
  team.data ? team.data.is_current_week : weekStart.value === mondayOf(today()),
)
const notShown = computed(() => Math.max(0, totalReports.value - reports.value.length))

const hasLeave = computed(() => reports.value.some((row) => row.leaves.length))

// --- the timesheets tab (plan 2026-10-04-003 U4, R2) -----------------------

const timesheetReports = computed(() => teamTimesheets.data?.reports || [])
const timesheetNotShown = computed(
  () => Math.max(0, (teamTimesheets.data?.total_reports || 0) - timesheetReports.value.length),
)

/** The seven dates of the week, for the per-day strip: the payload carries
 * hours keyed by date, so the strip needs the calendar, not another field. */
const weekDates = computed(() =>
  Array.from({ length: 7 }, (_, offset) => addCalendarDays(weekStart.value, offset)),
)

function dayHours(row, date) {
  const value = row.hours?.[date]
  return value ? Math.round(value * 10) / 10 : null
}

function hoursLabel(row) {
  const total = (row.total_hours || 0).toFixed(1).replace(/\.0$/, '')
  if (row.expected_hours == null) return `${total} h`
  const expected = Number(row.expected_hours).toFixed(1).replace(/\.0$/, '')
  return `${total} of ${expected} h`
}

/** "Acme rollout 12 h · Internal 2 h" -- the split as plain text, newest
 * total first, because a row that needs opening to be understood is a row
 * nobody opens (R2). */
function projectLabel(row) {
  const parts = (row.projects || []).map(
    (entry) => `${entry.project_name || entry.project} ${Number(entry.hours).toFixed(1).replace(/\.0$/, '')} h`,
  )
  return parts.join(' · ')
}

// --- the read-only week drawer (R3) ---------------------------------------

const memberWeek = createResource({
  url: 'helixhr.api.get_team_member_week',
  makeParams: () => ({ employee: memberName.value, week_start: weekStart.value }),
})
const memberName = ref('')
const memberOpen = computed(() => !!memberName.value && !!memberWeek.data)

function openMember(report) {
  if (!report.timesheet) return
  memberName.value = report.employee
  memberWeek.fetch()
}
function closeMember() {
  memberName.value = ''
}

/** The drawer's tasks-by-day grid: one line per project/task, a cell per
 * day, the same shape the decision screen reads. */
const memberLines = computed(() => {
  const rows = memberWeek.data?.timesheet?.rows || []
  const lines = []
  const index = {}
  for (const row of rows) {
    const key = `${row.project}|${row.task || ''}`
    let line = index[key]
    if (!line) {
      line = { project: row.project, task: row.task, cells: {}, total: 0 }
      index[key] = line
      lines.push(line)
    }
    if (row.date) line.cells[row.date] = (line.cells[row.date] || 0) + Number(row.hours || 0)
    line.total += Number(row.hours || 0)
  }
  return lines
})
const memberHours = computed(() => memberWeek.data?.timesheet?.hours || {})
const memberDates = computed(() =>
  Array.from({ length: 7 }, (_, offset) => addCalendarDays(weekStart.value, offset)),
)
const memberNameLabel = computed(() => {
  const row = timesheetReports.value.find((entry) => entry.employee === memberName.value)
  return row?.employee_name || ''
})

/** The drawer's headline reads the same "of expected" sentence the row does,
 * so the two can never disagree about what a week should have been. */
function memberHoursOf(timesheet) {
  return timesheet || { total_hours: 0, expected_hours: null }
}

// --- the grid -----------------------------------------------------------

/** Which of the seven columns a date sits in, 1-based for `grid-column`.
 * The server has already clipped every bar to this week, so a date that is
 * not in the week cannot reach here. */
const columnOf = computed(() => {
  const index = {}
  days.value.forEach((day, position) => {
    index[day.date] = position + 1
  })
  return index
})

function barStyle(leave, row) {
  const start = columnOf.value[leave.start] || 1
  const end = columnOf.value[leave.end] || start
  return { gridColumn: `${start} / ${end + 1}`, gridRow: String(row + 1) }
}

function trackStyle(report) {
  return { gridTemplateRows: `repeat(${Math.max(1, report.leaves.length)}, 1.75rem)` }
}

/** Approved and waiting are the two measured status pairs from
 * docs/design-system.md. The word is on the bar as well as the tint, so
 * nothing here means anything by colour alone. */
function barTone(leave) {
  return leave.waiting
    ? 'bg-surface-amber-1 text-ink-amber-3'
    : 'bg-surface-green-2 text-ink-green-3'
}

function barLabel(leave) {
  const parts = [leave.leave_type]
  if (leave.half_day) parts.push('half day')
  if (leave.waiting) parts.push('waiting')
  return parts.join(' · ')
}

/** A day nobody is expected at work on, for this person: the weekend, or a
 * holiday on *their* holiday list (the server resolves one per report --
 * HRMS assigns holiday lists per employee, so a team split across two lists
 * is dimmed per person rather than from the manager's calendar). */
function isOff(day, report) {
  return day.is_weekend || report.holidays.includes(day.date)
}

/** The day tracks have to be visible on a white card, or the bars float in
 * space -- so a working day is the page ground and a non-working one is a
 * step darker, which is the same two-step relationship the week grid on
 * Timesheet uses for its weekend columns. */
function cellTone(day, report) {
  return isOff(day, report) ? 'bg-surface-gray-3' : 'bg-surface-gray-1'
}

/** The reason a day is dimmed, in a word. The phone list has no per-person
 * column to dim, so it reads the week's own shading -- the manager's holiday
 * list, which is what the desktop column headers use too. */
function offLabel(day) {
  if (day.is_holiday) return 'Holiday'
  return day.is_weekend ? 'Weekend' : ''
}

// The date tile (index.css, `.date-tile`). `|| {}` so a value that is not a
// calendar date renders as an empty tile rather than throwing -- the week's
// dates are the server's, so it never happens.
function tile(date) {
  return dateTileParts(date) || {}
}

// --- the phone's day-first shape ----------------------------------------

/** The same week, read down instead of across: seven days, each naming who
 * is out on it. A day nobody is booked off on still gets a row -- "nobody
 * booked off on Wednesday" is the answer to the question this page is
 * asked, and a list that only shows the days with leave in them cannot be
 * told apart from a list that failed to load part of the week. */
const dayRows = computed(() =>
  days.value.map((day) => ({
    ...day,
    people: reports.value.flatMap((report) =>
      report.leaves
        .filter((leave) => leave.start <= day.date && leave.end >= day.date)
        .map((leave) => ({
          key: `${report.employee}-${leave.name}`,
          employee_name: report.employee_name,
          initials: report.initials,
          photo_url: report.photo_url,
          label: barLabel(leave),
          waiting: leave.waiting,
        })),
    ),
  })),
)

// --- the field block ----------------------------------------------------

/** "Priya and Sam" / "Priya, Sam and 2 others" -- names first, because a
 * count alone is never the thing a manager wanted to know. */
const outTodayNames = computed(() => {
  const names = [...new Set(outToday.value.map((row) => row.employee_name))]
  if (!names.length) return ''
  if (names.length === 1) return names[0]
  if (names.length === 2) return `${names[0]} and ${names[1]}`
  const rest = names.length - 2
  return `${names[0]}, ${names[1]} and ${rest} ${rest === 1 ? 'other' : 'others'}`
})

/** One person's line in the field block: the type, whether it is half a
 * day, and whether the decision is still this manager's to make. */
function outTodayLine(row) {
  // The names are already the headline when there is only one of them.
  const parts = outToday.value.length > 1 ? [row.employee_name, row.leave_type] : [row.leave_type]
  if (row.half_day) parts.push('half day')
  if (row.waiting) parts.push('waiting on you')
  return parts.join(' · ')
}

// Empty is "there is nobody on the grid", never "this week is quiet". A week
// with no leave in it is a real answer and still has to render the grid, the
// week's shading, and -- the part that made this a defect -- the anchored
// block, whose "out today" and "N still waiting" are not week-scoped at all:
// collapsing the region on a quiet week hid a manager's own decision queue.
const noReports = computed(() => !!team.data && reports.value.length === 0)
const emptyTitle = 'Nobody reports to you right now'
const emptyBody =
  'The leave of the people who report to you shows up here. Ask HR if that looks wrong.'

// The quiet week, in one plain sentence, so an empty grid is never mistaken
// for a week that failed to load.
const quietWeek = computed(() => !noReports.value && !hasLeave.value)
</script>

<template>
  <div>
    <PageHeader title="Team" />

    <!-- Plan 2026-10-04-003 R1. Two tabs, in the URL, arrow-key switchable --
         the same segmented control the Requests page uses. -->
    <div
      class="mb-4 flex gap-2 border-b border-outline-gray-2"
      role="tablist"
      aria-label="Team views"
      data-testid="team-tabs"
    >
      <button
        v-for="item in TABS"
        :key="item.name"
        type="button"
        role="tab"
        class="-mb-px min-h-11 border-b-2 px-3 text-sm font-medium"
        :class="
          tab === item.name
            ? 'border-ink-gray-9 text-ink-gray-9'
            : 'border-transparent text-ink-gray-6 hover:text-ink-gray-9'
        "
        :aria-selected="tab === item.name"
        :tabindex="tab === item.name ? 0 : -1"
        @click="showTab(item.name)"
        @keydown.right.prevent="cycleTab(1)"
        @keydown.left.prevent="cycleTab(-1)"
      >
        {{ item.label }}
      </button>
    </div>

    <!-- Week navigation. Both arrows and a way back, because a manager who
         has paged three weeks out should not have to page back. It sits
         outside the async region on purpose: the week is this page's own
         state, so the arrows keep working while the week behind them is
         loading, failing, or empty. -->
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

    <AsyncState
      v-if="tab === 'leaves'"
      section="team"
      :resource="team"
      :empty="noReports"
      :empty-title="emptyTitle"
      :empty-body="emptyBody"
      skeleton="field"
      skeleton-height="h-40"
    >
      <!-- The one anchored region: who is out today, and whether anything
           is waiting on this manager. Signal yellow is legal only in
           here. -->
      <section
        class="surface-field elev-2 mb-4 p-4"
        aria-label="Out today"
      >
        <h2 class="label !text-blue-200 mb-2">
          Out today
        </h2>
        <template v-if="!isCurrentWeek">
          <p class="text-sm text-blue-100">
            You're looking at another week. Come back to this week to see who is out today.
          </p>
          <p class="mt-3">
            <button
              type="button"
              class="min-h-11 cursor-pointer text-sm font-bold text-signal hover:underline"
              @click="thisWeek"
            >
              Go to this week
            </button>
          </p>
        </template>
        <template v-else-if="outToday.length">
          <p class="font-heading text-lg font-bold text-white">
            {{ outTodayNames }}
          </p>
          <ul class="mt-2 space-y-1">
            <li
              v-for="row in outToday"
              :key="`${row.employee}-${row.leave_type}`"
              class="text-sm text-blue-100"
            >
              {{ outTodayLine(row) }}
            </li>
          </ul>
        </template>
        <p
          v-else
          class="text-sm text-blue-100"
        >
          Everyone on your team is in today.
        </p>

        <p
          v-if="waitingCount"
          class="mt-3"
        >
          <router-link
            :to="{ name: 'Approvals' }"
            class="tabular -my-2 inline-flex min-h-11 items-center gap-2 rounded-full bg-signal px-3 text-sm font-bold text-field"
          >
            <Icon
              name="chevronRight"
              class="h-4 w-4"
            />
            {{ waitingCount }} {{ waitingCount === 1 ? 'request' : 'requests' }} still waiting —
            decide
          </router-link>
        </p>
      </section>

      <p
        v-if="quietWeek"
        class="mb-3 text-sm text-ink-gray-6"
      >
        Nobody on your team is booked off this week. Use the arrows to look at another week.
      </p>

      <!-- Desktop: the week across, one row per report. Wide content
           scrolls inside its own container, never the page. -->
      <div class="hidden lg:block">
        <div
          class="surface-card elev-1 overflow-x-auto p-4"
          aria-label="Team week"
        >
          <div class="min-w-[44rem]">
            <div class="grid grid-cols-[11rem_1fr] gap-3">
              <span />
              <div
                class="grid grid-cols-7 gap-1"
                aria-hidden="true"
              >
                <div
                  v-for="(day, index) in days"
                  :key="day.date"
                  class="text-center"
                >
                  <p class="label !text-ink-gray-5">
                    {{ WEEKDAYS[index] }}
                  </p>
                  <p
                    class="tabular text-sm font-medium"
                    :class="day.is_weekend || day.is_holiday ? 'text-ink-gray-5' : 'text-ink-gray-9'"
                  >
                    {{ tile(day.date).day }}
                  </p>
                </div>
              </div>
            </div>

            <div
              v-for="report in reports"
              :key="report.employee"
              class="mt-3 grid grid-cols-[11rem_1fr] items-start gap-3 border-t border-outline-gray-2 pt-3"
              data-testid="team-row"
              :data-employee="report.employee_name"
            >
              <div class="flex min-w-0 items-center gap-2">
                <Avatar
                  class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  :photo-url="report.photo_url"
                  :initials="report.initials"
                  :size="32"
                />
                <span class="min-w-0 truncate text-sm font-medium text-ink-gray-9">
                  {{ report.employee_name }}
                </span>
              </div>

              <div
                class="grid grid-cols-7 gap-1"
                :style="trackStyle(report)"
              >
                <!-- The seven day tracks, dimmed where this person is not
                     expected at work. Behind the bars, not beside them. -->
                <span
                  v-for="(day, index) in days"
                  :key="day.date"
                  class="rounded"
                  :class="cellTone(day, report)"
                  :style="{ gridColumn: String(index + 1), gridRow: '1 / -1' }"
                  aria-hidden="true"
                />
                <span
                  v-for="(leave, position) in report.leaves"
                  :key="leave.name"
                  class="flex items-center overflow-hidden truncate rounded px-2 text-xs font-medium"
                  :class="barTone(leave)"
                  :style="barStyle(leave, position)"
                >{{ barLabel(leave) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Phone: the same week read downwards, a date tile per day. -->
      <ul class="space-y-2 lg:hidden">
        <li
          v-for="day in dayRows"
          :key="day.date"
          class="surface-card elev-1 flex gap-3 p-3"
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
                v-if="offLabel(day)"
                class="ml-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
              >{{ offLabel(day) }}</span>
            </p>
            <ul
              v-if="day.people.length"
              class="mt-1.5 space-y-1.5"
            >
              <li
                v-for="person in day.people"
                :key="person.key"
                class="flex items-center gap-2"
              >
                <Avatar
                  class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  :photo-url="person.photo_url"
                  :initials="person.initials"
                  :size="28"
                />
                <span class="min-w-0 flex-1 truncate text-sm text-ink-gray-7">
                  {{ person.employee_name }}
                </span>
                <span
                  class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
                  :class="barTone(person)"
                >{{ person.label }}</span>
              </li>
            </ul>
            <p
              v-else
              class="mt-0.5 text-sm text-ink-gray-5"
            >
              Nobody booked off.
            </p>
          </div>
        </li>
      </ul>

      <!-- Legend, then the scope. Both are the same kind of statement: what
           this screen is showing, and what it is deliberately not. -->
      <div
        class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-gray-6"
        aria-label="Legend"
      >
        <span class="inline-flex items-center gap-1.5">
          <span
            class="h-3 w-6 rounded bg-surface-green-2"
            aria-hidden="true"
          />
          Approved
        </span>
        <span class="inline-flex items-center gap-1.5">
          <span
            class="h-3 w-6 rounded bg-surface-amber-1"
            aria-hidden="true"
          />
          Waiting for a decision
        </span>
        <span class="inline-flex items-center gap-1.5">
          <span
            class="h-3 w-6 rounded bg-surface-gray-2"
            aria-hidden="true"
          />
          Weekend or holiday
        </span>
      </div>

      <p class="mt-3 text-sm text-ink-gray-5">
        Only the people who report to you, and only this week.<template v-if="notShown">
          {{ notShown }} more {{ notShown === 1 ? 'person is' : 'people are' }} not shown.
        </template>
        Reasons for leave aren't shown here.
      </p>
    </AsyncState>

    <!-- Plan 2026-10-04-003 U4: the Timesheets tab. Every current direct
         report's week, in any state -- the state an approved week keeps
         *after* its share is gone (R2, R4). -->
    <AsyncState
      v-else
      section="team-timesheets"
      :resource="teamTimesheets"
      :empty="noReports"
      :empty-title="emptyTitle"
      empty-body="The weeks of the people who report to you show up here."
      skeleton="row"
      :skeleton-rows="4"
    >
      <ul class="space-y-2">
        <li
          v-for="row in timesheetReports"
          :key="row.employee"
        >
          <component
            :is="row.timesheet ? 'button' : 'div'"
            class="surface-card elev-1 block w-full p-3 text-left"
            :class="row.timesheet ? 'cursor-pointer hover:elev-2' : ''"
            :type="row.timesheet ? 'button' : undefined"
            :data-testid="'team-timesheet-row'"
            :data-employee="row.employee_name"
            @click="openMember(row)"
          >
            <span class="flex items-center gap-3">
              <Avatar
                class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                :photo-url="row.photo_url"
                :initials="row.initials"
                :size="32"
              />
              <span class="min-w-0 flex-1 truncate font-medium text-ink-gray-9">
                {{ row.employee_name }}
              </span>
              <StatusBadge
                v-if="row.state"
                kind="timesheet"
                :status="row.state"
              />
              <span
                v-else
                class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
              >
                Not started
              </span>
              <span
                v-if="row.open_change"
                class="shrink-0 rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
              >
                Change requested
              </span>
            </span>

            <!-- The per-day strip, then the totals: a week that is short or
                 gappy reads at arm's length, which is the whole point of the
                 tab (R2). -->
            <span class="mt-2 flex items-center gap-1">
              <span
                v-for="date in weekDates"
                :key="date"
                class="tabular flex h-7 min-w-0 flex-1 items-center justify-center rounded bg-surface-gray-1 text-xs text-ink-gray-7"
                :class="dayHours(row, date) ? 'bg-surface-gray-2 font-medium text-ink-gray-9' : ''"
              >
                {{ dayHours(row, date) ?? '·' }}
              </span>
            </span>

            <span class="mt-2 flex items-center justify-between gap-3 text-sm">
              <span class="min-w-0 truncate text-ink-gray-6">
                {{ projectLabel(row) || 'No projects booked' }}
              </span>
              <span class="tabular shrink-0 font-medium text-ink-gray-9">
                {{ hoursLabel(row) }}
              </span>
            </span>
          </component>
        </li>
      </ul>

      <p
        v-if="timesheetNotShown"
        class="mt-3 text-sm text-ink-gray-5"
      >
        {{ timesheetNotShown }} more {{ timesheetNotShown === 1 ? 'person is' : 'people are' }} not
        shown.
      </p>
    </AsyncState>

    <!-- R3: one report's week, read-only. Tasks by day, hours, the decision
         trail and any open change request -- and no rate, cost or billing
         number anywhere, which the server's allow-list already guarantees. -->
    <Dialog
      :model-value="memberOpen"
      :options="{
        title: memberWeek.data?.employee ? memberNameLabel : 'This week',
        size: '2xl',
      }"
      @update:model-value="(value) => !value && closeMember()"
    >
      <template #body-content>
        <AsyncState
          section="team-member-week"
          :resource="memberWeek"
          :empty="false"
          skeleton="field"
          skeleton-height="h-40"
        >
          <p class="text-sm text-ink-gray-6">
            <StatusBadge
              v-if="memberWeek.data?.timesheet?.state"
              kind="timesheet"
              :status="memberWeek.data.timesheet.state"
            />
            <span class="tabular ml-2 font-medium text-ink-gray-9">
              {{ hoursLabel(memberHoursOf(memberWeek.data?.timesheet)) }}
            </span>
          </p>

          <div class="mt-3 overflow-x-auto">
            <table
              class="w-full min-w-[36rem] text-sm"
              aria-label="Hours by day"
            >
              <thead>
                <tr class="label !text-ink-gray-5">
                  <th class="py-1 pr-2 text-left font-medium">
                    Work
                  </th>
                  <th
                    v-for="date in memberDates"
                    :key="date"
                    class="px-1 py-1 text-center font-medium"
                  >
                    {{ tile(date).day }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="line in memberLines"
                  :key="`${line.project}|${line.task || ''}`"
                  class="border-t border-outline-gray-2"
                >
                  <td class="py-1.5 pr-2">
                    <span class="block truncate font-medium text-ink-gray-9">
                      {{ line.project }}
                    </span>
                    <span
                      v-if="line.task"
                      class="block truncate text-xs text-ink-gray-6"
                    >
                      {{ line.task }}
                    </span>
                  </td>
                  <td
                    v-for="date in memberDates"
                    :key="date"
                    class="tabular px-1 py-1.5 text-center text-ink-gray-7"
                  >
                    {{ line.cells[date] || '·' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div
            v-if="memberWeek.data?.timesheet?.decision_reason"
            class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
          >
            <span class="font-medium text-ink-gray-9">Manager's note:</span>
            {{ memberWeek.data.timesheet.decision_reason }}
          </div>

          <div
            v-if="memberWeek.data?.timesheet?.open_change"
            class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
          >
            <span class="font-medium text-ink-gray-9">Change requested:</span>
            &ldquo;{{ memberWeek.data.timesheet.open_change.comment }}&rdquo;
          </div>

          <!-- The decision trail, oldest first: every workflow move the week
               has made, named by full name (R3). -->
          <ul
            v-if="memberWeek.data?.timesheet?.trail?.length"
            class="mt-3 space-y-1.5 border-t border-outline-gray-2 pt-3 text-sm text-ink-gray-6"
            aria-label="Decision trail"
          >
            <li
              v-for="entry in memberWeek.data.timesheet.trail"
              :key="entry.on + entry.kind"
            >
              <span class="tabular text-xs text-ink-gray-5">{{ entry.on.split(' ')[0] }}</span>
              <span class="ml-2">{{ entry.text }}</span>
              <span
                v-if="entry.by"
                class="ml-1 text-ink-gray-5"
              >&mdash; {{ entry.by }}</span>
            </li>
          </ul>
        </AsyncState>
      </template>
      <template #actions>
        <Button
          variant="subtle"
          @click="closeMember"
        >
          Close
        </Button>
      </template>
    </Dialog>
  </div>
</template>
