<script setup>
import { computed } from 'vue'
import Icon from '@/components/Icon.vue'
import { HANDLER_INBOX } from '@/lib/nav'
import { session } from '@/lib/session'

// A labelled row of text actions with dividers.
const ACTIONS = [
  // `?ask=1` opens the form on arrival, as the palette's Ask for leave does.
  { label: 'Ask for leave', to: { path: '/leave', query: { ask: '1' } }, icon: 'leave' },
  { label: 'Fill timesheet', to: '/timesheet', icon: 'timesheet' },
  { label: 'New request', to: '/requests', icon: 'requests' },
]

// Plan 2026-10-17-001 U6 (MH-13, AI-16): the jobs a role comes to Home for,
// one tap away instead of behind the sidebar's group. A second divided row
// under the first, drawn only when the session holds the role. Approvals
// keys on `canApprove` until U4 gives stable approvers their own flag.
const roleActions = computed(() =>
  [
    { label: 'Approvals', to: '/approvals', icon: 'approvals', show: session.canApprove },
    {
      label: 'Team inbox',
      to: HANDLER_INBOX,
      icon: 'requests',
      show: session.canHandleRequests,
    },
    { label: 'Report access', to: '/settings/report-access', icon: 'reports', show: session.canAdminPortal },
    { label: 'Email templates', to: '/email-templates', icon: 'mail', show: session.canAdminPortal },
  ].filter((action) => action.show),
)
const rows = computed(() => [ACTIONS, roleActions.value].filter((row) => row.length))
</script>

<template>
  <section aria-labelledby="quick-actions-heading">
    <h2
      id="quick-actions-heading"
      class="type-section mb-2 font-heading text-ink-gray-9"
    >
      Start something
    </h2>
    <div class="space-y-2">
      <div
        v-for="(row, index) in rows"
        :key="index"
        class="surface-card elev-1 flex flex-col divide-y divide-outline-gray-2 overflow-hidden sm:flex-row sm:divide-x sm:divide-y-0"
      >
        <router-link
          v-for="action in row"
          :key="action.label"
          :to="action.to"
          class="flex min-h-11 flex-1 cursor-pointer items-center gap-2 p-3 text-sm font-medium text-ink-gray-8 transition-colors duration-200 hover:bg-blue-50 hover:text-blue-700"
        >
          <Icon
            :name="action.icon"
            size="h-4 w-4"
            class="shrink-0 text-ink-gray-5"
          />
          {{ action.label }}
        </router-link>
      </div>
    </div>
  </section>
</template>
