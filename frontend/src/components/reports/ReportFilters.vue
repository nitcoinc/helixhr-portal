<script setup>
import { computed, ref, useId } from 'vue'
import { Button } from 'frappe-ui'
import EntityPicker from '@/components/reports/EntityPicker.vue'
import { DATE_PRESETS, matchPreset, presetRange } from '@/lib/datePresets'
import { today } from '@/lib/dates'
import { call } from '@/lib/api'

// Plan 2026-10-04-001 U3 (resolved decision 14): the filter bar, built from
// the catalog entry's own filter specs. Nothing here runs a report: Run is
// explicit, and edits after a run only mark the results stale. On a phone
// the bar folds into a disclosure panel.
const props = defineProps({
  entry: { type: Object, required: true },
  /** `{ [filter name]: value }`. */
  modelValue: { type: Object, required: true },
  groupBy: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'update:groupBy', 'run'])

const ENTITY_TYPES = new Set(['employee', 'project', 'task', 'department', 'select_link'])
const FIELD_CLASS =
  'min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8 sm:min-h-9'
const LABEL_CLASS = 'mb-1 block text-sm text-ink-gray-7'

const id = useId()
const panelOpen = ref(false)

const hasRange = computed(
  () =>
    props.entry.filters.some((f) => f.name === 'from_date') && props.entry.filters.some((f) => f.name === 'to_date'),
)
const otherFilters = computed(() =>
  props.entry.filters.filter((f) => !(hasRange.value && (f.name === 'from_date' || f.name === 'to_date'))),
)

function set(name, value) {
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}

// Plan 2026-10-05-001 U7 (R12): a new Project clears a chosen Task that
// does not belong to it. The server answers "is this task in that project"
// with the same scoped lookup the picker uses.
async function setProject(value) {
  const task = props.modelValue.task
  set('project', value)
  if (!value || !task || !props.entry.filters.some((f) => f.name === 'task')) return
  let keep = false
  try {
    const found = await call('helixhr.api.search_report_options', {
      report_key: props.entry.key,
      filter: 'task',
      value: task,
      context: JSON.stringify({ project: value }),
    })
    keep = !!found?.length
  } catch {
    // Unknown -> clear; a stale task would only narrow the report to nothing.
  }
  if (!keep && props.modelValue.project === value && props.modelValue.task === task) set('task', '')
}

const preset = computed(() =>
  matchPreset(props.modelValue.from_date, props.modelValue.to_date, today()),
)

function applyPreset(presetId) {
  const range = presetRange(presetId, today())
  if (range) emit('update:modelValue', { ...props.modelValue, ...range })
}

const missing = computed(() =>
  props.entry.filters.filter((f) => f.reqd && (props.modelValue[f.name] ?? '') === '').map((f) => f.label),
)

const activeCount = computed(
  () => props.entry.filters.filter((f) => !['', null, undefined, 0].includes(props.modelValue[f.name])).length,
)

function setGroup(level, field) {
  const next = [...props.groupBy]
  if (!field) next.splice(level)
  else next[level] = field
  // The second level may not repeat the first.
  emit('update:groupBy', next[0] && next[1] === next[0] ? [next[0]] : next.filter(Boolean))
}

function secondGroupOptions() {
  return props.entry.group_by.filter((group) => group.field !== props.groupBy[0])
}
</script>

<template>
  <section
    class="surface-card elev-1 mb-4 p-4"
    aria-label="Report filters"
  >
    <button
      type="button"
      class="flex min-h-11 w-full cursor-pointer items-center justify-between text-sm font-medium text-ink-gray-9 sm:hidden"
      :aria-expanded="panelOpen ? 'true' : 'false'"
      :aria-controls="`${id}-panel`"
      @click="panelOpen = !panelOpen"
    >
      Filters<span v-if="activeCount"> ({{ activeCount }})</span>
      <span aria-hidden="true">{{ panelOpen ? '−' : '+' }}</span>
    </button>

    <div
      :id="`${id}-panel`"
      :class="panelOpen ? 'block' : 'hidden sm:block'"
    >
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <template v-if="hasRange">
          <div>
            <label
              :for="`${id}-preset`"
              :class="LABEL_CLASS"
            >Period</label>
            <select
              :id="`${id}-preset`"
              :value="preset"
              :class="FIELD_CLASS"
              @change="applyPreset($event.target.value)"
            >
              <option
                v-for="option in DATE_PRESETS"
                :key="option.id"
                :value="option.id"
              >
                {{ option.label }}
              </option>
              <option
                value="custom"
                disabled
              >
                Custom
              </option>
            </select>
          </div>
          <div
            v-for="name in ['from_date', 'to_date']"
            :key="name"
          >
            <label
              :for="`${id}-${name}`"
              :class="LABEL_CLASS"
            >{{ name === 'from_date' ? 'From' : 'To' }}</label>
            <input
              :id="`${id}-${name}`"
              type="date"
              :value="modelValue[name] || ''"
              :class="FIELD_CLASS"
              @change="set(name, $event.target.value)"
            >
          </div>
        </template>

        <template
          v-for="filter in otherFilters"
          :key="filter.name"
        >
          <EntityPicker
            v-if="ENTITY_TYPES.has(filter.type)"
            :report-key="entry.key"
            :filter="filter"
            :context="filter.type === 'task' && modelValue.project ? { project: modelValue.project } : null"
            :model-value="modelValue[filter.name] || ''"
            @update:model-value="filter.name === 'project' ? setProject($event) : set(filter.name, $event)"
          />
          <div
            v-else-if="filter.type === 'toggle'"
            class="flex items-end"
          >
            <label class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8 sm:min-h-9">
              <input
                type="checkbox"
                class="size-4"
                :checked="!!modelValue[filter.name]"
                @change="set(filter.name, $event.target.checked ? 1 : 0)"
              >
              {{ filter.label }}
            </label>
          </div>
          <div v-else>
            <label
              :for="`${id}-${filter.name}`"
              :class="LABEL_CLASS"
            >{{ filter.label }}</label>
            <select
              v-if="filter.type === 'select'"
              :id="`${id}-${filter.name}`"
              :value="modelValue[filter.name] || ''"
              :class="FIELD_CLASS"
              @change="set(filter.name, $event.target.value)"
            >
              <option
                v-if="!filter.reqd"
                value=""
              >
                Any
              </option>
              <option
                v-for="option in filter.options"
                :key="option"
                :value="option"
              >
                {{ option }}
              </option>
            </select>
            <input
              v-else
              :id="`${id}-${filter.name}`"
              :type="filter.type === 'month' ? 'month' : 'date'"
              :value="modelValue[filter.name] || ''"
              :class="FIELD_CLASS"
              @change="set(filter.name, $event.target.value)"
            >
          </div>
        </template>

        <template v-if="entry.group_by.length">
          <div>
            <label
              :for="`${id}-group-0`"
              :class="LABEL_CLASS"
            >Group by</label>
            <select
              :id="`${id}-group-0`"
              :value="groupBy[0] || ''"
              :class="FIELD_CLASS"
              @change="setGroup(0, $event.target.value)"
            >
              <option value="">
                No grouping
              </option>
              <option
                v-for="group in entry.group_by"
                :key="group.field"
                :value="group.field"
              >
                {{ group.label }}
              </option>
            </select>
          </div>
          <div>
            <label
              :for="`${id}-group-1`"
              :class="LABEL_CLASS"
            >Then by</label>
            <select
              :id="`${id}-group-1`"
              :value="groupBy[1] || ''"
              :disabled="!groupBy[0]"
              :class="FIELD_CLASS"
              @change="setGroup(1, $event.target.value)"
            >
              <option value="">
                {{ groupBy[0] ? 'Nothing else' : 'Choose a first group' }}
              </option>
              <option
                v-for="group in secondGroupOptions()"
                :key="group.field"
                :value="group.field"
              >
                {{ group.label }}
              </option>
            </select>
          </div>
        </template>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-3">
        <Button
          variant="solid"
          theme="blue"
          size="md"
          :loading="running"
          :disabled="missing.length > 0"
          :aria-describedby="missing.length ? `${id}-missing` : undefined"
          @click="emit('run')"
        >
          Run report
        </Button>
        <p
          v-if="missing.length"
          :id="`${id}-missing`"
          class="text-sm text-ink-gray-6"
        >
          Choose {{ missing.join(', ') }} to run this report.
        </p>
        <slot name="actions" />
      </div>
    </div>
  </section>
</template>
