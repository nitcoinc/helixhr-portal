<script setup>
defineProps({
  title: { type: String, required: true },
  /** One short line under the title. Optional -- most pages don't need one. */
  subtitle: { type: String, default: '' },
})
</script>

<template>
  <!-- Every page's title block. Before the app shell landed each page
       repeated this markup inline with its own <header> bar, which is why
       they drifted apart; the shell owns the chrome now and pages own
       only their title and primary action. -->
  <div class="mb-5 flex flex-wrap items-start justify-between gap-3">
    <div>
      <h1 class="font-heading text-2xl font-semibold tracking-tight text-ink-gray-9">
        {{ title }}
      </h1>
      <p
        v-if="subtitle"
        class="mt-1 text-sm text-ink-gray-6"
      >
        {{ subtitle }}
      </p>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
