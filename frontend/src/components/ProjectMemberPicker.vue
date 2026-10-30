<script setup>
import { computed, ref, watch } from 'vue'
import { Button, Dialog } from 'frappe-ui'
import Avatar from '@/components/Avatar.vue'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import { call } from '@/lib/api'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'

// Plan 2026-10-31-001 U6 (R5-R7). Add several people to a project in one
// `add_project_members` call: picked from a search, or a whole department.
// The call appends and never removes; who it skipped, and why, stays on
// screen until dismissed. frappe-ui's Dialog (reka-ui) supplies the focus
// trap, Escape and focus return to the button that opened it.
//
// People come from `get_directory`, not `search_people`: a Delivery Manager
// has no admin scope, and `get_directory` is the caller's own company with
// no admin permission required (the same reason the old single-add used it).
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  project: { type: String, required: true },
  /** `get_project.departments`: `{ name, department_name, company }`. */
  departments: { type: Array, default: () => [] },
  /** Employee ids already on the project, shown as such and not pickable. */
  memberIds: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'added'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

// `get_directory` ignores a query under 2 characters (`_DIRECTORY_QUERY_MIN`)
// and would return an unfiltered page, which must never read as matches.
const QUERY_MIN = 2
const RESULTS_LIMIT = 20
const MODES = [
  { value: 'people', label: 'People' },
  { value: 'department', label: 'Department' },
]

const mode = ref('people')
const query = ref('')
const selected = ref([])
const department = ref('')
const sending = ref(false)
const error = ref('')
const result = ref(null)

const {
  search,
  cancel,
  results,
  loading: searching,
  error: searchError,
} = useDebouncedSearch(
  async (text) => (await call('helixhr.api.get_directory', { query: text, limit: RESULTS_LIMIT }))?.people || [],
  { initial: [] },
)

const searched = computed(() => query.value.trim().length >= QUERY_MIN)
watch(query, (value) => {
  const text = value.trim()
  if (text.length < QUERY_MIN) {
    cancel()
    results.value = []
    return
  }
  search(text)
})

watch(
  () => props.modelValue,
  (isOpen) => {
    if (!isOpen) return
    mode.value = 'people'
    query.value = ''
    selected.value = []
    department.value = ''
    error.value = ''
    result.value = null
  },
)

const memberSet = computed(() => new Set(props.memberIds))
const selectedIds = computed(() => new Set(selected.value.map((person) => person.name)))

function toggle(person, checked) {
  if (checked) {
    if (!selectedIds.value.has(person.name)) selected.value = [...selected.value, person]
  } else {
    selected.value = selected.value.filter((row) => row.name !== person.name)
  }
}

function peopleWord(count) {
  return `${count} ${count === 1 ? 'person' : 'people'}`
}

const manyCompanies = computed(() => new Set(props.departments.map((row) => row.company)).size > 1)
function departmentLabel(row) {
  const label = row.department_name || row.name
  return manyCompanies.value && row.company ? `${label} (${row.company})` : label
}
const chosenDepartment = computed(() => props.departments.find((row) => row.name === department.value))

async function add(params) {
  error.value = ''
  sending.value = true
  try {
    const response = await call('helixhr.api.add_project_members', { project: props.project, ...params })
    result.value = { added: response?.added || [], skipped: response?.skipped || [] }
    emit('added', response)
    if (params.employees) {
      selected.value = []
      query.value = ''
    }
  } catch (failure) {
    error.value = failure?.messages?.[0] || 'Could not add those people. Please try again.'
  } finally {
    sending.value = false
  }
}

function addSelected() {
  if (selected.value.length) add({ employees: selected.value.map((person) => person.name) })
}

function addDepartment() {
  if (department.value) add({ department: department.value })
}

/** "Not available" stands in for a name the server will not give back (an
 * id outside the caller's companies); its reason would only repeat it. */
function skippedLine(row) {
  const who = row.employee_name || 'Not available'
  return row.reason && row.reason.toLowerCase() !== who.toLowerCase() ? `${who} · ${row.reason}` : who
}
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title: 'Add people', size: 'lg' }"
  >
    <template #body-content>
      <div
        class="space-y-4"
        data-testid="project-member-picker"
      >
        <SegmentedChips
          v-model="mode"
          label="Add by"
          :options="MODES"
        />

        <template v-if="mode === 'people'">
          <SearchField
            v-model="query"
            label="Find people"
            placeholder="Name, employee ID or work email"
            maxlength="60"
            autocomplete="off"
          />

          <div
            v-if="selected.length"
            class="flex flex-wrap gap-2"
            aria-label="Selected people"
            role="group"
          >
            <button
              v-for="person in selected"
              :key="person.name"
              type="button"
              class="inline-flex min-h-9 cursor-pointer items-center gap-1 rounded-full border border-outline-gray-2 bg-surface-gray-2 px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-3"
              :aria-label="`Remove ${person.employee_name} from the selection`"
              @click="toggle(person, false)"
            >
              <span class="break-words">{{ person.employee_name }}</span>
              <span aria-hidden="true">×</span>
            </button>
          </div>

          <div v-if="searched">
            <p
              v-if="searching"
              class="text-sm text-ink-gray-6"
            >
              Searching…
            </p>
            <p
              v-else-if="searchError"
              class="text-sm text-ink-red-4"
              role="alert"
            >
              Could not search just now. Try again in a moment.
            </p>
            <fieldset v-else-if="results.length">
              <legend class="sr-only">
                Matching people
              </legend>
              <ul class="max-h-72 divide-y divide-outline-gray-1 overflow-y-auto">
                <li
                  v-for="person in results"
                  :key="person.name"
                >
                  <label
                    class="flex min-h-11 cursor-pointer items-center gap-3 px-1 py-1.5 text-sm text-ink-gray-8"
                    :class="memberSet.has(person.name) ? 'cursor-not-allowed opacity-70' : ''"
                  >
                    <input
                      type="checkbox"
                      class="size-5 shrink-0"
                      :checked="memberSet.has(person.name) || selectedIds.has(person.name)"
                      :disabled="memberSet.has(person.name) || sending"
                      @change="toggle(person, $event.target.checked)"
                    >
                    <Avatar
                      class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                      :photo-url="person.photo_url"
                      :initials="person.initials"
                      :size="32"
                    />
                    <span class="min-w-0 flex-1 break-words">{{ person.employee_name }}</span>
                    <span
                      v-if="memberSet.has(person.name)"
                      class="shrink-0 text-xs text-ink-gray-5"
                    >Already on the project</span>
                  </label>
                </li>
              </ul>
            </fieldset>
            <p
              v-else
              class="text-sm text-ink-gray-6"
            >
              Nobody matches that.
            </p>
          </div>
          <p
            v-else
            class="text-sm text-ink-gray-6"
          >
            Type at least two letters of a name.
          </p>

          <p
            class="text-sm text-ink-gray-6"
            aria-live="polite"
            data-testid="project-picker-count"
          >
            {{ selected.length ? `${peopleWord(selected.length)} selected.` : 'Nobody selected yet.' }}
          </p>
        </template>

        <template v-else>
          <div class="space-y-1.5">
            <!-- A native select: FormControl's select is a combobox in this
                 frappe-ui (the same reason RosterShiftSheet uses one). -->
            <label
              for="project-picker-department"
              class="text-sm text-ink-gray-7"
            >
              Department
            </label>
            <select
              id="project-picker-department"
              v-model="department"
              class="block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
              :disabled="sending"
            >
              <option value="">
                Choose a department
              </option>
              <option
                v-for="row in departments"
                :key="row.name"
                :value="row.name"
              >
                {{ departmentLabel(row) }}
              </option>
            </select>
          </div>
          <p class="text-sm text-ink-gray-6">
            Adds every active person in the department and the departments under it. People already on
            the project, or with no portal login, are skipped.
          </p>
        </template>

        <p
          v-if="error"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ error }}
        </p>

        <div
          v-if="result"
          class="surface-card p-3 text-sm"
          role="status"
          data-testid="project-picker-result"
        >
          <p class="font-medium text-ink-gray-9">
            Added {{ result.added.length }}, skipped {{ result.skipped.length }}.
          </p>
          <ul
            v-if="result.skipped.length"
            class="mt-2 space-y-1 text-ink-gray-7"
            aria-label="Skipped"
          >
            <li
              v-for="(row, index) in result.skipped"
              :key="`${row.employee}-${index}`"
              class="break-words"
            >
              {{ skippedLine(row) }}
            </li>
          </ul>
          <Button
            class="mt-2"
            variant="ghost"
            @click="result = null"
          >
            Dismiss
          </Button>
        </div>

        <div
          class="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3"
        >
          <Button
            v-if="mode === 'people'"
            variant="solid"
            theme="blue"
            :loading="sending"
            :disabled="!selected.length"
            @click="addSelected"
          >
            {{ selected.length ? `Add ${peopleWord(selected.length)}` : 'Add people' }}
          </Button>
          <Button
            v-else
            variant="solid"
            theme="blue"
            :loading="sending"
            :disabled="!chosenDepartment"
            @click="addDepartment"
          >
            {{ chosenDepartment ? `Add everyone in ${chosenDepartment.department_name || chosenDepartment.name}` : 'Add everyone' }}
          </Button>
          <Button
            variant="subtle"
            @click="open = false"
          >
            Done
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>
