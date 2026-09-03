<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'
import Icon from '@/components/Icon.vue'
import WeekSpine from '@/components/WeekSpine.vue'
import NeedsYou from '@/components/NeedsYou.vue'
import QuickActions from '@/components/QuickActions.vue'

const dashboard = createResource({
  url: 'helixhr.api.get_dashboard',
  auto: true,
})

const employee = computed(() => dashboard.data?.employee)
const roleLine = computed(() =>
  [employee.value?.designation, employee.value?.department].filter(Boolean).join(' · '),
)
const week = computed(() => dashboard.data?.week)
const needsYou = computed(() => dashboard.data?.needs_you?.items || [])
const needsYouMore = computed(() => dashboard.data?.needs_you?.more || 0)
const leaveTypeEntries = computed(() => Object.entries(dashboard.data?.leave_balances || {}))
const attendanceEntries = computed(() => Object.entries(dashboard.data?.attendance_this_month || {}))
// Both rails used to render entry[0] as though it were the whole story: the
// attendance figure printed its count with the status key thrown away, and a
// person holding three leave types saw one balance presented as their total.
const leadLeave = computed(() => leaveTypeEntries.value[0] || null)
const otherLeaveCount = computed(() => Math.max(0, leaveTypeEntries.value.length - 1))
const leadAttendance = computed(() => {
  const entries = [...attendanceEntries.value].sort((a, b) => b[1] - a[1])
  return entries[0] || null
})

const today = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
}).format(new Date())
</script>

<template>
  <div>
    <!-- Identity is one line, not a hero block: on a screen whose job is
         "what needs me", the person's own name is the least urgent thing on
         it. Designation, department and manager stay here but small; the
         profile page is where you go to read them. -->
    <header class="mb-4 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <div
        v-if="dashboard.loading"
        class="h-7 w-56 animate-pulse rounded bg-surface-gray-2"
        aria-busy="true"
      />
      <template v-else-if="employee">
        <h1 class="font-heading text-xl font-semibold tracking-tight text-ink-gray-9">
          {{ employee.employee_name }}
        </h1>
        <p class="text-sm text-ink-gray-6">
          {{ today }}
        </p>
        <p
          v-if="roleLine || employee.manager_name"
          class="w-full text-sm text-ink-gray-5"
        >
          <span v-if="roleLine">{{ roleLine }}</span>
          <span v-if="roleLine && employee.manager_name"> · </span>
          <span v-if="employee.manager_name">Reports to {{ employee.manager_name }}</span>
        </p>
      </template>
    </header>

    <WeekSpine
      class="mb-5"
      :week="week"
      :loading="dashboard.loading"
    />

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div class="lg:col-span-2">
        <NeedsYou
          :items="needsYou"
          :more="needsYouMore"
          :loading="dashboard.loading"
          :week-hours="week?.total_hours || 0"
          :timesheet-state="week?.timesheet_state"
        />
      </div>

      <!-- Reference numbers, deliberately demoted to a rail: they are what
           someone consults, not what they came to act on. -->
      <aside
        class="space-y-2"
        aria-label="Your numbers"
      >
        <router-link
          to="/leave"
          class="elev-1 group flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-outline-gray-1 bg-surface-white px-4 py-3 transition-colors duration-200 hover:border-blue-600"
        >
          <span class="min-w-0">
            <span class="block text-sm text-ink-gray-6">Leave left</span>
            <span class="block truncate text-xs text-ink-gray-5">
              <template v-if="leadLeave">
                {{ leadLeave[0] }}<template v-if="otherLeaveCount">
                  · +{{ otherLeaveCount }} more
                </template>
              </template>
              <template v-else>Nothing allocated yet</template>
            </span>
          </span>
          <span
            v-if="leadLeave"
            class="tabular font-heading text-2xl font-semibold text-ink-gray-9"
          >{{ leadLeave[1].balance_leaves }}</span>
          <Icon
            v-else
            name="chevronRight"
            size="h-4 w-4"
            class="text-ink-gray-4 group-hover:text-blue-700"
          />
        </router-link>

        <router-link
          v-if="leadAttendance"
          to="/attendance"
          class="elev-1 group flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-outline-gray-1 bg-surface-white px-4 py-3 transition-colors duration-200 hover:border-blue-600"
        >
          <span class="min-w-0">
            <span class="block text-sm text-ink-gray-6">Attendance</span>
            <span class="block truncate text-xs text-ink-gray-5">
              {{ leadAttendance[0] }} this month
            </span>
          </span>
          <span class="tabular font-heading text-2xl font-semibold text-ink-gray-9">
            {{ leadAttendance[1] }}
          </span>
        </router-link>

        <router-link
          to="/documents"
          class="elev-1 group flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-xl border border-outline-gray-1 bg-surface-white px-4 py-3 transition-colors duration-200 hover:border-blue-600"
        >
          <span class="text-sm text-ink-gray-6">Documents</span>
          <Icon
            name="chevronRight"
            size="h-4 w-4"
            class="text-ink-gray-4 group-hover:text-blue-700"
          />
        </router-link>
      </aside>
    </div>

    <QuickActions class="mt-6" />
  </div>
</template>
