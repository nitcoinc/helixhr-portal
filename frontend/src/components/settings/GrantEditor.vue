<script setup>
// Plan 2026-10-15-001 U6: company checkboxes and export toggle for one
// person's grant. Extracted from CompanyAccessSection.vue so PeopleAccessSection
// can reuse it.
import { computed } from 'vue'

const props = defineProps({
  /** Companies the caller may grant. */
  grantable: { type: Array, required: true },
  /** Currently selected grant level, or null. */
  level: { type: String, default: null },
  /** Currently selected company names. */
  companies: { type: Array, default: () => [] },
  /** Whether export is allowed. */
  allowExport: { type: Boolean, default: false },
  /** Whether the grant fields are editable. */
  disabled: { type: Boolean, default: false },
  /** Whether to show the level selector. */
  showLevel: { type: Boolean, default: true },
})

const emit = defineEmits(['update:level', 'update:companies', 'update:allowExport'])

const LEVEL_OPTIONS = [
  { value: 'All data', hint: 'People, organisation, projects and reports.' },
  { value: 'Reports only', hint: 'Reports, nothing else.' },
]

const LABEL_CLASS = 'mb-1 block text-sm text-ink-gray-7'
const CHECK_CLASS = 'mr-1.5 inline-flex accent-signal-3'

const activeLevel = computed(() => props.level || null)

function setLevel(value) {
  emit('update:level', value)
}

function toggleCompany(name) {
  if (props.disabled) return
  const next = props.companies.includes(name)
    ? props.companies.filter((c) => c !== name)
    : [...props.companies, name]
  emit('update:companies', next)
}

function setExport(value) {
  emit('update:allowExport', value)
}
</script>

<template>
  <fieldset :disabled="disabled">
    <legend class="mb-2 text-sm font-medium text-ink-gray-9">
      Company access grant
    </legend>

    <div v-if="showLevel">
      <label
        :class="LABEL_CLASS"
      >Level</label>
      <div class="flex flex-col gap-2">
        <label
          v-for="opt in LEVEL_OPTIONS"
          :key="opt.value"
          class="flex items-center gap-2 text-sm"
        >
          <input
            type="radio"
            :checked="activeLevel === opt.value"
            :value="opt.value"
            :class="CHECK_CLASS"
            :disabled="disabled"
            @change="setLevel(opt.value)"
          >
          <span class="font-medium text-ink-gray-9">{{ opt.value }}</span>
          <span class="text-ink-gray-6">— {{ opt.hint }}</span>
        </label>
      </div>
    </div>

    <div class="mt-3">
      <label
        :class="LABEL_CLASS"
      >Companies</label>
      <div
        v-if="!grantable.length"
        class="text-sm text-ink-gray-6"
      >
        No companies available to grant.
      </div>
      <div
        v-for="name in grantable"
        :key="name"
        class="flex items-center gap-2 text-sm"
      >
        <input
          type="checkbox"
          :checked="companies.includes(name)"
          :class="CHECK_CLASS"
          :disabled="disabled"
          @change="toggleCompany(name)"
        >
        <label>{{ name }}</label>
      </div>
    </div>

    <div
      v-if="activeLevel"
      class="mt-3 flex items-center gap-2 text-sm"
    >
      <input
        type="checkbox"
        :checked="allowExport"
        :class="CHECK_CLASS"
        :disabled="disabled"
        @change="setExport($event.target.checked)"
      >
      <label>May export reports</label>
    </div>
  </fieldset>
</template>