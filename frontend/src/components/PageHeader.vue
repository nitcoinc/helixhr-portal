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
       was the visible half of the same drift.

       The actions convention (plan 2026-10-17-001 U14, EP-13, ED-15):
         * the page's one create action is a frappe-ui `Button`
           variant="solid" theme="blue" (blue is the Signal brand green);
         * navigation (back, past weeks, open in Desk) is ghost: a
           `Button variant="ghost"`, or a real link styled the same way.
       `items-baseline` sits the slot on the title's baseline (MH-26).
       `items-center` centred it on the title *and* subtitle block, so on a
       page with a subtitle the button floated between the two lines, and
       Approvals' count pill rode above the title's baseline. -->
  <div class="mb-5 flex flex-wrap items-baseline justify-between gap-3">
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
    <div
      class="flex shrink-0 items-center gap-2"
      data-slot="page-actions"
    >
      <slot name="actions" />
    </div>
  </div>
</template>
