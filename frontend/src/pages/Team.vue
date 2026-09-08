<script setup>
import { ref, computed, watch } from 'vue'
import { createResource } from 'frappe-ui'
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

// The week is component state rather than a route parameter: unlike a
// timesheet, a team week is nobody's document -- there is no row anywhere in
// the portal that links to one, so a URL for it would only ever be produced
// by this page's own arrows.
const weekStart = ref(mondayOf(today()))
const weekEnd = computed(() => addCalendarDays(weekStart.value, 6))

const team = createResource({
  url: 'helixhr.api.get_my_team_week',
  makeParams: () => ({ week_start: weekStart.value }),
  auto: true,
})
watch(weekStart, () => team.fetch())

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

function prevWeek() {
  weekStart.value = addCalendarDays(weekStart.value, -7)
}
function nextWeek() {
  weekStart.value = addCalendarDays(weekStart.value, 7)
}
function thisWeek() {
  weekStart.value = mondayOf(today())
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

const emptyTitle = 'Nobody on your team is booked off this week'
const emptyBody = computed(
  () =>
    `Approved and waiting leave for the ${totalReports.value === 1 ? 'person' : `${totalReports.value} people`} reporting to you shows up here. Use the arrows to look at another week.`,
)
</script>

<template>
  <div>
    <PageHeader title="Team" />

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
      section="team"
      :resource="team"
      :empty="!hasLeave"
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
                <span
                  class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  aria-hidden="true"
                >{{ report.initials }}</span>
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
                <span
                  class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                  aria-hidden="true"
                >{{ person.initials }}</span>
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
  </div>
</template>
