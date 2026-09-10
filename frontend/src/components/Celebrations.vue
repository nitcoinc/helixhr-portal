<script setup>
import { computed, ref } from 'vue'
import { formatDayMonth } from '@/lib/dates'

// P4-U5 / P4-R14. Who is celebrating this month, in the caller's own company.
//
// The server hands this band a projection and nothing else: a name, a
// monogram, a day, a month, whether that day is today, and for an anniversary
// the years completed (P4-KTD14). There is no birth year and no age on the
// wire, so there is none to leak here either -- `formatDayMonth` exists
// because there is no date string to format.
//
// One card per group, side by side, not one card holding both. The reason is
// geometry: a single card grew without limit with the size of the company, and
// a tall card in a column beside a short one opens dead space next to it. Five
// rows plus an in-place disclosure keeps the page the same height whether one
// person is celebrating or forty.
const props = defineProps({
  birthdays: { type: Array, default: () => [] },
  anniversaries: { type: Array, default: () => [] },
})

/** Rows shown before the disclosure appears. The server sorts today-first and
 * then by day, so the five kept are the five most relevant, not an arbitrary
 * five. */
const VISIBLE_ROWS = 5

const groups = computed(() =>
  [
    { key: 'birthdays', label: 'Birthdays', people: props.birthdays },
    { key: 'anniversaries', label: 'Work anniversaries', people: props.anniversaries },
  ].filter((group) => group.people.length),
)

// A group with nobody in it renders no card at all. The alternative -- an
// empty slot or a card saying "nobody has a birthday" -- is either a hole in
// the band or noise, and the month-level version of that rule already exists:
// the whole band is hidden when neither group has anyone.
const onlyOneGroup = computed(() => groups.value.length === 1)

const expanded = ref({})

function rowsFor(group) {
  return expanded.value[group.key] ? group.people : group.people.slice(0, VISIBLE_ROWS)
}

/** "9 Sep · 3 years" — the years only where there are years to report. */
function when(person) {
  const day = formatDayMonth(person.month, person.day)
  if (!person.years) return day
  return `${day} · ${person.years} ${person.years === 1 ? 'year' : 'years'}`
}
</script>

<template>
  <section
    aria-labelledby="celebrations-heading"
    class="space-y-2"
  >
    <h2
      id="celebrations-heading"
      class="font-heading text-base font-semibold text-ink-gray-9"
    >
      Celebrating this month
    </h2>

    <div class="grid grid-cols-1 gap-2 lg:grid-cols-2">
      <section
        v-for="group in groups"
        :key="group.key"
        class="surface-card elev-1 p-4"
        :class="onlyOneGroup ? 'lg:col-span-2' : ''"
        :aria-labelledby="`celebrations-${group.key}`"
      >
        <h3
          :id="`celebrations-${group.key}`"
          class="label"
        >
          {{ group.label }}
        </h3>

        <!-- The monogram is the server's, the same two letters the directory
             and the Approvals queue draw. No photos: a face is personal data
             this card does not need (P3-R22). No row is clickable -- there is
             no detail view behind a name, and a run of rows that look like
             controls and do nothing is a run of dead tab stops. -->
        <ul
          :id="`celebrations-${group.key}-list`"
          class="mt-3 space-y-2"
        >
          <li
            v-for="person in rowsFor(group)"
            :key="`${group.key}:${person.employee}`"
            class="flex items-center gap-3"
          >
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

        <!-- The one control in the band, and it is a real button because it
             does something: it reveals the rest of a group that is longer than
             the card, in place, with no round trip. Scroll rather than expand
             was the alternative and was rejected -- a scroll region inside a
             card is a nested-scroll trap on a phone, needs its own focus and
             label to be reachable by keyboard, and hides people with no signal
             that they exist. -->
        <button
          v-if="group.people.length > VISIBLE_ROWS"
          type="button"
          class="mt-3 -mb-1 inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-blue-700 hover:underline"
          :aria-expanded="expanded[group.key] ? 'true' : 'false'"
          :aria-controls="`celebrations-${group.key}-list`"
          @click="expanded[group.key] = !expanded[group.key]"
        >
          {{ expanded[group.key] ? 'Show fewer' : `Show all ${group.people.length}` }}
        </button>
      </section>
    </div>
  </section>
</template>
