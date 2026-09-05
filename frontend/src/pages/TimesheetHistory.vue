<script setup>
import { ref, computed } from 'vue'
import { createResource, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import { formatDateRange } from '@/lib/dates'

// P2-U6 / P2-R22. One bounded page of weeks, from a session-scoped method.
// The page used to ask `frappe.client.get_list` for `limit_page_length: 0` --
// every week the employee had ever filed, to render a dozen -- and it had no
// way to show the manager's reason, because the Employee Self Service role
// cannot read Comment.
const PAGE = 12
const pageLimit = ref(PAGE)

const history = createResource({
  url: 'helixhr.api.get_my_timesheet_history',
  makeParams: () => ({ limit: pageLimit.value }),
  auto: true,
})

const weeks = computed(() => history.data?.weeks || [])
const total = computed(() => history.data?.total || 0)
const fullWeek = computed(() => history.data?.full_week_hours || 40)
const moreCount = computed(() => Math.max(0, total.value - weeks.value.length))

function showMore() {
  pageLimit.value = Math.min(52, pageLimit.value + PAGE)
  history.reload()
}

const average = computed(() => {
  if (!weeks.value.length) return null
  const sum = weeks.value.reduce((carry, week) => carry + (week.total_hours || 0), 0)
  return (sum / weeks.value.length).toFixed(1)
})

// Grouped by month, which is how people remember a timesheet ("that week in
// August"). The month name comes from the range formatter rather than a
// second date vocabulary: `lib/dates.js` is the only calendar module.
const MONTHS = [
  'JANUARY',
  'FEBRUARY',
  'MARCH',
  'APRIL',
  'MAY',
  'JUNE',
  'JULY',
  'AUGUST',
  'SEPTEMBER',
  'OCTOBER',
  'NOVEMBER',
  'DECEMBER',
]

const groups = computed(() => {
  const found = []
  for (const week of weeks.value) {
    const [year, month] = week.week_start.split('-')
    const key = `${year}-${month}`
    let group = found.find((entry) => entry.key === key)
    if (!group) {
      group = { key, label: MONTHS[Number(month) - 1], weeks: [] }
      found.push(group)
    }
    group.weeks.push(week)
  }
  return found
})

function barWidth(week) {
  if (!fullWeek.value) return 0
  return Math.max(0, Math.min(100, ((week.total_hours || 0) / fullWeek.value) * 100))
}

/** Each row opens **that** week (P2-R12, P2-AE5). Rows used to link to
 * `/timesheet`, which resolves to whatever week is current when the link is
 * followed -- the same answer only by accident. */
function weekRoute(week) {
  return { name: 'TimesheetWeek', params: { weekStart: week.week_start } }
}
</script>

<template>
  <div>
    <PageHeader title="Past weeks">
      <template #actions>
        <span
          v-if="average"
          class="tabular text-sm text-ink-gray-6"
        >
          Avg {{ average }} h
        </span>
      </template>
    </PageHeader>

    <router-link
      class="mb-2 inline-flex min-h-11 cursor-pointer items-center gap-1 text-sm font-medium text-ink-blue-link hover:underline"
      :to="{ name: 'Timesheet' }"
    >
      <Icon
        name="chevronLeft"
        size="h-4 w-4"
      />
      Timesheet
    </router-link>

    <AsyncState
      section="timesheet-history"
      :resource="history"
      :empty="weeks.length === 0"
      empty-title="No timesheets yet"
      empty-body="Log this week's hours and it will appear here once you send it."
      skeleton="row"
      :skeleton-rows="4"
    >
      <div class="space-y-5">
        <section
          v-for="group in groups"
          :key="group.key"
        >
          <h2 class="label mb-2">
            {{ group.label }}
          </h2>
          <ul class="space-y-2">
            <li
              v-for="week in group.weeks"
              :key="week.name"
            >
              <router-link
                class="surface-card elev-1 block p-3"
                :to="weekRoute(week)"
              >
                <span class="flex items-center gap-3">
                  <span class="tabular min-w-0 flex-1 truncate font-semibold text-ink-gray-9">
                    {{ formatDateRange(week.week_start, week.week_end) }}
                  </span>
                  <StatusBadge
                    kind="timesheet"
                    :status="week.workflow_state"
                  />
                </span>

                <span class="mt-2 flex items-center gap-3">
                  <!-- The week read against a full one, so a short week
                       looks short without doing the arithmetic. -->
                  <span class="h-1.5 min-w-0 flex-1 rounded-full bg-surface-gray-3">
                    <span
                      class="block h-1.5 rounded-full bg-surface-green-3"
                      :style="{ width: `${barWidth(week)}%` }"
                    />
                  </span>
                  <span class="tabular shrink-0 text-sm text-ink-gray-6">
                    {{ (week.total_hours || 0).toFixed(1) }} h
                  </span>
                  <Icon
                    name="chevronRight"
                    size="h-4 w-4"
                    class="shrink-0 text-ink-gray-4"
                  />
                </span>

                <span
                  v-if="week.rejection_comment"
                  class="mt-2 block text-sm italic text-ink-gray-6"
                >
                  &ldquo;{{ week.rejection_comment }}&rdquo;
                </span>
              </router-link>
            </li>
          </ul>
        </section>

        <Button
          v-if="moreCount"
          class="w-full"
          variant="subtle"
          :loading="history.loading"
          @click="showMore"
        >
          Show {{ Math.min(moreCount, PAGE) }} more
        </Button>
      </div>
    </AsyncState>
  </div>
</template>
