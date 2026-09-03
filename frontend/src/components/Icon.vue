<script setup>
import { computed } from 'vue'
import { icons } from '@/lib/icons'

const props = defineProps({
  name: { type: String, required: true },
  // Tailwind size class, so callers stay on the spacing scale.
  size: { type: String, default: 'h-5 w-5' },
})

const paths = computed(() => icons[props.name] || [])
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    :class="size"
  >
    <path
      v-for="d in paths"
      :key="d"
      :d="d"
    />
  </svg>
</template>
