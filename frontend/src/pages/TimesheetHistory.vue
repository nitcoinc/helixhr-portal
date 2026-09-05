<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import { session } from '@/lib/session'
import { formatDateRange } from '@/lib/dates'

// P2-U3 / P2-R21. Identity from the one bootstrap.
const employeeId = computed(() => session.employee?.name)

const history = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'Timesheet',
    filters: [['employee', '=', employeeId.value]],
    fields: ['name', 'start_date', 'end_date', 'total_hours', 'workflow_state'],
    order_by: 'start_date desc',
    limit_page_length: 0,
  }),
  auto: true,
})

const rows = computed(() => history.data || [])
</script>

<template>
  <div>
    <PageHeader title="Past timesheets" />

    <AsyncState
      section="timesheet-history"
      :resource="history"
      :empty="rows.length === 0"
      empty-title="No timesheets yet"
      empty-body="Log this week's hours and it will appear here once you send it."
      skeleton="row"
      :skeleton-rows="4"
    >
      <ul class="space-y-2">
        <li
          v-for="row in rows"
          :key="row.name"
        >
          <!-- Still the current week: the exact-week destination is P2-U6's
               (P2-R16, P2-AE5). The `/timesheet/:weekStart` route exists
               (P2-U2) but `Timesheet.vue` does not read the parameter yet, so
               pointing at it here would only promise something it cannot do. -->
          <router-link
            to="/timesheet"
            class="surface-card elev-1 flex items-center gap-3 p-3"
          >
            <span class="min-w-0 flex-1">
              <span class="tabular block font-medium text-ink-gray-9">
                {{ formatDateRange(row.start_date, row.end_date) }}
              </span>
              <span class="tabular block text-sm text-ink-gray-6">
                {{ row.total_hours }} hours
              </span>
            </span>
            <StatusBadge
              kind="timesheet"
              :status="row.workflow_state"
            />
            <Icon
              name="chevronRight"
              size="h-4 w-4"
              class="shrink-0 text-ink-gray-4"
            />
          </router-link>
        </li>
      </ul>
    </AsyncState>
  </div>
</template>
