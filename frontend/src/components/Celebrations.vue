<script setup>
import { computed } from 'vue'
import { formatDayMonth } from '@/lib/dates'

// P4-U5 / P4-R14. Who is celebrating this month, in the caller's own company.
//
// The server hands this card a projection and nothing else: a name, a
// monogram, a day, a month, whether that day is today, and for an
// anniversary the years completed (P4-KTD14). There is no birth year and no
// age on the wire, so there is none to leak here either -- `formatDayMonth`
// exists because there is no date string to format.
//
// Nothing in the card is clickable. A birthday is something to read on the
// way past; there is no detail view behind a name, and a rail of rows that
// look like controls and do nothing is a rail of dead tab stops.
const props = defineProps({
  birthdays: { type: Array, default: () => [] },
  anniversaries: { type: Array, default: () => [] },
})

const groups = computed(() =>
  [
    { key: 'birthdays', label: 'Birthdays', people: props.birthdays },
    { key: 'anniversaries', label: 'Work anniversaries', people: props.anniversaries },
  ].filter((group) => group.people.length),
)

/** "9 Sep · 3 years" — the years only where there are years to report. */
function when(person) {
  const day = formatDayMonth(person.month, person.day)
  if (!person.years) return day
  return `${day} · ${person.years} ${person.years === 1 ? 'year' : 'years'}`
}
</script>

<template>
  <section
    class="surface-card elev-1 px-4 py-3"
    aria-labelledby="celebrations-heading"
  >
    <h2
      id="celebrations-heading"
      class="text-sm text-ink-gray-6"
    >
      Celebrating this month
    </h2>

    <div
      v-for="group in groups"
      :key="group.key"
      class="mt-3"
    >
      <p class="text-xs font-medium uppercase tracking-wide text-ink-gray-5">
        {{ group.label }}
      </p>
      <ul class="mt-2 space-y-2">
        <li
          v-for="person in group.people"
          :key="`${group.key}:${person.employee}`"
          class="flex items-center gap-3"
        >
          <!-- The monogram is the server's, the same two letters the
               directory and the Approvals queue draw. No photos: a face is
               personal data this card does not need (P3-R22). -->
          <span
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-xs font-bold text-ink-green-3"
            aria-hidden="true"
          >{{ person.initials }}</span>

          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-ink-gray-9">
              {{ person.employee_name }}
            </span>
            <span class="block truncate text-xs text-ink-gray-5">
              {{ when(person) }}
            </span>
          </span>

          <span
            v-if="person.is_today"
            class="shrink-0 rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
          >Today</span>
        </li>
      </ul>
    </div>
  </section>
</template>
