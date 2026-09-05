<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import WeekSpine from '@/components/WeekSpine.vue'
import NeedsYou from '@/components/NeedsYou.vue'
import QuickActions from '@/components/QuickActions.vue'

const dashboard = createResource({
  url: 'helixhr.api.get_dashboard',
  auto: true,
})

const employee = computed(() => dashboard.data?.employee)
// R6 wants name, designation, department, manager and location on the home
// screen. They ride one line rather than a stack: on a screen whose job is
// "what needs me", who you are is orientation, not the headline.
const identityLine = computed(() => {
  const e = employee.value
  if (!e) return ''
  return [
    e.designation,
    e.department,
    e.manager_name ? `Reports to ${e.manager_name}` : null,
    e.branch,
  ]
    .filter(Boolean)
    .join(' · ')
})
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
  <!--
    P2-U3 / P2-R23. The whole page body is one async region, and that is the
    fix for the U0 baseline's CLS of 0.8431.

    Every element below reads from the same `get_dashboard` response, but they
    used to be painted *before* it arrived, each with its own inline loading
    branch: a one-line header skeleton that resolved into three lines, a
    seven-bar spine skeleton that resolved 25px taller, and a two-row queue
    skeleton that resolved into up to eight rows (`_QUEUE_LIMIT` in
    helixhr/api.py). Each of those grew in place and shoved the rail and the
    quick actions below it down by 210px in one frame, ~1.5s after first
    paint. Measured attribution: one layout-shift entry, value 0.8377,
    naming this grid, the spine section, the aside and the quick-actions
    section.

    Painting the skeleton and the content as *alternative* subtrees of one
    region means nothing that has already been laid out ever moves: the
    skeleton nodes are removed and the content nodes are new, and neither is
    an "unstable element". Re-measured on the same pinned U0 profile and
    fixture set after this change: 0.0002.
  -->
  <AsyncState
    section="dashboard"
    :resource="dashboard"
    :empty="false"
  >
    <template #skeleton>
      <!-- Shaped like the page it stands in for, so the region reserves
           roughly the room the answer needs rather than a token strip. -->
      <div class="space-y-5">
        <div class="h-7 w-56 animate-pulse rounded bg-surface-gray-2" />
        <div class="h-44 animate-pulse rounded-xl bg-field/10" />
        <div class="space-y-2">
          <div
            v-for="n in 3"
            :key="n"
            class="h-20 animate-pulse rounded-xl bg-surface-gray-2"
          />
        </div>
      </div>
    </template>

    <div>
      <!-- Identity is one line, not a hero block: on a screen whose job is
           "what needs me", the person's own name is the least urgent thing on
           it. Designation, department and manager stay here but small; the
           profile page is where you go to read them. -->
      <header class="mb-4 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h1 class="font-heading text-xl font-semibold tracking-tight text-ink-gray-9">
          {{ employee?.employee_name }}
        </h1>
        <p class="text-sm text-ink-gray-6">
          {{ today }}
        </p>
        <p
          v-if="identityLine"
          class="w-full text-sm text-ink-gray-5"
        >
          {{ identityLine }}
        </p>
      </header>

      <WeekSpine
        class="mb-5"
        :week="week"
      />

      <div class="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div class="lg:col-span-2">
          <NeedsYou
            :items="needsYou"
            :more="needsYouMore"
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
            class="surface-card elev-1 group flex cursor-pointer items-center justify-between gap-3 px-4 py-3 transition-colors duration-200 hover:border-blue-600"
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
            class="surface-card elev-1 group flex cursor-pointer items-center justify-between gap-3 px-4 py-3 transition-colors duration-200 hover:border-blue-600"
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
            class="surface-card elev-1 group flex min-h-11 cursor-pointer items-center justify-between gap-3 px-4 py-3 transition-colors duration-200 hover:border-blue-600"
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
  </AsyncState>
</template>
