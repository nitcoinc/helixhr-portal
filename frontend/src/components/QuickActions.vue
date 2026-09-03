<script setup>
import Icon from '@/components/Icon.vue'

// A labeled row of text actions with dividers, not three icon+heading+text
// tiles. The tile version was the same same-size-card scaffold this whole
// redesign existed to remove from the dashboard -- leaving three of them at
// the bottom of the page was the pattern surviving its own deletion.
const ACTIONS = [
  { label: 'Ask for leave', to: '/leave', icon: 'leave' },
  { label: 'Fill timesheet', to: '/timesheet', icon: 'timesheet' },
  { label: 'New request', to: '/requests', icon: 'requests' },
]
</script>

<template>
  <section aria-labelledby="quick-actions-heading">
    <h2
      id="quick-actions-heading"
      class="mb-2 text-sm font-medium text-ink-gray-7"
    >
      Start something
    </h2>
    <div
      class="elev-1 flex flex-col divide-y divide-outline-gray-2 overflow-hidden rounded-xl border border-outline-gray-1 bg-surface-white sm:flex-row sm:divide-x sm:divide-y-0"
    >
      <router-link
        v-for="action in ACTIONS"
        :key="action.to"
        :to="action.to"
        class="flex min-h-11 flex-1 cursor-pointer items-center gap-2 px-4 py-3 text-sm font-medium text-ink-gray-8 transition-colors duration-200 hover:bg-blue-50 hover:text-blue-700"
      >
        <Icon
          :name="action.icon"
          size="h-4 w-4"
          class="shrink-0 text-ink-gray-5"
        />
        {{ action.label }}
      </router-link>
    </div>
  </section>
</template>
