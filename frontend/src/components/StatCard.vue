<script setup>
import Icon from '@/components/Icon.vue'

defineProps({
  title: { type: String, required: true },
  icon: { type: String, default: '' },
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
    class="group flex flex-col rounded-xl border border-outline-gray-2 bg-surface-white p-4 transition-colors duration-200"
    :class="to ? 'cursor-pointer hover:border-blue-600 hover:bg-blue-50/30' : ''"
  >
    <div class="flex items-center gap-2">
      <span
        v-if="icon"
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-surface-gray-2 text-ink-gray-6 group-hover:bg-blue-50 group-hover:text-blue-700"
      >
        <Icon
          :name="icon"
          size="h-4 w-4"
        />
      </span>
      <h2 class="flex-1 text-sm font-medium text-ink-gray-7">
        {{ title }}
      </h2>
      <Icon
        v-if="to"
        name="chevronRight"
        size="h-4 w-4"
        class="text-ink-gray-4 group-hover:text-blue-700"
      />
    </div>

    <div class="mt-3 flex-1">
      <!-- A skeleton, not a "Loading…" string: the design system asks for
           a loading state on every resource, and a text swap makes the
           card jump height when the real content arrives. -->
      <div
        v-if="loading"
        class="space-y-2"
        aria-busy="true"
      >
        <div class="h-4 w-2/3 animate-pulse rounded bg-surface-gray-2" />
        <div class="h-4 w-1/3 animate-pulse rounded bg-surface-gray-2" />
      </div>
      <p
        v-else-if="empty"
        class="text-sm text-ink-gray-5"
      >
        {{ emptyText }}
      </p>
      <slot v-else />
    </div>
  </component>
</template>
