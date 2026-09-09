<script setup>
import { ref, computed, watch } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { dateTileParts, formatDate, today } from '@/lib/dates'
import { HOLIDAY_LIST_UNKNOWN } from '@/lib/holidays'

// P3-U3 / P3-R10, P3-R11. The holidays HRMS resolves for *this* employee,
// which is not the same thing as "the company's holiday list": the
// assignment can be per employee, and it can change mid-year. The server
// resolves both and this page renders what came back -- there is no holiday
// arithmetic in the browser, and no second implementation of the fallback
// rule (P3-KTD1).
const year = ref(Number(today().slice(0, 4)))

const holidays = createResource({
  url: 'helixhr.api.get_my_holidays',
  makeParams: () => ({ year: year.value }),
  auto: true,
})
watch(year, () => holidays.fetch())

const rows = computed(() => holidays.data?.holidays || [])
const years = computed(() => holidays.data?.years || [year.value])
const next = computed(() => holidays.data?.next || null)
const listName = computed(() => holidays.data?.holiday_list || '')
// Only ever false once a response has landed. Before that the region is
// pending, not unknowable, and AsyncState is showing its skeleton.
const known = computed(() => !holidays.data || holidays.data.known !== false)
const isCurrentYear = computed(() => year.value === Number(today().slice(0, 4)))

// Coming up / Earlier, the same division Leave.vue makes: on a holiday list
// the only question anyone opens the page with is "which is next", and the
// days already gone still have to be reachable for a leave plan. The
// server's own today, never the host clock (P2-R5).
const groups = computed(() =>
  [
    {
      key: 'coming-up',
      label: 'Coming up',
      rows: rows.value.filter((row) => row.date >= today()),
    },
    {
      key: 'earlier',
      label: isCurrentYear.value ? 'Earlier this year' : `Earlier in ${year.value}`,
      rows: rows.value.filter((row) => row.date < today()),
    },
  ].filter((group) => group.rows.length),
)

// The date tile (index.css, `.date-tile`). `|| {}` so a value that is not a
// calendar date renders as an empty tile rather than throwing -- these dates
// are the server's, so it never happens.
function tile(date) {
  return dateTileParts(date) || {}
}

/** The countdown, in the words a person would use. `days_until` is the
 * server's, computed against the same today the rest of the portal uses. */
function countdown(days) {
  if (days <= 0) return 'Today'
  if (days === 1) return 'Tomorrow'
  return `in ${days} days`
}

function holidayName(row) {
  return row.description || 'Holiday'
}

/** Which list the dates above came from, in one sentence. */
const footnote = computed(() =>
  listName.value
    ? `From your holiday list, ${listName.value}. Your weekly days off aren't listed here.`
    : "From your holiday list. Your weekly days off aren't listed here.",
)

const emptyTitle = computed(() =>
  known.value ? `No holidays listed for ${year.value}` : "We can't tell your holidays yet",
)
const emptyBody = computed(() =>
  known.value
    ? 'Your holiday list has no holidays in this year. Ask HR if you think that is wrong.'
    : // P3-R11: the same cannot-tell sentence the attendance-request preview
      // says, from one place (@/lib/holidays).
      HOLIDAY_LIST_UNKNOWN,
)
</script>

<template>
  <div>
    <PageHeader title="Holidays" />

    <!-- The year chips, outside the async region and above it -- the same
         place Payslips and Directory put their filters. Inside the region
         they were removed by their own effect: choosing a year with no
         holidays in it rendered the empty state, which took the only control
         that could leave that year with it (P3-R11). The server sends the
         whole `years` list whether or not the chosen year has anything in
         it, so the chips stay accurate in the empty state. -->
    <div
      v-if="years.length > 1"
      class="mb-4 flex max-w-xl flex-wrap gap-2"
      role="group"
      aria-label="Year"
    >
      <button
        v-for="option in years"
        :key="option"
        type="button"
        class="tabular min-h-11 cursor-pointer rounded-full border px-4 text-sm font-medium"
        :class="
          option === year
            ? 'border-field bg-surface-white text-ink-gray-9 ring-1 ring-field'
            : 'border-outline-gray-2 bg-surface-white text-ink-gray-7 hover:bg-surface-gray-2'
        "
        :aria-pressed="option === year"
        @click="year = option"
      >
        {{ option }}
      </button>
    </div>

    <AsyncState
      class="max-w-xl"
      section="holidays"
      :resource="holidays"
      :empty="rows.length === 0"
      :empty-title="emptyTitle"
      :empty-body="emptyBody"
      skeleton="field"
      skeleton-height="h-32"
    >
      <!-- The anchored region: the next holiday and how far away it is. One
           per page, and the only place signal yellow is legal. -->
      <section
        class="surface-field elev-2 mb-4 p-4"
        aria-label="Next holiday"
      >
        <h2 class="label !text-blue-200 mb-2">
          Next holiday
        </h2>
        <template v-if="next">
          <p class="font-heading text-lg font-bold text-white">
            {{ next.description || 'Holiday' }}
          </p>
          <p class="mt-0.5 text-sm text-blue-100">
            {{ formatDate(next.date) }}
          </p>
          <p class="mt-3">
            <span
              class="tabular rounded-full bg-signal px-3 py-1 text-sm font-bold text-field"
            >{{ countdown(next.days_until) }}</span>
          </p>
        </template>
        <p
          v-else
          class="text-sm text-blue-200"
        >
          No holidays left in {{ year }}.
        </p>
      </section>

      <div
        v-for="group in groups"
        :key="group.key"
        class="mb-6 last:mb-0"
      >
        <h2 class="label mb-2">
          {{ group.label }}
        </h2>
        <ul class="space-y-2">
          <li
            v-for="row in group.rows"
            :key="row.date"
            class="surface-card elev-1 flex gap-3 p-3"
          >
            <span
              class="date-tile mt-0.5"
              aria-hidden="true"
            >
              <span class="date-tile-month">{{ tile(row.date).month }}</span>
              <span class="date-tile-day">{{ tile(row.date).day }}</span>
            </span>
            <div class="min-w-0 flex-1">
              <p class="font-medium text-ink-gray-9">
                {{ holidayName(row) }}
                <!-- Half day is a property of the holiday, not a decoration:
                     it changes whether anyone is expected at work. -->
                <span
                  v-if="row.is_half_day"
                  class="ml-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
                >Half day</span>
              </p>
              <p class="mt-0.5 text-sm text-ink-gray-6">
                {{ row.weekday }} · {{ formatDate(row.date) }}
              </p>
            </div>
          </li>
        </ul>
      </div>

      <!-- Which list this is, and the one thing about it that surprises
           people: the weekly days off are holidays in HRMS too, and listing
           104 of them would bury the eight days that matter (P3-R10). -->
      <p class="mt-4 text-sm text-ink-gray-5">
        {{ footnote }}
      </p>
    </AsyncState>
  </div>
</template>
