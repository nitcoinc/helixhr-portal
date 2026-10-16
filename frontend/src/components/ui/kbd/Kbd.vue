<script setup>
import { cn } from '@/lib/utils'

const props = defineProps({
  class: {
    type: [Boolean, null, String, Object, Array],
    required: false,
    skipCheck: true,
  },
})
</script>

<!-- Upstream's third class line (Kbd inside a shadcn tooltip) is dropped: it
     keys on `data-slot=tooltip-content`, which nothing here renders, and its
     20% / 10% opacity steps on `background` generate no CSS against Signal's
     hex variables (KTD4). -->
<template>
  <kbd
    :class="
      cn(
        'bg-muted text-muted-foreground pointer-events-none inline-flex h-5 w-fit min-w-5 items-center justify-center gap-1 rounded-sm px-1 font-sans text-xs font-medium select-none',
        '[&_svg:not([class*=\'size-\'])]:size-3',
        props.class,
      )
    "
  >
    <slot />
  </kbd>
</template>
