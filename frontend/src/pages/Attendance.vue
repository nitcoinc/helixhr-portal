<script setup>
import { ref, computed, watch } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
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

const calendar = createResource({
  url: 'hrms.api.get_attendance_calendar_events',
  makeParams: () => ({ from_date: monthStart.value, to_date: monthEnd.value }),
  auto: true,
})

watch([year, month], () => calendar.fetch())

const daysInMonth = computed(() => new Date(year.value, month.value + 1, 0).getDate())
const firstWeekday = computed(() => new Date(year.value, month.value, 1).getDay())

const days = computed(() => {
  const list = []
  for (let i = 0; i < firstWeekday.value; i++) list.push(null)
  for (let d = 1; d <= daysInMonth.value; d++) {
    const iso = isoDate(year.value, month.value, d)
    list.push({ day: d, iso, status: calendar.data?.[iso] })
  }
  return list
})

const summary = computed(() => {
  const counts = {}
  for (const status of Object.values(calendar.data || {})) {
    counts[status] = (counts[status] || 0) + 1
  }
  return counts
})

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

function openDay(day) {
  if (!day) return
  selectedDay.value = day.iso
  checkins.fetch()
}
function closeDay() {
  selectedDay.value = null
}
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

    <div class="flex max-w-xl flex-wrap gap-2 text-sm">
      <span
        v-for="(count, status) in summary"
        :key="status"
        class="flex items-center gap-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-ink-gray-7"
      >
        <span
          class="h-2 w-2 rounded-full"
          :class="STATUS_COLOR[status] || 'bg-surface-gray-4'"
        />
        {{ STATUS_LABEL[status] || status }}: {{ count }}
      </span>
      <span
        v-if="!calendar.loading && Object.keys(summary).length === 0"
        class="text-ink-gray-5"
      >
        No attendance recorded yet.
      </span>
    </div>

    <div class="max-w-xl rounded-xl border border-outline-gray-2 bg-surface-white p-3">
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
          :class="day ? 'cursor-pointer text-ink-gray-8 hover:bg-surface-gray-2' : ''"
          :aria-label="day ? `${monthLabel} ${day.day}` : undefined"
          @click="day && openDay(day)"
        >
          <span>{{ day?.day }}</span>
          <span
            v-if="day?.status"
            class="mt-0.5 h-1.5 w-1.5 rounded-full"
            :class="STATUS_COLOR[day.status] || 'bg-surface-gray-4'"
          />
        </component>
      </div>
    </div>

    <div
      v-if="selectedDay"
      class="fixed inset-x-0 bottom-0 z-10 rounded-t-xl border-t border-outline-gray-2 bg-surface-white p-4 shadow-lg"
    >
      <div class="mb-2 flex items-center justify-between">
        <h2 class="font-medium text-ink-gray-9">
          {{ formatDate(selectedDay) }}
        </h2>
        <button
          class="text-ink-gray-5"
          @click="closeDay"
        >
          Close
        </button>
      </div>
      <p
        v-if="checkins.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <ul
        v-else-if="checkins.data?.length"
        class="space-y-1"
      >
        <li
          v-for="row in checkins.data"
          :key="row.name"
          class="flex justify-between text-sm text-ink-gray-7"
        >
          <span>{{ row.log_type === 'IN' ? 'Check-in' : 'Check-out' }}</span>
          <span class="tabular">{{ formatTime(row.time) }}</span>
        </li>
      </ul>
      <p
        v-else
        class="text-sm text-ink-gray-5"
      >
        No check-ins recorded for this day.
      </p>
    </div>
  </div>
</template>
