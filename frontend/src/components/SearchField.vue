<script setup>
// Plan 2026-10-17-001 U3 (R17). The one search input. Every search on the
// portal already shows its label ("Search", "Find a report", "Find a
// person"), so this one does too -- a placeholder is a hint, never the
// name. 44px at every pointer, like the native inputs Reports and People &
// access use. Attributes (`maxlength`, `data-testid`, `autocomplete`) land
// on the input; lay it out with a wrapper.
import { useId } from 'vue'

defineOptions({ inheritAttrs: false })
defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, default: 'Search' },
  placeholder: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])
const id = useId()
</script>

<template>
  <div>
    <label
      :for="id"
      class="mb-1 block text-sm text-ink-gray-7"
    >{{ label }}</label>
    <input
      :id="id"
      v-bind="$attrs"
      :value="modelValue"
      type="search"
      :placeholder="placeholder"
      class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
      @input="emit('update:modelValue', $event.target.value)"
    >
  </div>
</template>
