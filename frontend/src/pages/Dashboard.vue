<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'
import StatCard from '@/components/StatCard.vue'
import QuickActions from '@/components/QuickActions.vue'

const dashboard = createResource({
  url: 'helixhr.api.get_dashboard',
  auto: true,
})

const employee = computed(() => dashboard.data?.employee)
const roleLine = computed(() =>
  [employee.value?.designation, employee.value?.department].filter(Boolean).join(' · '),
)
const leaveTypeEntries = computed(() => Object.entries(dashboard.data?.leave_balances || {}))
const attendanceEntries = computed(() => Object.entries(dashboard.data?.attendance_this_month || {}))
const unreadNotifications = computed(() => dashboard.data?.unread_notifications)
</script>

<template>
  <div class="min-h-screen bg-surface-gray-1 pb-24">
    <header class="border-b border-outline-gray-2 bg-surface-white px-4 py-4">
      <p
        v-if="dashboard.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <template v-else-if="employee">
        <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
          {{ employee.employee_name }}
        </h1>
        <p
          v-if="roleLine"
          class="text-ink-gray-6"
        >
          {{ roleLine }}
        </p>
        <p class="mt-1 text-sm text-ink-gray-5">
          <span v-if="employee.manager_name">Reports to {{ employee.manager_name }}</span>
          <span v-if="employee.branch"> · {{ employee.branch }}</span>
        </p>
      </template>
    </header>

    <div class="px-4 py-4">
      <QuickActions />
    </div>

    <div class="grid grid-cols-1 gap-3 px-4 md:grid-cols-2">
      <StatCard
        title="Leave balance"
        to="/leave"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && leaveTypeEntries.length === 0"
        empty-text="No leave set up yet."
      >
        <ul class="space-y-1">
          <li
            v-for="[type, balance] in leaveTypeEntries"
            :key="type"
            class="flex justify-between text-ink-gray-8"
          >
            <span>{{ type }}</span>
            <span class="font-medium">{{ balance.balance_leaves }}</span>
          </li>
        </ul>
      </StatCard>

      <StatCard
        title="Attendance this month"
        to="/attendance"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && attendanceEntries.length === 0"
        empty-text="No attendance recorded yet."
      >
        <ul class="space-y-1">
          <li
            v-for="[status, count] in attendanceEntries"
            :key="status"
            class="flex justify-between text-ink-gray-8"
          >
            <span>{{ status }}</span>
            <span class="font-medium">{{ count }}</span>
          </li>
        </ul>
      </StatCard>

      <!-- Real content lands in U8 (workflow) and U9/U12 (requests, -->
      <!-- approvals) -- these cards are honest empty states until then. -->
      <StatCard
        title="This week's timesheet"
        to="/timesheet"
        :loading="dashboard.loading"
        empty
        empty-text="Nothing here yet."
      />
      <StatCard
        title="My pending items"
        :loading="dashboard.loading"
        empty
        empty-text="Nothing here yet."
      />

      <StatCard
        title="Notifications"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && !unreadNotifications"
        empty-text="You're all caught up."
      >
        <p class="text-2xl font-semibold text-ink-gray-9">
          {{ unreadNotifications }}
        </p>
      </StatCard>
    </div>
  </div>
</template>
