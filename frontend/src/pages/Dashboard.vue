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
const initials = computed(() =>
  (employee.value?.employee_name || '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join(''),
)
const leaveTypeEntries = computed(() => Object.entries(dashboard.data?.leave_balances || {}))
const attendanceEntries = computed(() => Object.entries(dashboard.data?.attendance_this_month || {}))
const unreadNotifications = computed(() => dashboard.data?.unread_notifications)
const pending = computed(() => dashboard.data?.pending)
const hasApprovalsWaiting = computed(() => (pending.value?.approvals_waiting_for_me || 0) > 0)

// Rendered once on mount, not reactive: the portal is not open across a
// date boundary long enough for a live clock to be worth the watcher.
const today = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
}).format(new Date())
</script>

<template>
  <div>
    <!-- Identity hero. The shell's sidebar repeats the name in small type
         for orientation; this is the page's own H1 and the only place
         designation, department, manager and location are shown. -->
    <section class="mb-6 flex items-start gap-4">
      <span
        v-if="!dashboard.loading"
        class="hidden h-14 w-14 shrink-0 items-center justify-center rounded-full bg-blue-50 font-heading text-lg font-semibold text-blue-700 sm:flex"
      >
        {{ initials || '—' }}
      </span>
      <div class="min-w-0">
        <div
          v-if="dashboard.loading"
          class="space-y-2"
          aria-busy="true"
        >
          <div class="h-7 w-56 animate-pulse rounded bg-surface-gray-2" />
          <div class="h-4 w-40 animate-pulse rounded bg-surface-gray-2" />
        </div>
        <template v-else-if="employee">
          <p class="text-sm text-ink-gray-5">
            {{ today }}
          </p>
          <h1 class="font-heading text-2xl font-semibold tracking-tight text-ink-gray-9">
            {{ employee.employee_name }}
          </h1>
          <p
            v-if="roleLine"
            class="mt-0.5 text-ink-gray-6"
          >
            {{ roleLine }}
          </p>
          <p class="mt-2 text-sm text-ink-gray-5">
            <span v-if="employee.manager_name">Reports to {{ employee.manager_name }}</span>
            <span v-if="employee.branch"> · {{ employee.branch }}</span>
          </p>
        </template>
      </div>
    </section>

    <QuickActions class="mb-6" />

    <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
      <StatCard
        title="Leave balance"
        icon="leave"
        to="/leave"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && leaveTypeEntries.length === 0"
        empty-text="No leave set up yet. Ask HR to add your allocation."
      >
        <ul class="space-y-1.5">
          <li
            v-for="[type, balance] in leaveTypeEntries"
            :key="type"
            class="flex items-baseline justify-between gap-3"
          >
            <span class="truncate text-sm text-ink-gray-7">{{ type }}</span>
            <span class="font-heading text-xl font-semibold text-ink-gray-9">
              {{ balance.balance_leaves }}
            </span>
          </li>
        </ul>
      </StatCard>

      <StatCard
        title="Attendance this month"
        icon="attendance"
        to="/attendance"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && attendanceEntries.length === 0"
        empty-text="No attendance recorded yet."
      >
        <ul class="space-y-1.5">
          <li
            v-for="[status, count] in attendanceEntries"
            :key="status"
            class="flex items-baseline justify-between gap-3"
          >
            <span class="truncate text-sm text-ink-gray-7">{{ status }}</span>
            <span class="font-heading text-xl font-semibold text-ink-gray-9">{{ count }}</span>
          </li>
        </ul>
      </StatCard>

      <StatCard
        title="This week's timesheet"
        icon="timesheet"
        to="/timesheet"
        :loading="dashboard.loading"
        empty
        empty-text="Nothing logged yet. Fill your timesheet to get started."
      />

      <StatCard
        title="My pending items"
        icon="requests"
        to="/requests"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && !pending"
        empty-text="Nothing here yet."
      >
        <ul
          v-if="pending"
          class="space-y-1.5"
        >
          <li class="flex items-baseline justify-between gap-3">
            <span class="text-sm text-ink-gray-7">Leave waiting</span>
            <span class="font-heading text-xl font-semibold text-ink-gray-9">
              {{ pending.my_open_leave }}
            </span>
          </li>
          <li class="flex items-baseline justify-between gap-3">
            <span class="text-sm text-ink-gray-7">Requests open</span>
            <span class="font-heading text-xl font-semibold text-ink-gray-9">
              {{ pending.my_open_requests }}
            </span>
          </li>
        </ul>
      </StatCard>

      <StatCard
        v-if="hasApprovalsWaiting"
        title="Waiting for your approval"
        icon="approvals"
        to="/approvals"
        :loading="dashboard.loading"
      >
        <p class="font-heading text-3xl font-semibold text-ink-gray-9">
          {{ pending.approvals_waiting_for_me }}
        </p>
      </StatCard>

      <StatCard
        title="Notifications"
        icon="notifications"
        to="/notifications"
        :loading="dashboard.loading"
        :empty="!dashboard.loading && !unreadNotifications"
        empty-text="You're all caught up."
      >
        <p class="font-heading text-3xl font-semibold text-ink-gray-9">
          {{ unreadNotifications }}
          <span class="font-sans text-sm font-normal text-ink-gray-5">unread</span>
        </p>
      </StatCard>
    </div>
  </div>
</template>
