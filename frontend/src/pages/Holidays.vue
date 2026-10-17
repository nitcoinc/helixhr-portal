<script setup>
import { ref, computed, watch } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import Icon from '@/components/Icon.vue'
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
const yearOptions = computed(() => years.value.map((value) => ({ value, label: String(value) })))
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
const upcoming = computed(() => rows.value.filter((row) => row.date >= today()))
const past = computed(() => rows.value.filter((row) => row.date < today()))

// EP-22. The field block already names the next holiday, so Coming up starts
// from the one after it rather than saying the same day twice. Matched on the
// date, not the position: `next` is the server's, and a year other than this
// one may not lead with it.
const comingUp = computed(() =>
  next.value && upcoming.value[0]?.date === next.value.date
    ? upcoming.value.slice(1)
    : upcoming.value,
)
const pastLabel = computed(() =>
  isCurrentYear.value ? 'Earlier this year' : `Earlier in ${year.value}`,
)

// EP-22. The days already gone fold behind "Show past". Open by default only
// when nothing is still to come -- a past year, or December after the last
// one -- because then the past *is* the list. `null` means "not touched", so
// a tap wins over the default until the year changes.
const pastToggled = ref(null)
const pastOpen = computed(() => pastToggled.value ?? upcoming.value.length === 0)
watch(year, () => {
  pastToggled.value = null
})

const sections = computed(() =>
  [
    { key: 'coming-up', label: 'Coming up', rows: comingUp.value, fold: false },
    { key: 'earlier', label: pastLabel.value, rows: past.value, fold: true },
  ].filter((section) => section.rows.length),
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
    <SegmentedChips
      v-if="years.length > 1"
      v-model="year"
      class="mb-4"
      label="Year"
      :options="yearOptions"
    />

    <AsyncState
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

      <!-- Compact rows (ED-21): the date tile carries the date, so the row is
           the tile's height -- name and weekday beside it -- rather than an
           80px card. Two columns at desktop widths, in the shell's own
           max-w-5xl like every other list page (ED-20). -->
      <div
        v-for="section in sections"
        :key="section.key"
        class="mb-6 last:mb-0"
      >
        <!-- EP-22. A real disclosure: the days already gone are reachable,
             not in the way. Only rendered when there are some. -->
        <button
          v-if="section.fold"
          type="button"
          class="mb-1 inline-flex min-h-11 cursor-pointer items-center gap-1.5 text-sm font-medium text-blue-700 hover:underline"
          :aria-expanded="pastOpen ? 'true' : 'false'"
          :aria-controls="`holidays-${section.key}`"
          @click="pastToggled = !pastOpen"
        >
          <Icon
            name="chevronRight"
            class="shrink-0 transition-transform duration-200"
            :class="pastOpen ? 'rotate-90' : ''"
          />
          <span v-if="pastOpen">Hide past</span>
          <span v-else>Show past (<span class="tabular">{{ section.rows.length }}</span>)</span>
        </button>
        <div
          v-show="!section.fold || pastOpen"
          :id="`holidays-${section.key}`"
        >
          <h2 class="label mb-2">
            {{ section.label }}
          </h2>
          <ul class="space-y-2 lg:grid lg:grid-cols-2 lg:gap-2 lg:space-y-0">
            <li
              v-for="row in section.rows"
              :key="row.date"
              class="surface-card elev-1 flex items-center gap-3 p-1 pr-3"
            >
              <span
                class="date-tile"
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
                <!-- The tile is aria-hidden, so the full date is read here. -->
                <p class="text-sm text-ink-gray-6">
                  {{ row.weekday }}<span class="sr-only">, {{ formatDate(row.date) }}</span>
                </p>
              </div>
            </li>
          </ul>
        </div>
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
