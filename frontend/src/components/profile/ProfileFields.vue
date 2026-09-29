<script setup>
import { computed } from 'vue'
import { formatDate, isCalendarDate, today } from '@/lib/dates'
import { NOT_RECORDED, displayValue, spokenValue } from '@/lib/profileCorrection'

// Plan 2026-09-29-001 U5. One Profile tab's fields, as label/value rows.
//
// `fields` is the server's section (`get_my_profile`): label, value (already
// masked where it must be), `masked`, `editable`. `groups` optionally splits
// a long tab into headed runs; a field no group names renders in a trailing
// untitled run so nothing the server sent is dropped. Editable fields are
// skipped here -- the Contact tab edits them in its own form.
const props = defineProps({
  fields: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] },
  /** False when the correction category is retired: no per-row button. */
  canCorrect: { type: Boolean, default: true },
})
const emit = defineEmits(['correct'])

const shown = computed(() => props.fields.filter((field) => !field.editable))

const runs = computed(() => {
  const byName = Object.fromEntries(shown.value.map((field) => [field.fieldname, field]))
  const claimed = new Set()
  const out = []
  for (const group of props.groups) {
    const members = group.fields.map((name) => byName[name]).filter(Boolean)
    members.forEach((field) => claimed.add(field.fieldname))
    if (members.length) out.push({ label: group.label, fields: members })
  }
  const rest = shown.value.filter((field) => !claimed.has(field.fieldname))
  if (rest.length) out.push({ label: '', fields: rest })
  return out
})

function display(field) {
  return displayValue(field.value, (value) => (isCalendarDate(value) ? formatDate(value) : String(value)))
}

// "Valid until" in the past is worth saying out loud: an expired passport on
// record is exactly the kind of thing an employee needs to tell HR about.
function isExpired(field) {
  return field.fieldname === 'valid_upto' && isCalendarDate(field.value) && field.value < today()
}

function correct(field) {
  emit('correct', { label: field.label, display: display(field), masked: field.masked })
}
</script>

<template>
  <div class="space-y-5">
    <section
      v-for="run in runs"
      :key="run.label || 'rest'"
      :aria-label="run.label || undefined"
    >
      <h3
        v-if="run.label"
        class="label mb-2"
      >
        {{ run.label }}
      </h3>
      <div class="surface-card elev-1 divide-y divide-outline-gray-1">
        <div
          v-for="field in run.fields"
          :key="field.fieldname"
          :data-testid="`profile-field-${field.fieldname}`"
          class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-3"
        >
          <span class="text-sm text-ink-gray-6">{{ field.label }}</span>
          <span class="flex min-w-0 items-center gap-3">
            <span
              class="min-w-0 break-words text-right"
              :class="display(field) === NOT_RECORDED ? 'text-ink-gray-5' : 'text-ink-gray-9'"
              :aria-label="field.masked ? spokenValue(display(field)) : undefined"
            >
              {{ display(field) }}
            </span>
            <span
              v-if="isExpired(field)"
              class="rounded-full bg-surface-red-2 px-2 py-0.5 text-xs font-medium text-ink-red-4"
            >Expired</span>
            <!-- Always visible, never hover-only: the correction path has to
                 work on a phone and from the keyboard. -->
            <button
              v-if="canCorrect"
              type="button"
              class="-my-2 inline-flex min-h-11 shrink-0 cursor-pointer items-center text-sm font-medium text-blue-700 underline decoration-dotted underline-offset-4"
              :aria-label="`Request a correction to ${field.label}`"
              @click="correct(field)"
            >
              Request a correction
            </button>
          </span>
        </div>
      </div>
    </section>
  </div>
</template>
