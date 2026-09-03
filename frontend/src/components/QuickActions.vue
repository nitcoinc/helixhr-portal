<script setup>
import Icon from '@/components/Icon.vue'

// Three tiles rather than a button row: as plain buttons the first of the
// three read as a selected tab next to two unselected ones (the design
// system allows one filled button per screen, and these are three peers).
const ACTIONS = [
  { label: 'Ask for leave', hint: 'Book time off', to: '/leave', icon: 'leave' },
  { label: 'Fill timesheet', hint: 'Log this week', to: '/timesheet', icon: 'timesheet' },
  { label: 'New request', hint: 'Ask HR for something', to: '/requests', icon: 'requests' },
]
</script>

<template>
  <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
    <router-link
      v-for="action in ACTIONS"
      :key="action.to"
      :to="action.to"
      class="group flex cursor-pointer items-center gap-3 rounded-xl border border-outline-gray-2 bg-surface-white p-3 transition-colors duration-200 hover:border-blue-600 hover:bg-blue-50/30"
    >
      <span
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700"
      >
        <Icon :name="action.icon" />
      </span>
      <span class="min-w-0">
        <span class="block truncate text-sm font-medium text-ink-gray-8">{{ action.label }}</span>
        <span class="block truncate text-xs text-ink-gray-5">{{ action.hint }}</span>
      </span>
    </router-link>
  </div>
</template>
