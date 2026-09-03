<script setup>
defineProps({ title: { type: String, required: true } })
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center gap-2 bg-surface-white px-4 text-center">
    <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
      {{ title }}
    </h1>
    <p class="text-ink-gray-6">
      This page is coming soon.
    </p>
    <router-link
      to="/"
      class="text-blue-700 underline"
    >
      Back to Dashboard
    </router-link>
  </div>
</template>
