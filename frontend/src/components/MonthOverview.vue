<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { roundHours } from '@/lib/hours'
import { formatDateRange, mondayOf, today } from '@/lib/dates'
import { addMonths, formatMonth, isMonth, monthOf, weekCardStatus } from '@/lib/month'

const props = defineProps({
  // The Monday (YYYY-MM-DD) of the week on screen in the editor.
  monday: { type: String, required: true },
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
    const hoursText =
      w.expected_hours === null || w.expected_hours === undefined
        ? `${total}h, not measured`
        : `${total} of ${roundHours(w.expected_hours)}h`
    return {
      ...w,
      status,
      hoursText,
      range: formatDateRange(w.week_start, w.week_end),
      selected: w.week_start === props.monday,
      current: w.week_start === thisMonday.value,
      approved: w.state === 'Approved' && !w.change_open,
    }
  }),
)

function weekLink(iso) {
  return { name: 'TimesheetWeek', params: { weekStart: iso }, query: { month: month.value } }
}
function shiftMonth(delta) {
  router.push({ query: { ...route.query, month: addMonths(month.value, delta) } })
}

defineExpose({ reload: () => monthView.reload() })
</script>

<template>
  <!-- Plan 2026-10-05-001 U5 (R3-R5): the month at a glance. Each week is
       a link to its editor; state and "missing" are words, never colour
       alone. A vertical list on a phone, one row of cards on desktop. -->
  <section aria-labelledby="month-heading">
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
    </div>
    <AsyncState
      section="timesheet-month"
      :resource="monthView"
      :empty="false"
      skeleton="block"
      skeleton-height="h-20"
    >
      <ol
        class="flex flex-col gap-2 sm:grid sm:grid-cols-3 lg:grid-cols-6"
        data-testid="month-weeks"
      >
        <li
          v-for="w in monthWeeks"
          :key="w.week_start"
        >
          <router-link
            :to="weekLink(w.week_start)"
            class="flex h-full min-h-11 items-center justify-between gap-2 rounded-lg border bg-surface-white px-3 py-2 text-sm hover:bg-surface-gray-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-outline-gray-4 sm:flex-col sm:items-start"
            :class="[
              w.selected ? 'border-outline-gray-4 ring-1 ring-outline-gray-4' : 'border-outline-gray-2',
              w.approved ? 'bg-surface-green-1' : '',
            ]"
            :aria-current="w.selected ? 'page' : undefined"
            :aria-label="`${w.range}${w.current ? ', this week' : ''}: ${w.status.label}, ${w.hoursText}${w.missing ? ', missing' : ''}`"
            :data-week="w.week_start"
          >
            <span class="tabular min-w-0 font-medium text-ink-gray-9">
              {{ w.range }}
              <span
                v-if="w.current"
                class="font-normal text-ink-gray-6"
              > &middot; This week</span>
            </span>
            <span class="flex flex-wrap items-center gap-1.5">
              <span
                class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium"
                :class="w.status.toneClass"
              >
                <Icon
                  v-if="w.approved"
                  name="approvals"
                  size="h-3.5 w-3.5"
                />
                {{ w.status.label }}
              </span>
              <span
                v-if="w.missing"
                class="rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ring-current text-ink-red-4"
              >Missing</span>
              <span class="tabular text-xs text-ink-gray-6">{{ w.hoursText }}</span>
            </span>
          </router-link>
        </li>
      </ol>
    </AsyncState>
  </section>
</template>
