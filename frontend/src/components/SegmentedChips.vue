<script setup>
// Plan 2026-10-17-001 U3 (R17). The one filter-chip row: toggle buttons with
// `aria-pressed` inside a named group, the style Requests' work filter
// already uses. One line that scrolls sideways on a phone rather than
// wrapping into a second row; 44px tall at every pointer. Nine pages carry
// their own copy today -- U15 moves them here.
defineProps({
  /** `{ value, label, count?, testid? }`. `value` may be null (an "All"
   * chip); `testid` lands on that chip's button. */
  options: { type: Array, required: true },
  modelValue: { type: [String, Number, Boolean], default: null },
  /** The group's accessible name, e.g. "Filter work by state". */
  label: { type: String, required: true },
  /** Every chip off at once -- a filter with nothing to filter (U17). Say
   * why next to it, and point `aria-describedby` at that sentence. */
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <div
    class="flex gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    role="group"
    :aria-label="label"
  >
    <button
      v-for="option in options"
      :key="String(option.value)"
      type="button"
      class="min-h-11 shrink-0 cursor-pointer whitespace-nowrap rounded-full border px-4 text-sm font-medium focus-visible:[outline-offset:-3px] disabled:cursor-not-allowed disabled:opacity-50"
      :class="
        modelValue === option.value
          ? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
          : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2'
      "
      :aria-pressed="modelValue === option.value ? 'true' : 'false'"
      :disabled="disabled"
      :data-testid="option.testid"
      :aria-label="option.count != null ? `${option.label} (${option.count})` : undefined"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.label }}
      <span
        v-if="option.count != null"
        class="tabular ml-1 text-ink-gray-5"
      >{{ option.count }}</span>
    </button>
  </div>
</template>
