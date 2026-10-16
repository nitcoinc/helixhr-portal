<script setup>
import { reactiveOmit } from '@vueuse/core'
import { ListboxFilter, useForwardProps } from 'reka-ui'
import Icon from '@/components/Icon.vue'
import { cn } from '@/lib/utils'
import { useCommand } from '.'

defineOptions({
  inheritAttrs: false,
})

const props = defineProps({
  modelValue: { type: String, required: false },
  autoFocus: { type: Boolean, required: false },
  disabled: { type: Boolean, required: false },
  asChild: { type: Boolean, required: false },
  as: { type: null, required: false },
  class: {
    type: [Boolean, null, String, Object, Array],
    required: false,
    skipCheck: true,
  },
})

const delegatedProps = reactiveOmit(props, 'class')

const forwardedProps = useForwardProps(delegatedProps)

const { filterState } = useCommand()
</script>

<template>
  <div
    class="flex items-center border-b px-3"
    cmdk-input-wrapper
  >
    <!-- Upstream draws lucide's Search at 50% opacity; here it is the
         lib/icons.js glyph in the muted ink, so the colour is a measured
         Signal step rather than a blend. -->
    <Icon
      name="search"
      size="h-4 w-4"
      class="mr-2 shrink-0 text-muted-foreground"
    />
    <ListboxFilter
      v-bind="{ ...forwardedProps, ...$attrs }"
      v-model="filterState.search"
      auto-focus
      :class="
        cn(
          'flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50',
          props.class,
        )
      "
    />
  </div>
</template>
