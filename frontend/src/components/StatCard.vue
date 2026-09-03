<script setup>
defineProps({
  title: { type: String, required: true },
  to: { type: [String, Object], default: null },
  loading: { type: Boolean, default: false },
  empty: { type: Boolean, default: false },
  emptyText: { type: String, default: 'Nothing here yet.' },
})
</script>

<template>
  <component
    :is="to ? 'router-link' : 'div'"
    :to="to"
    class="block rounded-lg border border-outline-gray-2 bg-surface-white p-4 transition-colors duration-200"
    :class="to ? 'hover:border-outline-gray-3 cursor-pointer' : ''"
  >
    <h2 class="text-sm font-medium text-ink-gray-6">
      {{ title }}
    </h2>
    <div class="mt-2">
      <p
        v-if="loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <p
        v-else-if="empty"
        class="text-ink-gray-5"
      >
        {{ emptyText }}
      </p>
      <slot v-else />
    </div>
  </component>
</template>
