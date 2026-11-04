<script setup>
// Plan 2026-11-03-001 U4 / R8, A12. The sheet's "Pick people" checklist: the
// manager's direct reports, from `get_roster_week` (team mode), which pages at
// 50 -- so a bigger team is searched and paged from the server, never taken
// from the rows the grid happens to show. Nobody already waiting for HR, and
// never the manager, is offered; `add_roster_ops_bulk` skips the same people
// if this is bypassed. Picks survive a new search.
import { computed, ref, watch } from 'vue'
import SearchField from '@/components/SearchField.vue'
import { call } from '@/lib/api'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'

const props = defineProps({
  /** Any date in a week; only who is in the team matters here. */
  weekStart: { type: String, required: true },
  /** The manager's own employee id, never offered. */
  me: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
/** The picked employee ids. */
const picked = defineModel({ type: Array, default: () => [] })

const query = ref('')
const people = ref([])
const total = ref(0)
const loadingMore = ref(false)
const failed = ref('')

async function page(text, start) {
  const data = await call('helixhr.api.get_roster_week', {
    week_start: props.weekStart,
    mode: 'team',
    search: text || undefined,
    start,
  })
  return { rows: data?.rows || [], total: data?.total || 0 }
}

const { search, results, loading, error } = useDebouncedSearch((text) => page(text, 0))
watch(query, (text) => search(text.trim()))
search('', 0)
watch(results, (value) => {
  if (!value) return
  people.value = value.rows
  total.value = value.total
})
watch(error, (value) => {
  failed.value = value ? toPlainMessage(value?.messages?.[0] ?? value?.message) || FALLBACK_ERROR : ''
})

async function more() {
  loadingMore.value = true
  try {
    const next = await page(query.value.trim(), people.value.length)
    people.value = [...people.value, ...next.rows]
    total.value = next.total
  } catch (e) {
    failed.value = toPlainMessage(e?.messages?.[0] ?? e?.message) || FALLBACK_ERROR
  } finally {
    loadingMore.value = false
  }
}

// A12: "Select all shown" stops at the loaded page; point a bigger team to
// "Whole team" instead. Not while searching -- the matches are the point then.
const partial = computed(() => !query.value.trim() && total.value > people.value.length)

const offered = computed(() => people.value.filter((row) => row.employee !== props.me && !row.pending_request))
const pickedSet = computed(() => new Set(picked.value))
const allPicked = computed(
  () => offered.value.length > 0 && offered.value.every((row) => pickedSet.value.has(row.employee)),
)

function toggle(employee, on) {
  const next = new Set(picked.value)
  if (on) next.add(employee)
  else next.delete(employee)
  picked.value = [...next]
}

/** Select all: everyone this list shows (the search's matches, loaded so far). */
function toggleAll(on) {
  const next = new Set(picked.value)
  for (const row of offered.value) {
    if (on) next.add(row.employee)
    else next.delete(row.employee)
  }
  picked.value = [...next]
}
</script>

<template>
  <div
    class="space-y-2"
    data-testid="roster-people-picker"
  >
    <SearchField
      v-model="query"
      label="Find a report"
      placeholder="Name or employee ID"
      :disabled="disabled"
    />
    <div class="flex items-center justify-between gap-2 text-sm">
      <label class="inline-flex min-h-11 cursor-pointer items-center gap-2 text-ink-gray-8">
        <input
          type="checkbox"
          class="size-5 shrink-0"
          :checked="allPicked"
          :disabled="disabled || !offered.length"
          data-testid="roster-pick-all"
          @change="toggleAll($event.target.checked)"
        >
        Select all shown
      </label>
      <span
        class="tabular text-ink-gray-7"
        role="status"
        aria-live="polite"
        data-testid="roster-pick-count"
      >{{ picked.length }} selected</span>
    </div>
    <p
      v-if="partial"
      class="text-sm text-ink-gray-6"
      data-testid="roster-pick-whole-team-hint"
    >
      Showing {{ people.length }} of {{ total }}. To change everyone, choose Whole team above.
    </p>
    <p
      v-if="loading && !people.length"
      class="text-sm text-ink-gray-6"
    >
      Loading your team…
    </p>
    <p
      v-else-if="!offered.length"
      class="text-sm text-ink-gray-6"
    >
      {{ query ? 'Nobody matches that search.' : 'Nobody in your team can take a change now.' }}
    </p>
    <ul
      v-else
      class="max-h-56 space-y-0.5 overflow-y-auto rounded border border-outline-gray-2 p-1"
      :aria-busy="loading ? 'true' : 'false'"
    >
      <li
        v-for="row in offered"
        :key="row.employee"
      >
        <label class="flex min-h-11 cursor-pointer items-center gap-2 rounded px-2 text-sm text-ink-gray-8 hover:bg-surface-gray-2">
          <input
            type="checkbox"
            class="size-5 shrink-0"
            :checked="pickedSet.has(row.employee)"
            :disabled="disabled"
            @change="toggle(row.employee, $event.target.checked)"
          >
          <span class="min-w-0 flex-1 break-words">{{ row.employee_name }}</span>
        </label>
      </li>
    </ul>
    <button
      v-if="people.length < total"
      type="button"
      class="inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-ink-blue-link underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      :disabled="disabled || loadingMore"
      @click="more"
    >
      {{ loadingMore ? 'Loading…' : `Show more (${total - people.length} more)` }}
    </button>
    <p
      v-if="failed"
      class="text-sm text-ink-red-4"
      role="alert"
    >
      {{ failed }}
    </p>
  </div>
</template>
