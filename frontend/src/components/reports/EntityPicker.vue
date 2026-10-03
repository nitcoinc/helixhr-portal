<script setup>
import { onUnmounted, ref, useId, watch } from 'vue'
import { call } from '@/lib/api'

// Plan 2026-10-04-001 U3 (resolved decision 14): one scoped, single-select
// typeahead for every entity filter. Options come only from
// `search_report_options`, which resolves access for THIS report first, so
// the picker can never offer a value the report would refuse. ARIA 1.2
// combobox: the input owns a listbox popup; arrow keys move
// `aria-activedescendant`, Enter picks, Escape closes.
const props = defineProps({
  reportKey: { type: String, required: true },
  /** The catalog filter spec: `{name, type, label}`. */
  filter: { type: Object, required: true },
  modelValue: { type: String, default: '' },
  /** A dependent picker's parent values, e.g. `{ project }` for tasks. */
  context: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const MIN_QUERY = 2
const DEBOUNCE_MS = 250

const id = useId()
const listId = `${id}-list`
const text = ref('')
const selectedLabel = ref('')
const options = ref([])
const open = ref(false)
const active = ref(-1)
const loading = ref(false)
const failed = ref(false)
let timer = null
let seq = 0

async function fetchOptions(params) {
  return call('helixhr.api.search_report_options', {
    report_key: props.reportKey,
    filter: props.filter.name,
    context: props.context ? JSON.stringify(props.context) : undefined,
    ...params,
  })
}

// A value that arrived from the URL (or a saved view) shows its label, not
// its id. Out of scope resolves to nothing; the id stays visible and the
// server reports it in `filters_removed`.
watch(
  () => props.modelValue,
  async (value) => {
    if (!value) {
      selectedLabel.value = ''
      text.value = ''
      return
    }
    if (selectedLabel.value && text.value === selectedLabel.value) return
    text.value = value
    try {
      const [option] = await fetchOptions({ value })
      if (option && props.modelValue === value) {
        selectedLabel.value = option.label
        text.value = option.label
      }
    } catch {
      // The id is still shown; the report itself will say if it is refused.
    }
  },
  { immediate: true },
)

function onInput(event) {
  text.value = event.target.value
  open.value = true
  active.value = -1
  clearTimeout(timer)
  const needle = text.value.trim()
  if (needle.length < MIN_QUERY) {
    options.value = []
    loading.value = false
    return
  }
  loading.value = true
  timer = setTimeout(async () => {
    const mine = ++seq
    try {
      const result = await fetchOptions({ query: needle })
      if (mine !== seq) return
      options.value = result || []
      failed.value = false
    } catch {
      if (mine !== seq) return
      options.value = []
      failed.value = true
    } finally {
      if (mine === seq) loading.value = false
    }
  }, DEBOUNCE_MS)
}

function pick(option) {
  selectedLabel.value = option.label
  text.value = option.label
  open.value = false
  emit('update:modelValue', option.value)
}

function clear() {
  selectedLabel.value = ''
  text.value = ''
  options.value = []
  emit('update:modelValue', '')
}

function onKeydown(event) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    open.value = true
    if (options.value.length) active.value = (active.value + 1) % options.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (options.value.length) active.value = (active.value - 1 + options.value.length) % options.value.length
  } else if (event.key === 'Enter' && open.value && active.value >= 0) {
    event.preventDefault()
    pick(options.value[active.value])
  } else if (event.key === 'Escape') {
    open.value = false
  }
}

function onBlur() {
  // Let a mousedown on an option land first.
  setTimeout(() => {
    open.value = false
    text.value = props.modelValue ? selectedLabel.value || props.modelValue : ''
  }, 120)
}

onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <div class="relative">
    <label
      :for="id"
      class="mb-1 block text-sm text-ink-gray-7"
    >{{ filter.label }}</label>
    <div class="flex items-center gap-1">
      <input
        :id="id"
        :value="text"
        type="text"
        role="combobox"
        autocomplete="off"
        aria-autocomplete="list"
        :aria-expanded="open ? 'true' : 'false'"
        :aria-controls="listId"
        :aria-activedescendant="open && active >= 0 ? `${listId}-${active}` : undefined"
        :placeholder="`Type ${MIN_QUERY}+ letters`"
        class="min-h-11 w-full min-w-0 rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8 sm:min-h-9"
        @input="onInput"
        @keydown="onKeydown"
        @focus="open = text.trim().length >= MIN_QUERY"
        @blur="onBlur"
      >
      <button
        v-if="modelValue"
        type="button"
        class="inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded text-ink-gray-6 hover:bg-surface-gray-2 sm:size-9"
        :aria-label="`Clear ${filter.label}`"
        @click="clear"
      >
        &times;
      </button>
    </div>
    <ul
      v-show="open"
      :id="listId"
      role="listbox"
      :aria-label="filter.label"
      class="surface-card elev-2 absolute z-20 mt-1 max-h-72 w-full min-w-[16rem] overflow-y-auto p-1"
    >
      <li
        v-if="text.trim().length < MIN_QUERY"
        class="px-3 py-2 text-sm text-ink-gray-5"
      >
        Type at least {{ MIN_QUERY }} letters.
      </li>
      <li
        v-else-if="loading"
        class="px-3 py-2 text-sm text-ink-gray-5"
      >
        Searching…
      </li>
      <li
        v-else-if="failed"
        class="px-3 py-2 text-sm text-ink-gray-5"
      >
        Search didn't load. Keep typing to try again.
      </li>
      <li
        v-else-if="!options.length"
        class="px-3 py-2 text-sm text-ink-gray-5"
      >
        No match you can report on.
      </li>
      <li
        v-for="(option, index) in loading || failed ? [] : options"
        :id="`${listId}-${index}`"
        :key="option.value"
        role="option"
        :aria-selected="option.value === modelValue ? 'true' : 'false'"
        class="cursor-pointer rounded px-3 py-2 text-sm"
        :class="index === active ? 'bg-surface-gray-2' : ''"
        @mousedown.prevent="pick(option)"
        @mouseenter="active = index"
      >
        <span class="block text-ink-gray-9">{{ option.label }}</span>
        <span class="block text-xs text-ink-gray-5">{{ option.value }}<template v-if="option.description"> · {{ option.description }}</template></span>
      </li>
    </ul>
  </div>
</template>
