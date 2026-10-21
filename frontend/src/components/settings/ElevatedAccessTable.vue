<script setup>
import { computed, ref } from 'vue'
import { formatDate } from '@/lib/dates'
import { TONE } from '@/lib/statusBadge'
import { warningLabel } from '@/lib/accessProfiles'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'

// Plan 2026-10-19-001 U7 (R17-R20): everyone in the caller's reach with
// elevated access, as `get_elevated_access` sorts them -- problems first,
// then All orgs and Manage access, then by name. The name filter runs on the
// loaded rows (the server sends them all); a table at md and up, cards on a
// phone or beside an open person.
const props = defineProps({
  people: { type: Array, required: true },
  /** 'all' | 'attention' -- the page's banner sets it. */
  filter: { type: String, default: 'all' },
  selected: { type: String, default: '' },
  /** Cards at every width: the editor is open beside the list. */
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['update:filter', 'open'])

const name = ref('')
const role = ref('')
const region = ref(null)

function problem(person) {
  return person.warnings.some((warning) => warning.kind === 'problem')
}
const attentionCount = computed(() => props.people.filter(problem).length)

const FILTERS = computed(() => [
  { value: 'all', label: 'All', testid: 'elevated-filter-all' },
  { value: 'attention', label: 'Needs attention', count: attentionCount.value, testid: 'elevated-filter-attention' },
])

const roleOptions = computed(() => {
  const seen = new Map()
  for (const person of props.people) {
    if (person.system_manager) seen.set('System Manager', 'System Manager')
    for (const entry of person.roles) seen.set(entry.role, entry.label)
  }
  return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label))
})

const shown = computed(() => {
  const needle = name.value.trim().toLowerCase()
  return props.people.filter((person) => {
    if (props.filter === 'attention' && !problem(person)) return false
    if (role.value === 'System Manager' && !person.system_manager) return false
    if (role.value && role.value !== 'System Manager' && !person.roles.some((entry) => entry.role === role.value)) {
      return false
    }
    if (!needle) return true
    return [person.full_name, person.employee_name, person.user, person.employee]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(needle))
  })
})

/** The start of "HR Manager · Org A, Org B"; the orgs (and the filled All
 * orgs badge) follow it. A role held in Frappe with no row has no orgs. */
function roleLead(entry) {
  return entry.assigned ? `${entry.label} ·` : `${entry.label} (no assignment)`
}

function warningTone(kind) {
  return kind === 'problem' ? TONE.sentBack : TONE.resting
}

/** Moves focus to the table region: the banner's second half. */
function focus() {
  region.value?.focus()
}
defineExpose({ focus })
</script>

<template>
  <div>
    <div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
      <SearchField
        v-model="name"
        label="Filter by name"
        placeholder="Name, email or employee ID"
        autocomplete="off"
        data-testid="elevated-name-filter"
      />
      <label>
        <span class="mb-1 block text-sm text-ink-gray-7">Role</span>
        <select
          v-model="role"
          class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 text-sm text-ink-gray-8"
          data-testid="elevated-role-filter"
        >
          <option value="">Any role</option>
          <option
            v-for="option in roleOptions"
            :key="option.value"
            :value="option.value"
          >{{ option.label }}</option>
        </select>
      </label>
    </div>
    <SegmentedChips
      class="mt-3"
      :model-value="filter"
      :options="FILTERS"
      label="Show"
      @update:model-value="emit('update:filter', $event)"
    />

    <div
      ref="region"
      tabindex="-1"
      class="mt-3 focus:outline-none"
      aria-label="Elevated access list"
      role="region"
      data-testid="elevated-region"
    >
      <p
        v-if="!shown.length"
        class="surface-inset p-3 text-sm text-ink-gray-7"
        data-testid="elevated-no-match"
      >
        No one matches these filters.
      </p>

      <!-- Wide: a table. Each person's name is the button that opens them. -->
      <div
        v-if="shown.length && !compact"
        class="surface-card hidden overflow-x-auto md:block"
      >
        <table
          class="w-full text-left text-sm"
          data-testid="elevated-table"
        >
          <thead class="text-ink-gray-6">
            <tr class="border-b border-outline-gray-1">
              <th
                scope="col"
                class="px-3 py-2 font-medium"
              >
                Person
              </th>
              <th
                scope="col"
                class="px-3 py-2 font-medium"
              >
                Own org
              </th>
              <th
                scope="col"
                class="px-3 py-2 font-medium"
              >
                Access
              </th>
              <th
                scope="col"
                class="px-3 py-2 font-medium"
              >
                Warnings
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-outline-gray-1">
            <tr
              v-for="person in shown"
              :key="person.user"
              class="align-top"
              :class="selected === person.user ? 'bg-surface-gray-2' : ''"
              :data-testid="`elevated-row-${person.user}`"
            >
              <th
                scope="row"
                class="px-3 py-1 font-normal"
              >
                <button
                  type="button"
                  class="-mx-1 flex min-h-11 cursor-pointer flex-col items-start justify-center rounded px-1 text-left hover:underline"
                  :aria-current="selected === person.user ? 'true' : undefined"
                  :data-person-open="person.user"
                  :data-testid="`elevated-open-${person.user}`"
                  @click="emit('open', person.user)"
                >
                  <span class="font-medium text-ink-gray-9">{{ person.full_name }}</span>
                  <span
                    v-if="person.last_change"
                    class="text-xs text-ink-gray-6"
                  >Changed {{ formatDate(person.last_change.at) }} by {{ person.last_change.by_name }}</span>
                </button>
              </th>
              <td class="px-3 py-3 text-ink-gray-7">
                {{ person.own_company || '—' }}
              </td>
              <td class="px-3 py-2">
                <ul class="flex flex-wrap gap-1.5">
                  <li
                    v-if="person.system_manager"
                    class="rounded-full border border-outline-gray-2 px-2 py-0.5 text-xs text-ink-gray-8"
                  >
                    System Manager · unscoped
                  </li>
                  <li
                    v-for="entry in person.roles"
                    :key="entry.role"
                    class="inline-flex items-center gap-1 rounded-full border border-outline-gray-2 px-2 py-0.5 text-xs text-ink-gray-8"
                  >
                    {{ roleLead(entry) }}
                    <span
                      v-if="entry.all_orgs"
                      class="rounded-full bg-heading px-1.5 text-white"
                    >All orgs</span>
                    {{ entry.companies.join(', ') }}
                  </li>
                </ul>
              </td>
              <td class="px-3 py-2">
                <ul
                  v-if="person.warnings.length"
                  class="flex flex-wrap gap-1.5"
                >
                  <li
                    v-for="warning in person.warnings"
                    :key="warning.key"
                    class="rounded-full px-2 py-0.5 text-xs font-medium"
                    :class="warningTone(warning.kind)"
                  >
                    {{ warningLabel(warning.key) }}
                  </li>
                </ul>
                <span
                  v-else
                  class="text-ink-gray-5"
                >—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Phone, or beside an open person: one card per person. -->
      <ul
        v-if="shown.length"
        class="space-y-2"
        :class="compact ? '' : 'md:hidden'"
        data-testid="elevated-cards"
      >
        <li
          v-for="person in shown"
          :key="person.user"
          class="surface-card relative p-3"
          :class="selected === person.user ? 'ring-2 ring-outline-gray-3' : ''"
          :data-testid="`elevated-card-${person.user}`"
        >
          <button
            type="button"
            class="-my-2 inline-flex min-h-11 cursor-pointer items-center text-left font-medium text-ink-gray-9 after:absolute after:inset-0"
            :aria-current="selected === person.user ? 'true' : undefined"
            :data-person-open="person.user"
            @click="emit('open', person.user)"
          >
            {{ person.full_name }}
          </button>
          <p class="text-xs text-ink-gray-6">
            {{ person.own_company || 'No employee record' }}
          </p>
          <ul class="mt-2 flex flex-wrap gap-1.5">
            <li
              v-if="person.system_manager"
              class="rounded-full border border-outline-gray-2 px-2 py-0.5 text-xs text-ink-gray-8"
            >
              System Manager · unscoped
            </li>
            <li
              v-for="entry in person.roles"
              :key="entry.role"
              class="inline-flex items-center gap-1 rounded-full border border-outline-gray-2 px-2 py-0.5 text-xs text-ink-gray-8"
            >
              {{ roleLead(entry) }}
              <span
                v-if="entry.all_orgs"
                class="rounded-full bg-heading px-1.5 text-white"
              >All orgs</span>
              {{ entry.companies.join(', ') }}
            </li>
          </ul>
          <ul
            v-if="person.warnings.length"
            class="mt-2 flex flex-wrap gap-1.5"
          >
            <li
              v-for="warning in person.warnings"
              :key="warning.key"
              class="rounded-full px-2 py-0.5 text-xs font-medium"
              :class="warningTone(warning.kind)"
            >
              {{ warningLabel(warning.key) }}
            </li>
          </ul>
        </li>
      </ul>
    </div>
  </div>
</template>
