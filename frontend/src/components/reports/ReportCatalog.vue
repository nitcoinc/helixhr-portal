<script setup>
import { computed, ref } from 'vue'
import SearchField from '@/components/SearchField.vue'

// Plan 2026-10-04-001 U4: the /reports landing page -- every entry the
// server says this caller may run (`get_report_catalog`), in families,
// with a search over label and question. The page never decides access.
const props = defineProps({
  catalog: { type: Array, required: true },
  /** U11 (HR-16): catalog entries this viewer ran lately, newest first. */
  recent: { type: Array, default: () => [] },
  /** Query carried onto each report link (People.vue's `?employee=`). */
  carryQuery: { type: Object, default: () => ({}) },
})

const FAMILIES = [
  { id: 'time', label: 'Time' },
  { id: 'attendance', label: 'Attendance' },
  { id: 'leave', label: 'Leave' },
  { id: 'people', label: 'People' },
]

const search = ref('')
// The strip is for before you type; a search is the other way in.
const showRecent = computed(() => props.recent.length > 0 && !search.value.trim())

const matches = computed(() => {
  const needle = search.value.trim().toLowerCase()
  if (!needle) return props.catalog
  return props.catalog.filter((entry) => `${entry.label} ${entry.question}`.toLowerCase().includes(needle))
})

const families = computed(() =>
  FAMILIES.map((family) => ({
    ...family,
    entries: matches.value.filter((entry) => entry.family === family.id),
  })).filter((family) => family.entries.length),
)
</script>

<template>
  <div>
    <div class="mb-5 sm:max-w-md">
      <SearchField
        v-model="search"
        label="Find a report"
        placeholder="Search by name or question"
      />
    </div>

    <section
      v-if="showRecent"
      class="mb-6"
      aria-labelledby="report-recent-heading"
      data-testid="report-recent"
    >
      <h2
        id="report-recent-heading"
        class="label mb-2 text-ink-gray-6"
      >
        Recently run
      </h2>
      <ul class="flex flex-wrap gap-2">
        <li
          v-for="entry in recent"
          :key="entry.key"
        >
          <router-link
            :to="{ name: 'ReportView', params: { reportKey: entry.key }, query: carryQuery }"
            class="inline-flex min-h-11 items-center rounded-full border border-outline-gray-2 bg-surface-white px-4 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
          >
            {{ entry.label }}
          </router-link>
        </li>
      </ul>
    </section>

    <p
      class="sr-only"
      aria-live="polite"
    >
      {{ matches.length }} {{ matches.length === 1 ? 'report' : 'reports' }}
    </p>

    <div
      v-if="!matches.length"
      class="surface-inset p-4 text-sm text-ink-gray-7"
    >
      <p class="font-medium text-ink-gray-9">
        No report matches "{{ search.trim() }}"
      </p>
      <p class="mt-1">
        Try another word, or
        <button
          type="button"
          class="cursor-pointer text-blue-700 underline underline-offset-2"
          @click="search = ''"
        >
          clear the search
        </button>.
      </p>
    </div>

    <section
      v-for="family in families"
      :key="family.id"
      class="mb-6"
      :aria-labelledby="`report-family-${family.id}`"
    >
      <h2
        :id="`report-family-${family.id}`"
        class="label mb-2 text-ink-gray-6"
      >
        {{ family.label }}
      </h2>
      <ul class="space-y-2 lg:grid lg:grid-cols-2 lg:gap-3 lg:space-y-0">
        <li
          v-for="entry in family.entries"
          :key="entry.key"
        >
          <router-link
            :to="{ name: 'ReportView', params: { reportKey: entry.key }, query: carryQuery }"
            class="surface-card elev-1 flex h-full w-full flex-col items-start gap-1 p-4 text-left"
          >
            <span class="font-medium text-ink-gray-9">{{ entry.label }}</span>
            <span class="text-sm text-ink-gray-6">{{ entry.question }}</span>
          </router-link>
        </li>
      </ul>
    </section>
  </div>
</template>
