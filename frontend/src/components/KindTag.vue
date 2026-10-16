<script setup>
import { computed } from 'vue'
import Icon from '@/components/Icon.vue'
import { getKindTag } from '@/lib/workKinds'

const props = defineProps({
  kind: { type: String, default: '' },
  routedToRole: { type: String, default: '' },
  row: { type: Object, default: null },
})

const tag = computed(() => {
  if (props.row) {
    return getKindTag(props.row)
  }
  return getKindTag(props.kind, props.routedToRole)
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium bg-surface-gray-2 text-ink-gray-7 whitespace-nowrap"
  >
    <Icon
      :name="tag.icon"
      size="h-3.5 w-3.5"
    />
    <span>{{ tag.label }}</span>
  </span>
</template>
