<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { roundHours } from '@/lib/hours'
import { formatDateRange, mondayOf, today } from '@/lib/dates'
import { addMonths, formatMonth, isMonth, monthOf, weekCardStatus } from '@/lib/month'

const props = defineProps({
  // The Monday (YYYY-MM-DD) of the week on screen in the editor.
  monday: { type: String, required: true },
  /** Phone (U9, EP-02): the week editor comes first, so the month folds
   * into one summary row under it that opens on a tap. */
  collapsible: { type: Boolean, default: false },
})

const router = useRouter()
const route = useRoute()

// --- the month overview (plan 2026-10-05-001 U5, R3-R5) ------------------
//
// `?month=` wins when it is a real YYYY-MM; otherwise the month is the one
// the selected week's Monday falls in, so /timesheet and
// /timesheet/<monday> both show the overview around the week on screen.
const month = computed(() =>
  isMonth(route.query.month) ? route.query.month : monthOf(props.monday),
)
const monthView = createResource({
  url: 'helixhr.api.get_my_month',
  makeParams: () => ({ month: month.value }),
  auto: true,
})
watch(month, () => monthView.reload())
const thisMonday = computed(() => mondayOf(today()))

const monthWeeks = computed(() =>
  (monthView.data?.weeks || []).map((w) => {
    const status = weekCardStatus(w)
    const total = roundHours(w.total_hours || 0)
    // ED-13: no "not measured" -- without a standard day the figure is
    // just the hours.
    const hoursText =
      w.expected_hours === null || w.expected_hours === undefined
        ? `${total}h`
        : `${total} of ${roundHours(w.expected_hours)}h`
    return {
      ...w,
      status,
      hoursText,
      range: formatDateRange(w.week_start, w.week_end),
      selected: w.week_start === props.monday,
      current: w.week_start === thisMonday.value,
    }
  }),
)

// Open from the start when the URL already names a month: the phone user
// moved months or opened a week from this list, so they are mid-browse.
const open = ref(!props.collapsible || isMonth(route.query.month))
const missingCount = computed(() => monthWeeks.value.filter((w) => w.missing).length)
const summary = computed(() => {
  if (!monthView.data) return formatMonth(month.value)
  const n = missingCount.value
  const missing = n ? `${n} ${n === 1 ? 'week' : 'weeks'} missing` : 'nothing missing'
  return `${formatMonth(month.value)}: ${missing}`
})

function weekLink(iso) {
  return { name: 'TimesheetWeek', params: { weekStart: iso }, query: { month: month.value } }
}
function shiftMonth(delta) {
  router.push({ query: { ...route.query, month: addMonths(month.value, delta) } })
}
// ED-12: with the strip as the one week navigator, "This week" lives here,
// and only when the week on screen is another one.
const thisWeekLink = computed(() => ({ name: 'TimesheetWeek', params: { weekStart: thisMonday.value } }))

defineExpose({ reload: () => monthView.reload() })
</script>

<template>
  <!-- Plan 2026-10-05-001 U5 (R3-R5): the month at a glance. Each week is
       a link to its editor; state and "missing" are words, never colour
       alone. A vertical list on a phone, one row of cards on desktop.
       Plan 2026-10-17-001 U9: on desktop this strip is the page's one week
       navigator (ED-12); on a phone it folds under the week (EP-02). -->
  <section aria-labelledby="month-heading">
    <button
      v-if="collapsible"
      class="surface-card elev-1 flex min-h-11 w-full cursor-pointer items-center justify-between gap-3 px-3 py-2 text-left text-sm font-medium text-ink-gray-9 hover:border-blue-600"
      type="button"
      :aria-expanded="open"
      aria-controls="month-overview-body"
      data-testid="month-summary"
      @click="open = !open"
    >
      <span>{{ summary }}</span>
      <Icon
        name="chevronDown"
        class="shrink-0 text-ink-gray-6 transition-transform"
        :class="open ? 'rotate-180' : ''"
      />
    </button>
    <div
      v-show="open"
      id="month-overview-body"
      :class="collapsible ? 'mt-2' : ''"
    >
      <div class="mb-2 flex items-center gap-1">
        <button
          class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-md text-ink-gray-7 hover:bg-surface-gray-2"
          type="button"
          aria-label="Previous month"
          @click="shiftMonth(-1)"
        >
          <Icon name="chevronLeft" />
        </button>
        <h2
          id="month-heading"
          class="type-section min-w-36 text-center text-ink-gray-9"
        >
          {{ formatMonth(month) }}
        </h2>
        <button
          class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-md text-ink-gray-7 hover:bg-surface-gray-2"
          type="button"
          aria-label="Next month"
          @click="shiftMonth(1)"
        >
          <Icon name="chevronRight" />
        </button>
        <router-link
          v-if="!collapsible && monday !== thisMonday"
          :to="thisWeekLink"
          class="ml-2 inline-flex min-h-11 items-center text-sm font-medium text-ink-blue-link underline underline-offset-2"
        >
          This week
        </router-link>
      </div>
      <AsyncState
        section="timesheet-month"
        :resource="monthView"
        :empty="false"
        skeleton="block"
        skeleton-height="h-20"
      >
        <ol
          class="flex flex-col gap-2 sm:grid sm:grid-cols-3 lg:grid-flow-col lg:auto-cols-fr lg:grid-cols-none"
          data-testid="month-weeks"
        >
          <li
            v-for="w in monthWeeks"
            :key="w.week_start"
          >
            <!-- ED-13 / EP-14: a resting card with one StatusBadge; a fixed
                 two-column grid on a phone so the status column lines up
                 row to row. -->
            <router-link
              :to="weekLink(w.week_start)"
              class="surface-card elev-1 grid h-full min-h-11 grid-cols-2 items-center gap-2 px-3 py-2 text-sm hover:border-blue-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-outline-gray-4 sm:flex sm:flex-col sm:items-start sm:justify-between"
              :class="w.selected ? 'border-blue-600 ring-1 ring-blue-600' : ''"
              :aria-current="w.selected ? 'page' : undefined"
              :aria-label="`${w.range}${w.current ? ', this week' : ''}: ${w.status.label}, ${w.hoursText}${w.missing ? ', missing' : ''}`"
              :data-week="w.week_start"
            >
              <span class="min-w-0">
                <span
                  v-if="w.current"
                  class="label block"
                >This week</span>
                <span class="tabular font-medium text-ink-gray-9">{{ w.range }}</span>
              </span>
              <span class="flex flex-wrap items-center gap-1.5">
                <StatusBadge
                  kind="timesheet"
                  :status="w.status.status"
                />
                <span class="tabular text-xs text-ink-gray-6">{{ w.hoursText }}</span>
              </span>
            </router-link>
          </li>
        </ol>
      </AsyncState>
    </div>
  </section>
</template>
