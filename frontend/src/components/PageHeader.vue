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
       only their title and primary action.

       The title is the `type-page-title` role (26px/700/-2%) rather than a
       Tailwind size chosen per page -- `text-2xl` here and `text-xl` there
       was the visible half of the same drift. `items-center` keeps the
       primary action optically aligned with the title rather than with the
       top of its ascenders, which is what the canvas shows on Leave and
       Requests. -->
  <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
    <div class="min-w-0">
      <h1 class="type-page-title font-heading text-ink-gray-9">
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
