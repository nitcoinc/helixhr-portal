<script setup>
import { computed } from 'vue'
import { createResource, Badge } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import { formatDateRange } from '@/lib/dates'

const me = createResource({
  url: 'hrms.api.get_current_employee_info',
  auto: true,
  onSuccess: () => {
    if (me.data?.name) history.fetch()
  },
})

const history = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'Timesheet',
    filters: [['employee', '=', me.data.name]],
    fields: ['name', 'start_date', 'end_date', 'total_hours', 'workflow_state'],
    order_by: 'start_date desc',
    limit_page_length: 0,
  }),
  auto: false,
})

const rows = computed(() => history.data || [])

function badgeTheme(state) {
  if (state === 'Approved') return 'green'
  if (state === 'Rejected') return 'red'
  if (state === 'Pending Approval') return 'orange'
  return 'gray'
}
function badgeLabel(state) {
  if (state === 'Pending Approval') return 'Waiting for manager'
  if (state === 'Rejected') return 'Sent back'
  return state
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Past timesheets" />

    <div class="space-y-2">
      <p
        v-if="history.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <p
        v-else-if="rows.length === 0"
        class="text-ink-gray-5"
      >
        No timesheets yet.
      </p>
      <router-link
        v-for="row in rows"
        :key="row.name"
        to="/timesheet"
        class="flex items-center justify-between rounded-lg border border-outline-gray-2 bg-surface-white p-3"
      >
        <div>
          <p class="tabular text-ink-gray-9">
            {{ formatDateRange(row.start_date, row.end_date) }}
          </p>
          <p class="tabular text-sm text-ink-gray-6">
            {{ row.total_hours }} hours
          </p>
        </div>
        <Badge :theme="badgeTheme(row.workflow_state)">
          {{ badgeLabel(row.workflow_state) }}
        </Badge>
      </router-link>
    </div>
  </div>
</template>
