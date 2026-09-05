<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Dialog } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { formatDate, formatTime } from '@/lib/dates'

const STATUS_COLOR = {
  Present: 'bg-surface-green-3',
  Absent: 'bg-surface-red-3',
  'Half Day': 'bg-surface-amber-3',
  'On Leave': 'bg-surface-blue-3',
  Holiday: 'bg-surface-gray-4',
}
const STATUS_LABEL = {
  Present: 'Present',
  Absent: 'Absent',
  'Half Day': 'Half day',
  'On Leave': 'On leave',
  Holiday: 'Holiday',
}

const today = new Date()
const year = ref(today.getFullYear())
const month = ref(today.getMonth()) // 0-indexed

const monthLabel = computed(() =>
  new Date(year.value, month.value, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  }),
)

function pad(n) {
  return String(n).padStart(2, '0')
}
function isoDate(y, m, d) {
  return `${y}-${pad(m + 1)}-${pad(d)}`
}

const monthStart = computed(() => isoDate(year.value, month.value, 1))
const monthEnd = computed(() => {
  const lastDay = new Date(year.value, month.value + 1, 0).getDate()
  return isoDate(year.value, month.value, lastDay)
})

// helixhr.api.get_my_attendance rather than hrms.api directly: the exceptions
// R16 asks for (late, missing) need the Attendance late_entry flag and the
// employee's holiday list, and "missing" has a rule that belongs on the server.
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

watch([year, month], () => calendar.fetch())

const daysInMonth = computed(() => new Date(year.value, month.value + 1, 0).getDate())
const firstWeekday = computed(() => new Date(year.value, month.value, 1).getDay())

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
  year.value = today.getFullYear()
  month.value = today.getMonth()
}

const selectedDay = ref(null)
const checkins = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'Employee Checkin',
    filters: [['time', 'between', [`${selectedDay.value} 00:00:00`, `${selectedDay.value} 23:59:59`]]],
    fields: ['name', 'time', 'log_type'],
    order_by: 'time asc',
    limit_page_length: 0,
  }),
  auto: false,
})

function dayLabel(day) {
  const parts = [`${monthLabel.value} ${day.day}`]
  if (day.status) parts.push(STATUS_LABEL[day.status] || day.status)
  if (day.late) parts.push('late arrival')
  if (day.missing) parts.push('no record')
  return parts.join(', ')
}

function openDay(day) {
  if (!day) return
  selectedDay.value = day.iso
  checkins.fetch()
}

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
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Attendance" />

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
          class="cursor-pointer rounded-full bg-surface-gray-2 px-3 py-1.5 text-xs text-ink-gray-7 hover:bg-surface-gray-3"
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

    <AsyncState
      class="max-w-xl"
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
            {{ STATUS_LABEL[status] || status }}
          </p>
        </div>
        <p
          v-else
          class="text-sm text-blue-100"
        >
          No attendance recorded yet this month.
        </p>

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
            v-for="d in ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']"
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
    </AsyncState>

    <Dialog
      v-model="dayOpen"
      :options="{ title: formatDate(selectedDay), size: 'sm' }"
    >
      <template #body-content>
        <AsyncState
          section="attendance-day"
          :resource="checkins"
          :empty="!checkins.data?.length"
          empty-title="No check-ins for this day"
          empty-body="Check-in isn't set up yet, so there's nothing recorded here."
          skeleton="row"
          :skeleton-rows="2"
        >
          <ul class="divide-y divide-outline-gray-1">
            <li
              v-for="row in checkins.data"
              :key="row.name"
              class="flex justify-between py-2 text-sm text-ink-gray-7"
            >
              <span>{{ row.log_type === 'IN' ? 'Check-in' : 'Check-out' }}</span>
              <span class="tabular">{{ formatTime(row.time) }}</span>
            </li>
          </ul>
        </AsyncState>
      </template>
    </Dialog>
  </div>
</template>
