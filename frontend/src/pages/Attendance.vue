<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Dialog } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import CheckInSheet from '@/components/CheckInSheet.vue'
import AttendanceRequestSheet from '@/components/AttendanceRequestSheet.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StepStrip from '@/components/StepStrip.vue'
import { formatDate, formatDateRange, formatTime, today } from '@/lib/dates'
import { ATTENDANCE_LABEL } from '@/lib/week'

// P3-U6 step 7 / P3-R17. `/attendance/requests/:name` resolves here rather
// than to a page of its own: a request is four fields and two steps, the
// sheet already renders exactly that, and a second surface would be a second
// place for the copy and the actions to drift. The route opens the sheet over
// the calendar, which is also the context the request is about.
const props = defineProps({
  name: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

const STATUS_COLOR = {
  Present: 'bg-surface-green-3',
  Absent: 'bg-surface-red-3',
  'Half Day': 'bg-surface-amber-3',
  'On Leave': 'bg-surface-blue-3',
  // P3-R19. A day an approved attendance request marked. The palette adds no
  // new colour (P3-R24), so this is the light green of the Approved pair
  // against Present's solid one -- and the word is in the day's accessible
  // name, in the legend and in the sheet, so the dot is never the only
  // reading of it.
  'Work From Home': 'bg-surface-green-2',
  Holiday: 'bg-surface-gray-4',
}

// P2-R5 / P2-AE3. "This month" is the *user's* month, from the same server
// today the rest of the portal uses. `new Date()` here read the host clock,
// so a laptop set to another zone could open the wrong month on the last day
// of one.
const [startYear, startMonth] = today()
  .split('-')
  .map(Number)
const year = ref(startYear)
const month = ref(startMonth - 1) // 0-indexed

/** A UTC-pinned Date for month arithmetic only. It is never rendered as an
 * instant and never crosses a timezone: the y/m/d that goes in comes out. */
function utc(y, m, d) {
  return new Date(Date.UTC(y, m, d))
}

const monthLabel = computed(() =>
  utc(year.value, month.value, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }),
)

function pad(n) {
  return String(n).padStart(2, '0')
}
function isoDate(y, m, d) {
  return `${y}-${pad(m + 1)}-${pad(d)}`
}

const monthStart = computed(() => isoDate(year.value, month.value, 1))
const monthEnd = computed(() =>
  isoDate(year.value, month.value, utc(year.value, month.value + 1, 0).getUTCDate()),
)

// helixhr.api.get_my_attendance rather than hrms.api directly: the exceptions
// R16 asks for (late, missing) need the Attendance late_entry flag and the
// employee's holiday list, and "missing" has a rule that belongs on the
// server. P2-U5 added the date-span bound and made it resolve holidays once
// per request rather than once per question asked of them.
const calendar = createResource({
  url: 'helixhr.api.get_my_attendance',
  makeParams: () => ({ from_date: monthStart.value, to_date: monthEnd.value }),
  auto: true,
})

const attendanceDays = computed(() => calendar.data?.days || {})
const missingDays = computed(() => new Set(calendar.data?.missing || []))
const exceptions = computed(() => calendar.data?.exceptions || {})
// No check-in device is configured yet. Until one is, the server reports
// nothing missing and this page says so rather than showing an empty grid
// that looks broken.
const isTracked = computed(() => !!calendar.data?.tracked)
const EXCEPTION_LABELS = {
  absent: 'Absent',
  half_day: 'Half day',
  late: 'Late arrival',
  missing: 'No record',
}
const exceptionEntries = computed(() =>
  Object.entries(EXCEPTION_LABELS)
    .map(([key, label]) => [label, exceptions.value[key] || 0])
    .filter(([, count]) => count > 0),
)

// --- the Today strip (P3-U4 step 1 / P3-R5, P3-R8) -----------------------
//
// One strip, inside the field block, and the server decides everything on
// it: whether a punch is possible at all, which punch is next, and the one
// sentence to show when it is not (P3-KTD5). The page never derives IN/OUT
// itself -- the same derivation the punch uses is the one rendered here, so
// the button and the server cannot disagree.
const checkin = computed(() => calendar.data?.checkin || { enabled: false, reason: null })
const nextLogType = computed(() => checkin.value.next_log_type || 'IN')
const punchLabel = computed(() => (nextLogType.value === 'OUT' ? 'Check out' : 'Check in'))
const sheetOpen = ref(false)

const lastPunchLine = computed(() => {
  const last = checkin.value.last
  if (!last) return 'No punches yet in this shift.'
  const kind = last.log_type === 'OUT' ? 'Checked out' : 'Checked in'
  const place = last.has_location ? 'location captured' : 'no location captured'
  return `${kind} at ${formatTime(last.time)} · ${place}`
})

/** The fallback when a punch cannot happen: the Fix a day sheet, with today
 * already in it (P3-U6 step 1). A route rather than an event, because
 * CheckInSheet offers it as a link and because `?fix=` makes "fix this day"
 * something Home, a notification or another person can send you to. */
const todayFixRoute = computed(() => ({ name: 'Attendance', query: { fix: today() } }))

/** Two different reasons for an empty day sheet, and only one of them is
 * about this employee's day. */
const dayEmptyBody = computed(() =>
  checkin.value.enabled
    ? 'Nothing was recorded on this day.'
    : "Check-in isn't set up yet, so there's nothing recorded here.",
)

function onPunched() {
  // The month grid, the exceptions and the strip are one response, so one
  // reload keeps the button and the day sheet from disagreeing.
  calendar.reload()
  if (selectedDay.value?.iso === today()) checkins.fetch()
}

watch([year, month], () => calendar.fetch())

const daysInMonth = computed(() => utc(year.value, month.value + 1, 0).getUTCDate())
// Monday = 0. The week spine on Home, `helixhr.utils.get_week_bounds` and
// `lib/dates.js` all run Monday..Sunday (KTD10); this grid was the one
// Sunday-first surface in the portal, so the same date sat in a different
// column depending on which screen you were looking at.
const firstWeekday = computed(() => (utc(year.value, month.value, 1).getUTCDay() + 6) % 7)

const days = computed(() => {
  const list = []
  for (let i = 0; i < firstWeekday.value; i++) list.push(null)
  for (let d = 1; d <= daysInMonth.value; d++) {
    const iso = isoDate(year.value, month.value, d)
    const record = attendanceDays.value[iso]
    list.push({
      day: d,
      iso,
      status: record?.status,
      late: !!record?.late,
      missing: missingDays.value.has(iso),
    })
  }
  return list
})

const summary = computed(() => calendar.data?.summary || {})

function prevMonth() {
  if (month.value === 0) {
    month.value = 11
    year.value -= 1
  } else {
    month.value -= 1
  }
}
function nextMonth() {
  if (month.value === 11) {
    month.value = 0
    year.value += 1
  } else {
    month.value += 1
  }
}
function goToday() {
  const [y, m] = today().split('-').map(Number)
  year.value = y
  month.value = m - 1
}

// --- the day sheet ------------------------------------------------------

const selectedDay = ref(null)
const checkins = createResource({
  // P2-R27. This was `frappe.client.get_list` on Employee Checkin with no
  // employee filter and `limit_page_length: 0` -- correct only because
  // Frappe's permission layer narrowed it, and unbounded either way.
  url: 'helixhr.api.get_my_checkins',
  makeParams: () => ({ date: selectedDay.value?.iso }),
  auto: false,
})

function statusLabel(day) {
  if (!day) return ''
  if (day.status) return ATTENDANCE_LABEL[day.status] || day.status
  return day.missing ? 'No record' : 'Nothing recorded'
}

/** P3-R9. A day with punches and no Attendance row is not "No record" --
 * the employee did check in, and HRMS's nightly job has not turned those
 * punches into attendance yet. Saying "No record" there reads as "we lost
 * it" and sends somebody to HR about a day that is fine. Only the sheet can
 * say it: the grid does not load punches. */
const daySheetLabel = computed(() => {
  const day = selectedDay.value
  if (!day) return ''
  if (day.status) return statusLabel(day)
  if (checkins.data?.length) return 'Checked in, attendance not marked yet'
  return statusLabel(day)
})

function dayLabel(day) {
  const parts = [`${monthLabel.value} ${day.day}`]
  if (day.status) parts.push(ATTENDANCE_LABEL[day.status] || day.status)
  if (day.late) parts.push('late arrival')
  if (day.missing) parts.push('no record')
  return parts.join(', ')
}

function openDay(day) {
  if (!day) return
  selectedDay.value = day
  checkins.fetch()
}

/** "Report a problem with this day" (P2-R15). Attendance correction is HR's
 * job in Frappe HR, so the portal does not edit the record -- it opens one HR
 * Request with the date and the status already written into it, which is what
 * makes the reply actionable without a round of "which day?". */
const reportRoute = computed(() => ({
  name: 'Requests',
  query: {
    category: 'Other',
    subject: selectedDay.value
      ? `Attendance problem on ${formatDate(selectedDay.value.iso)} (${statusLabel(selectedDay.value)})`
      : '',
  },
}))

// The day panel is a frappe-ui `Dialog` rather than the hand-rolled
// `fixed inset-x-0 bottom-0` div it used to be. That div sat *under* the
// phone tab bar, could not be closed with Escape, did not trap focus and
// left focus wherever it had been when it closed -- four P2-R4 failures for
// one panel. reka-ui, underneath `Dialog`, answers all four; index.css gives
// it the phone sheet shape and the desktop bounded-dialog shape (P2-R6).
const dayOpen = computed({
  get: () => !!selectedDay.value,
  set: (open) => {
    if (!open) selectedDay.value = null
  },
})

// --- attendance requests (P3-U6 steps 1 and 2 / P3-R12, P3-R17) ----------
//
// One bounded read for the whole column, and it also carries the two reasons
// HRMS offers and the manager's name, so opening the sheet costs nothing
// extra (P3-R25).
const requests = createResource({
  url: 'helixhr.api.get_my_attendance_requests',
  auto: true,
})

const requestRows = computed(() => requests.data?.requests || [])
const requestOverflow = computed(() =>
  Math.max(0, (requests.data?.total || 0) - requestRows.value.length),
)
const approverName = computed(() => requests.data?.approver_name || '')
const requestReasons = computed(() => requests.data?.reasons || [])

const requestSheetOpen = ref(false)
const requestName = ref('')
const requestDate = ref('')

/** Raise one, for a named day. The day sheet, the header action and the
 * check-in sheet's fallback all land here. */
function fixADay(iso) {
  selectedDay.value = null
  requestName.value = ''
  requestDate.value = iso || today()
  requestSheetOpen.value = true
}

function openRequest(name) {
  requestDate.value = ''
  requestName.value = name
  requestSheetOpen.value = true
}

// Two ways in from the URL: `/attendance/requests/:name` opens that request,
// and `?fix=<date>` opens the ask for that day. Both are replaced on close,
// so Back does not reopen the sheet the employee just dismissed.
watch(
  () => props.name,
  (name) => {
    if (name) openRequest(name)
  },
  { immediate: true },
)
watch(
  () => route.query.fix,
  (fix) => {
    if (fix) {
      sheetOpen.value = false
      fixADay(String(fix))
    }
  },
  { immediate: true },
)
watch(requestSheetOpen, (open) => {
  if (open) return
  requestName.value = ''
  requestDate.value = ''
  if (props.name || route.query.fix) router.replace({ name: 'Attendance' })
})

/** A request that was sent, withdrawn or decided changes both the list and
 * what the calendar shows for those days. */
function onRequestChanged() {
  requests.reload()
  calendar.reload()
}

const LEGEND = [
  { label: 'Present', class: STATUS_COLOR.Present },
  { label: 'Work from home', class: STATUS_COLOR['Work From Home'] },
  { label: 'Half day', class: STATUS_COLOR['Half Day'] },
  { label: 'Absent', class: STATUS_COLOR.Absent },
  { label: 'On leave', class: STATUS_COLOR['On Leave'] },
]
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Attendance">
      <template #actions>
        <!-- P3-U6 step 1. The page-level way in, for a day the employee has
             to scroll to find. The day sheet offers the same thing with the
             day already named. -->
        <button
          class="min-h-11 cursor-pointer rounded-full border border-outline-gray-2 bg-surface-white px-4 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
          @click="fixADay(today())"
        >
          Fix a day
        </button>
      </template>
    </PageHeader>

    <div class="flex max-w-xl items-center justify-between">
      <button
        class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-gray-6 hover:bg-surface-gray-2"
        aria-label="Previous month"
        @click="prevMonth"
      >
        <Icon name="chevronLeft" />
      </button>
      <div class="flex items-center gap-2">
        <span class="font-medium text-ink-gray-9">{{ monthLabel }}</span>
        <button
          class="min-h-11 cursor-pointer rounded-full bg-surface-gray-2 px-3 text-xs text-ink-gray-7 hover:bg-surface-gray-3"
          @click="goToday"
        >
          This month
        </button>
      </div>
      <button
        class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-gray-6 hover:bg-surface-gray-2"
        aria-label="Next month"
        @click="nextMonth"
      >
        <Icon name="chevronRight" />
      </button>
    </div>

    <div class="lg:flex lg:items-start lg:gap-6">
      <AsyncState
        class="max-w-xl lg:flex-1"
        section="attendance-month"
        :resource="calendar"
        :empty="false"
        skeleton="field"
        skeleton-height="h-[26rem]"
      >
        <!-- The anchored region: this month, counted, on the field. The dot is
           a second reading of a word that is always present, so nothing here
           is carried by colour alone. -->
        <section
          class="surface-field elev-2 mb-4 p-4"
          aria-label="This month"
        >
          <div
            v-if="Object.keys(summary).length"
            class="flex flex-wrap gap-x-5 gap-y-2"
          >
            <p
              v-for="(count, status) in summary"
              :key="status"
              class="flex items-center gap-2 text-sm text-blue-100"
            >
              <span
                class="h-2 w-2 shrink-0 rounded-full"
                :class="STATUS_COLOR[status] || 'bg-surface-gray-4'"
                aria-hidden="true"
              />
              <span class="tabular font-heading text-base font-bold text-white">{{ count }}</span>
              {{ ATTENDANCE_LABEL[status] || status }}
            </p>
          </div>
          <p
            v-else
            class="text-sm text-blue-100"
          >
            No attendance recorded yet this month.
          </p>

          <!-- P3-R5. The Today strip: the next punch, the last one, and
             whether it carried a location. Inside the field block because
             it is this page's one anchored action, which is also the only
             place signal yellow is legal. -->
          <div class="mt-4 border-t border-white/15 pt-3">
            <h2 class="label !text-blue-200 mb-2">
              Today
            </h2>
            <template v-if="checkin.enabled">
              <button
                class="min-h-11 cursor-pointer rounded-full bg-signal px-5 text-sm font-bold text-field hover:brightness-95"
                @click="sheetOpen = true"
              >
                {{ punchLabel }}
              </button>
              <p class="mt-2 flex items-center gap-1.5 text-sm text-blue-100">
                <Icon
                  v-if="checkin.last?.has_location"
                  name="pin"
                  size="h-3.5 w-3.5"
                  class="shrink-0 text-blue-200"
                />
                {{ lastPunchLine }}
              </p>
            </template>
            <p
              v-else
              class="text-sm text-blue-200"
            >
              {{ checkin.reason }}
            </p>
          </div>

          <!-- R16's exceptions. Dormant by design: with no check-in device the
             server reports nothing missing, so this resolves to the one
             explanatory line rather than a wall of red. It starts working the
             day real records arrive, with no change here. -->
          <div class="mt-4 border-t border-white/15 pt-3">
            <h2 class="label !text-blue-200 mb-2">
              Exceptions
            </h2>
            <div
              v-if="exceptionEntries.length"
              class="flex flex-wrap gap-2 text-sm"
            >
              <span
                v-for="[label, count] in exceptionEntries"
                :key="label"
                class="rounded-full bg-signal px-3 py-1 font-medium text-field"
              >
                {{ label }}: <span class="tabular">{{ count }}</span>
              </span>
            </div>
            <p
              v-else-if="!isTracked"
              class="text-sm text-blue-200"
            >
              Check-in isn't set up yet, so there's nothing to flag. Once it is,
              late arrivals and days with no record will show up here.
            </p>
            <p
              v-else
              class="text-sm text-blue-200"
            >
              Nothing to flag this month.
            </p>
          </div>
        </section>

        <div class="surface-card elev-1 p-3">
          <div class="grid grid-cols-7 gap-1 text-center text-xs text-ink-gray-5">
            <span
              v-for="d in ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']"
              :key="d"
            >{{ d }}</span>
          </div>
          <div class="mt-1 grid grid-cols-7 gap-1">
            <!-- The blanks before the 1st are spacing, not controls. They used to
             render as disabled <button>s with no text, which put four unnamed
             buttons per month into the accessibility tree. -->
            <component
              :is="day ? 'button' : 'span'"
              v-for="(day, index) in days"
              :key="index"
              class="tabular flex aspect-square min-h-11 flex-col items-center justify-center rounded-md text-sm"
              :class="[
                day ? 'cursor-pointer text-ink-gray-8 hover:bg-surface-gray-2' : '',
                day?.missing ? 'border border-dashed border-outline-gray-3' : '',
              ]"
              :data-day="day?.iso"
              :aria-label="day ? dayLabel(day) : undefined"
              @click="day && openDay(day)"
            >
              <span>{{ day?.day }}</span>
              <span
                v-if="day?.status"
                class="mt-0.5 h-2 w-2 rounded-full"
                :class="[
                  STATUS_COLOR[day.status] || 'bg-surface-gray-4',
                  day.late ? 'ring-2 ring-amber-500 ring-offset-1' : '',
                ]"
              />
            </component>
          </div>
        </div>

        <!-- The legend. The grid's dots are a second reading of a word that
           lives in the day's accessible name and in the sheet; without this
           they are a second reading of nothing (P2-R5). -->
        <ul class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-gray-6">
          <li
            v-for="entry in LEGEND"
            :key="entry.label"
            class="flex items-center gap-1.5"
          >
            <span
              class="h-2 w-2 shrink-0 rounded-full"
              :class="entry.class"
              aria-hidden="true"
            />
            {{ entry.label }}
          </li>
          <li class="flex items-center gap-1.5">
            <span
              class="h-2 w-2 shrink-0 rounded-full ring-2 ring-amber-500"
              aria-hidden="true"
            />
            Late
          </li>
          <li class="flex items-center gap-1.5">
            <span
              class="h-3 w-3 shrink-0 rounded-sm border border-dashed border-outline-gray-3"
              aria-hidden="true"
            />
            No record
          </li>
        </ul>
      </AsyncState>

      <!-- P3-U6 step 1 / P3-R17. The employee's own requests, each with its
           two steps and, when it came back, the reason inline -- so following
           one never means opening it. -->
      <section
        class="min-w-0 lg:w-80 lg:shrink-0"
        aria-labelledby="attendance-requests-heading"
        data-testid="attendance-requests"
      >
        <h2
          id="attendance-requests-heading"
          class="label mb-2"
        >
          Your requests
        </h2>
        <AsyncState
          section="attendance-requests"
          :resource="requests"
          :empty="requestRows.length === 0"
          empty-title="No requests yet"
          empty-body="Ask to have a work-from-home or on-duty day counted, and it shows up here."
          :skeleton-rows="2"
        >
          <ul class="space-y-2">
            <li
              v-for="row in requestRows"
              :key="row.name"
              class="surface-card elev-1"
              :data-request-name="row.name"
            >
              <button
                type="button"
                class="w-full cursor-pointer p-3 text-left"
                @click="openRequest(row.name)"
              >
                <span class="flex items-baseline justify-between gap-2">
                  <span class="min-w-0 truncate text-sm font-medium text-ink-gray-9">
                    {{ row.reason }}
                  </span>
                  <StatusBadge
                    kind="attendance"
                    :status="row.workflow_state"
                    :docstatus="row.docstatus"
                    :approver="approverName"
                  />
                </span>
                <span class="tabular mt-0.5 block text-xs text-ink-gray-6">
                  {{ formatDateRange(row.from_date, row.to_date) }}
                </span>
                <StepStrip
                  class="mt-2"
                  :state="row.workflow_state"
                  :docstatus="row.docstatus"
                  :approver="approverName"
                />
                <span
                  v-if="row.reason_sent_back"
                  class="mt-2 block text-sm text-ink-red-4"
                >“{{ row.reason_sent_back }}”</span>
              </button>
            </li>
          </ul>
          <p
            v-if="requestOverflow"
            class="mt-2 text-sm text-ink-gray-5"
          >
            <span class="tabular">{{ requestOverflow }}</span> older
            {{ requestOverflow === 1 ? 'request' : 'requests' }} not shown here.
          </p>
        </AsyncState>
      </section>
    </div>

    <Dialog
      v-model="dayOpen"
      :options="{ title: selectedDay ? formatDate(selectedDay.iso) : '', size: 'sm' }"
    >
      <template #body-content>
        <p
          v-if="selectedDay"
          class="-mt-2 mb-3 text-sm text-ink-gray-6"
        >
          {{ daySheetLabel }}
          <span v-if="selectedDay.late"> · late arrival</span>
        </p>

        <AsyncState
          section="attendance-day"
          :resource="checkins"
          :empty="!checkins.data?.length"
          empty-title="No check-ins for this day"
          :empty-body="dayEmptyBody"
          skeleton="row"
          :skeleton-rows="2"
        >
          <ul class="divide-y divide-outline-gray-1">
            <li
              v-for="row in checkins.data"
              :key="row.name"
              class="flex items-center justify-between gap-2 py-2 text-sm text-ink-gray-7"
            >
              <span>{{ row.log_type === 'IN' ? 'Check-in' : 'Check-out' }}</span>
              <span class="flex items-center gap-2">
                <!-- P3-R9. A pin, with a name of its own: whether the punch
                     carried a location is the one thing an employee is
                     asked about afterwards. -->
                <template v-if="row.has_location">
                  <Icon
                    name="pin"
                    size="h-3.5 w-3.5"
                    class="shrink-0 text-ink-gray-5"
                  />
                  <span class="sr-only">Location captured</span>
                </template>
                <span class="tabular">{{ formatTime(row.time) }}</span>
                <span
                  v-if="row.log_type === 'IN' && selectedDay?.late"
                  class="rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
                >Late</span>
              </span>
            </li>
          </ul>
        </AsyncState>

        <!-- P3-KTD10. Two different jobs, one sentence each. Work from home
             and on duty are the two reasons HRMS can turn into attendance by
             itself, so those go to the manager as a request; a forgotten
             punch on an office day is HR's call, and that keeps the prefilled
             HR Request it always had (P2-R15). -->
        <div class="mt-4 space-y-3 border-t border-outline-gray-1 pt-3">
          <div>
            <button
              class="flex min-h-11 w-full cursor-pointer items-center justify-center rounded-md bg-field px-4 text-sm font-medium text-white hover:brightness-95"
              @click="fixADay(selectedDay?.iso)"
            >
              Fix a day
            </button>
            <p class="mt-1 text-xs text-ink-gray-5">
              You worked from home or were on duty. Your manager and then HR agree, and the day counts.
            </p>
          </div>
          <div>
            <router-link
              class="flex min-h-11 w-full items-center justify-center rounded-md border border-outline-gray-2 px-4 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
              :to="reportRoute"
            >
              Report a problem
            </router-link>
            <p class="mt-1 text-xs text-ink-gray-5">
              Anything else — a missed punch, the wrong time, a day that looks wrong. HR reads it and replies.
            </p>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- P3-U4 step 4. Mounted, not conditionally rendered: it asks for a
         location when it *opens*, and opening it is always a tap (P3-R6). -->
    <CheckInSheet
      v-model="sheetOpen"
      :log-type="nextLogType"
      :fix-a-day-to="todayFixRoute"
      @punched="onPunched"
    />

    <!-- P3-U6 step 2. One sheet for both moments: raising a request, and
         following the one the route or the column opened. -->
    <AttendanceRequestSheet
      v-model="requestSheetOpen"
      :date="requestDate"
      :name="requestName"
      :reasons="requestReasons"
      :approver-name="approverName"
      @changed="onRequestChanged"
    />
  </div>
</template>
