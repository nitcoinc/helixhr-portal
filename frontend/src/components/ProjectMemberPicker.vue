<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Button, Dialog } from 'frappe-ui'
import Avatar from '@/components/Avatar.vue'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import { call } from '@/lib/api'
import { listWords, pickerOffer, pickerSearchLimit } from '@/lib/projects'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'

// Plan 2026-10-31-001 U6 (R5-R7). Add several people to a project in one
// `add_project_members` call: picked from a search, or whole departments.
// The call appends and never removes; who it skipped, and why, stays on
// screen until dismissed. frappe-ui's Dialog (reka-ui) supplies the focus
// trap, Escape and focus return to the button that opened it.
//
// People come from `get_directory`, not `search_people`: a Delivery Manager
// has no admin scope, and `get_directory` is the caller's own company with
// no admin permission required (the same reason the old single-add used it).
//
// Plan 2026-11-01-001 U4 (R4, R5, R8). Departments are a checklist sent as
// one `departments` list. Nobody who can already book is offered: members
// are dropped from the search page, and with the project open to a company
// (`openCompanies`, the server's covered list) that company's departments
// are left out -- and its people, which is every search result when it is
// the caller's own. Each hidden group is counted on screen. There is no
// "Select all"; the server skips the same people if the client is bypassed.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  project: { type: String, required: true },
  /** `get_project.departments`: `{ name, department_name, company,
   * parent_department }`. */
  departments: { type: Array, default: () => [] },
  /** Employee ids already on the project, never offered. */
  memberIds: { type: Array, default: () => [] },
  /** `get_project.open_companies`: companies whose people can already book. */
  openCompanies: { type: Array, default: () => [] },
  /** The caller's own company (`search_projects.company`), whose people
   * `get_directory` searches. */
  callerCompany: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'added'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

// `get_directory` ignores a query under 2 characters (`_DIRECTORY_QUERY_MIN`)
// and would return an unfiltered page, which must never read as matches.
const QUERY_MIN = 2
const MODES = [
  { value: 'people', label: 'People' },
  { value: 'departments', label: 'Departments' },
]

const mode = ref('people')
const query = ref('')
const selected = ref([])
const chosenDepartments = ref([])
const departmentFilter = ref('')
const departmentsBox = ref(null)
const sending = ref(false)
const error = ref('')
const result = ref(null)

// The page size travels with its answer, so "more matches" is judged
// against the limit that page was asked for.
const {
  search,
  cancel,
  results,
  loading: searching,
  error: searchError,
} = useDebouncedSearch(
  async (text) => {
    const limit = pickerSearchLimit(props.memberIds.length)
    const people = (await call('helixhr.api.get_directory', { query: text, limit }))?.people || []
    return { people, limit }
  },
  { initial: { people: [], limit: 0 } },
)

const searched = computed(() => query.value.trim().length >= QUERY_MIN)
watch(query, (value) => {
  const text = value.trim()
  if (text.length < QUERY_MIN) {
    cancel()
    results.value = { people: [], limit: 0 }
    return
  }
  search(text)
})

const offer = computed(() =>
  pickerOffer({
    departments: props.departments,
    people: results.value?.people,
    memberIds: props.memberIds,
    openCompanies: props.openCompanies,
    callerCompany: props.callerCompany,
    limit: results.value?.limit,
  }),
)

watch(
  () => props.modelValue,
  (isOpen) => {
    if (!isOpen) return
    // `get_directory` is the caller's own company: when that company can
    // already book, People has nobody to offer, so start on Departments.
    mode.value = offer.value.peopleCovered ? 'departments' : 'people'
    query.value = ''
    selected.value = []
    chosenDepartments.value = []
    departmentFilter.value = ''
    error.value = ''
    result.value = null
  },
)

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

function departmentsWord(count) {
  return `${count} ${count === 1 ? 'department' : 'departments'}`
}

// --- departments -------------------------------------------------------------

const manyCompanies = computed(() => new Set(props.departments.map((row) => row.company)).size > 1)
function departmentName(row) {
  return row.department_name || row.name
}
const departmentsById = computed(() => new Map(props.departments.map((row) => [row.name, row])))
/** The parent's name when the parent is in the list (it may be out of
 * scope, and then is not named). */
function parentName(row) {
  const parent = row.parent_department && departmentsById.value.get(row.parent_department)
  return parent ? departmentName(parent) : ''
}
const chosenSet = computed(() => new Set(chosenDepartments.value))
const chosenRows = computed(() => props.departments.filter((row) => chosenSet.value.has(row.name)))
const shownDepartments = computed(() => {
  const text = departmentFilter.value.trim().toLowerCase()
  const rows = offer.value.departments
  if (!text) return rows
  return rows.filter((row) => `${departmentName(row)} ${row.company || ''}`.toLowerCase().includes(text))
})
const departmentCountLine = computed(() => {
  if (!departmentFilter.value.trim()) return ''
  const total = offer.value.departments.length
  return `${shownDepartments.value.length} of ${total} ${total === 1 ? 'department' : 'departments'} match.`
})

function toggleDepartment(name, checked) {
  chosenDepartments.value = checked
    ? [...new Set([...chosenDepartments.value, name])]
    : chosenDepartments.value.filter((row) => row !== name)
}

async function showDepartments() {
  mode.value = 'departments'
  // The button that was pressed is gone with the People view; put focus on
  // the department filter rather than lose it to the page.
  await nextTick()
  departmentsBox.value?.querySelector('input')?.focus()
}

// --- adding ------------------------------------------------------------------

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
    } else {
      chosenDepartments.value = []
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

function addDepartments() {
  if (chosenDepartments.value.length) add({ departments: chosenDepartments.value })
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

        <template v-if="mode === 'people' && offer.peopleCovered">
          <div
            class="surface-card space-y-2 p-3 text-sm text-ink-gray-7"
            data-testid="project-picker-people-covered"
          >
            <p>
              Everyone in {{ callerCompany }} can already book here, and people search only covers {{ callerCompany }}.
              Add people from other companies by department.
            </p>
            <Button
              variant="outline"
              @click="showDepartments"
            >
              Choose departments
            </Button>
          </div>
        </template>

        <template v-else-if="mode === 'people'">
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
              class="inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-full border border-outline-gray-2 bg-surface-gray-2 px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-3"
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
            <fieldset v-else-if="offer.people.length">
              <legend class="sr-only">
                Matching people
              </legend>
              <ul class="max-h-72 divide-y divide-outline-gray-1 overflow-y-auto">
                <li
                  v-for="person in offer.people"
                  :key="person.name"
                >
                  <label class="flex min-h-11 cursor-pointer items-center gap-3 px-1 py-1.5 text-sm text-ink-gray-8">
                    <input
                      type="checkbox"
                      class="size-5 shrink-0"
                      :checked="selectedIds.has(person.name)"
                      :disabled="sending"
                      @change="toggle(person, $event.target.checked)"
                    >
                    <Avatar
                      class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                      :photo-url="person.photo_url"
                      :initials="person.initials"
                      :size="32"
                    />
                    <span class="min-w-0 flex-1 break-words">{{ person.employee_name }}</span>
                  </label>
                </li>
              </ul>
            </fieldset>
            <p
              v-else
              class="text-sm text-ink-gray-6"
            >
              {{ offer.hiddenMembers ? 'Nobody else matches that.' : 'Nobody matches that.' }}
            </p>
            <p
              v-if="!searching && !searchError && (offer.hiddenMembers || offer.moreMatches)"
              class="mt-2 text-xs text-ink-gray-5"
              aria-live="polite"
              data-testid="project-picker-hidden"
            >
              <template v-if="offer.hiddenMembers">
                {{ offer.hiddenMembers }} already on this project not shown.
              </template>
              <template v-if="offer.moreMatches">
                More matches: type more of the name.
              </template>
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

        <div
          v-else
          ref="departmentsBox"
          class="space-y-3"
        >
          <p
            v-if="offer.hiddenDepartments"
            class="text-sm text-ink-gray-6"
            data-testid="project-picker-departments-hidden"
          >
            Departments in {{ listWords(offer.hiddenCompanies) }} are left out: everyone there can already book
            here.
          </p>

          <p
            v-if="!offer.departments.length"
            class="text-sm text-ink-gray-6"
          >
            {{
              offer.hiddenDepartments
                ? 'Everyone in these departments can already book here.'
                : 'There are no departments to choose from.'
            }}
          </p>
          <template v-else>
            <SearchField
              v-model="departmentFilter"
              label="Filter departments"
              placeholder="Department name"
              maxlength="140"
              autocomplete="off"
            />
            <p
              class="text-xs text-ink-gray-5"
              aria-live="polite"
              data-testid="project-picker-department-count"
            >
              {{ departmentCountLine }}
            </p>

            <div
              v-if="chosenRows.length"
              class="flex flex-wrap gap-2"
              aria-label="Selected departments"
              role="group"
            >
              <button
                v-for="row in chosenRows"
                :key="row.name"
                type="button"
                class="inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-full border border-outline-gray-2 bg-surface-gray-2 px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-3"
                :aria-label="`Remove ${departmentName(row)} from the selection`"
                @click="toggleDepartment(row.name, false)"
              >
                <span class="break-words">{{ departmentName(row) }}</span>
                <span aria-hidden="true">×</span>
              </button>
            </div>

            <fieldset
              v-if="shownDepartments.length"
              aria-describedby="project-picker-departments-hint"
            >
              <legend class="mb-1 text-sm text-ink-gray-7">
                Departments
              </legend>
              <ul class="max-h-72 divide-y divide-outline-gray-1 overflow-y-auto">
                <li
                  v-for="row in shownDepartments"
                  :key="row.name"
                >
                  <label
                    class="flex min-h-11 cursor-pointer items-center gap-3 px-1 py-1.5 text-sm text-ink-gray-8"
                    data-testid="project-picker-department-option"
                  >
                    <input
                      type="checkbox"
                      class="size-5 shrink-0"
                      :checked="chosenSet.has(row.name)"
                      :disabled="sending"
                      @change="toggleDepartment(row.name, $event.target.checked)"
                    >
                    <span class="min-w-0 flex-1 break-words">
                      {{ departmentName(row) }}
                      <span
                        v-if="parentName(row)"
                        class="block text-xs text-ink-gray-5"
                      >Under {{ parentName(row) }}</span>
                    </span>
                    <span
                      v-if="manyCompanies && row.company"
                      class="shrink-0 text-xs text-ink-gray-5"
                    >{{ row.company }}</span>
                  </label>
                </li>
              </ul>
            </fieldset>
            <p
              v-else
              class="text-sm text-ink-gray-6"
            >
              No departments match.
            </p>
          </template>

          <p
            id="project-picker-departments-hint"
            class="text-sm text-ink-gray-6"
          >
            Ticking a department includes the departments under it. Adds every active person in them; people
            already on the project, who can already book here, or with no portal login are skipped.
          </p>
        </div>

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
            v-if="mode === 'people' && !offer.peopleCovered"
            variant="solid"
            theme="blue"
            :loading="sending"
            :disabled="!selected.length"
            @click="addSelected"
          >
            {{ selected.length ? `Add ${peopleWord(selected.length)}` : 'Add people' }}
          </Button>
          <Button
            v-else-if="mode === 'departments'"
            variant="solid"
            theme="blue"
            :loading="sending"
            :disabled="!chosenDepartments.length"
            @click="addDepartments"
          >
            Add people from {{ departmentsWord(chosenDepartments.length) }}
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
