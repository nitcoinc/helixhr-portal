<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import ProjectPeoplePane from '@/components/projects/ProjectPeoplePane.vue'
import ProjectTasksPane from '@/components/projects/ProjectTasksPane.vue'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import TabBar from '@/components/TabBar.vue'
import { formatDate } from '@/lib/dates'
import { fieldOptions } from '@/lib/lists'
import {
  NO_FILTERS,
  PROJECT_SORTS,
  distinctValues,
  filterProjects,
  filtersFromQuery,
  filtersToQuery,
  isFiltered,
  membersLabel,
  showCompanyFilter,
  sortProjects,
} from '@/lib/projects'
import { useIsDesktop } from '@/lib/useIsDesktop'

// P7-U5 / P7-R1-R3. Administer the projects a caller can reach, per
// `resolve_project_scope` -- a HelixHR Delivery Manager sees exactly the
// projects they are a member of, an HR Manager their own company's, System
// Manager everything. `search_projects` and `get_project` are the server's
// own gates -- a caller outside their scope gets AsyncState's 'forbidden'
// region below, not a client-side redirect (the same posture People.vue
// already takes).
//
// The project's record is addressable by URL (`/projects/:project`), the
// same P2-R12 rule every other detail route follows.
const props = defineProps({
  project: { type: String, default: '' },
})
const route = useRoute()
const router = useRouter()
const isDesktop = useIsDesktop()

// --- the project list ------------------------------------------------------
//
// Plan 2026-10-31-001 U2 (R1, R2; KTD1, KTD2). `search_projects` returns the
// caller's whole list, so filtering and sorting run on the one response. A
// table on desktop (People.vue's plain markup with `aria-sort`), cards on a
// phone. The filters live in the route query, so a reload or a shared link
// keeps them.
const projects = createResource({
  url: 'helixhr.api.search_projects',
  auto: true,
})

const rows = computed(() => projects.data?.projects || [])
// Plan 2026-10-14-001 KTD4: a company access grant reads projects but never
// changes them. Both flags mirror the gates `create_project`, `save_task`
// and `set_project_members` enforce; hiding is only to not offer a refusal.
const canCreate = computed(() => !!projects.data?.can_create)

const filters = reactive({ ...NO_FILTERS })
const sameQuery = (a, b) => JSON.stringify(a) === JSON.stringify(b)

// The URL is the source: a reload, Back, or the nav link to a bare
// /projects all land here. Skipped when the two already agree, so the
// trailing space of a half-typed search is not trimmed out of the field.
watch(
  () => [props.project, route.query],
  () => {
    if (props.project) return
    const fromRoute = filtersFromQuery(route.query)
    if (!sameQuery(filtersToQuery(filters), filtersToQuery(fromRoute))) Object.assign(filters, fromRoute)
  },
  { immediate: true },
)
watch(filters, () => {
  if (props.project) return
  const next = filtersToQuery(filters)
  if (!sameQuery(next, filtersToQuery(filtersFromQuery(route.query)))) router.replace({ query: next })
})

const statusOptions = computed(() => fieldOptions(rows.value, 'status'))
const typeOptions = computed(() => distinctValues(rows.value, 'project_type'))
const companyOptions = computed(() => distinctValues(rows.value, 'company'))
const manyCompanies = computed(() => showCompanyFilter(rows.value))
const BILLABLE_OPTIONS = [
  { value: null, label: 'All' },
  { value: 'yes', label: 'Billable' },
  { value: 'no', label: 'Non-billable' },
]

const sort = ref({ key: 'name', order: 'asc' })
const SORT_LABELS = {
  name: 'Name',
  status: 'Status',
  members: 'Most people',
  tasks: 'Most open tasks',
  end: 'Latest end date',
}

function ariaSort(key) {
  if (sort.value.key !== key) return 'none'
  return sort.value.order === 'desc' ? 'descending' : 'ascending'
}

function sortArrow(key) {
  return { ascending: '↑', descending: '↓' }[ariaSort(key)] || ''
}

function toggleSort(key) {
  const current = sort.value.key === key ? sort.value.order : null
  sort.value = { key, order: current ? (current === 'asc' ? 'desc' : 'asc') : PROJECT_SORTS[key].first }
}

function pickSort(key) {
  sort.value = { key, order: PROJECT_SORTS[key].first }
}

const filteredRows = computed(() => sortProjects(filterProjects(rows.value, filters), sort.value))
const narrowed = computed(() => isFiltered({ ...filters, company: manyCompanies.value ? filters.company : null }))

function clearFilters() {
  Object.assign(filters, NO_FILTERS)
}

function projectCount(count) {
  return `${count} ${count === 1 ? 'project' : 'projects'}`
}

/** The phone card's second line: "Open · 3 people · 5 open tasks · …". */
function projectLine(row) {
  const members = row.open_to_all
    ? 'Everyone'
    : row.member_count != null
      ? `${row.member_count} ${row.member_count === 1 ? 'person' : 'people'}`
      : null
  const tasks =
    row.open_task_count != null ? `${row.open_task_count} open ${row.open_task_count === 1 ? 'task' : 'tasks'}` : null
  return [
    row.status,
    manyCompanies.value ? row.company : null,
    members,
    tasks,
    row.project_type,
    row.billable ? 'Billable' : null,
    row.expected_end_date ? `Ends ${formatDate(row.expected_end_date)}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
}
const emptyTitle = computed(() => (narrowed.value ? 'No projects match.' : 'No projects yet'))
const emptyBody = computed(() =>
  narrowed.value
    ? ''
    : canCreate.value
      ? 'Create a project to start assigning tasks and people.'
      : 'There are no projects for you to see yet.',
)

function openProject(project) {
  router.push(`/projects/${project.name}`)
}

// --- creating a project ------------------------------------------------------

const showCreateForm = ref(false)
const newProjectName = ref('')
const newProjectBillable = ref(false)
const newProjectPriority = ref('')
const newProjectType = ref('')
const createError = ref('')
const createProject = createResource({ url: 'helixhr.api.create_project', method: 'POST' })

// P8-U2: `search_projects`'s own response carries the create form's option
// lists and the caller's company, so the page that hosts the form needs no
// second request for them. `company` is `None` for the Desk-only,
// no-Employee-record persona `create_project` itself refuses (see its
// docstring) -- the form says so instead of offering Create to a caller who
// would only be refused.
const creatorCompany = computed(() => projects.data?.company ?? null)
const projectTypeOptions = computed(() => [
  { label: 'No project type', value: '' },
  ...(projects.data?.project_types || []).map((name) => ({ label: name, value: name })),
])
const priorityOptions = computed(() => [
  { label: 'Default priority', value: '' },
  ...(projects.data?.priority_options || []).map((name) => ({ label: name, value: name })),
])

function startCreate() {
  showCreateForm.value = true
  newProjectName.value = ''
  newProjectBillable.value = false
  newProjectPriority.value = ''
  newProjectType.value = ''
  createError.value = ''
}

async function submitCreate() {
  createError.value = ''
  const project_name = newProjectName.value.trim()
  if (!project_name) {
    createError.value = 'Give the project a name.'
    return
  }
  try {
    const created = await createProject.submit({
      project_name,
      is_billable: newProjectBillable.value ? 1 : 0,
      priority: newProjectPriority.value || undefined,
      project_type: newProjectType.value || undefined,
    })
    showCreateForm.value = false
    // The list's row (and its company, which the detail's "Open to
    // everyone in <company>" names) comes from the server, not a guess.
    projects.reload()
    router.push(`/projects/${created.name}`)
  } catch (error) {
    createError.value = error?.messages?.[0] || 'Could not create that project. Please try again.'
  }
}

// --- the project view --------------------------------------------------------
//
// Plan 2026-10-31-001 U3 (R3, R4). Facts on one line; Tasks and People as
// two panes -- side by side on desktop, a TabBar on a phone -- each with its
// own search, revealing 25 rows at a time (KTD3: `get_project` already
// returns every open task and member). Each pane owns its writes and hands
// the changed list back here, into `detail`.

const detail = createResource({
  url: 'helixhr.api.get_project',
  makeParams: () => ({ project: props.project }),
  auto: false,
})
const canEdit = computed(() => !!detail.data?.can_edit)

const tasks = computed(() => detail.data?.tasks || [])
const members = computed(() => detail.data?.members || [])
// `get_project` carries no company; the list row this page already loaded
// does.
const projectCompany = computed(() => rows.value.find((row) => row.name === props.project)?.company || '')
const companyName = computed(() => projectCompany.value || 'this company')

const facts = computed(() => {
  const data = detail.data || {}
  return [
    { label: 'Status', value: data.status || '—' },
    { label: 'Priority', value: data.priority || '—' },
    { label: 'Type', value: data.project_type || '—' },
    { label: 'Billable', value: data.billable ? 'Yes' : 'No', testid: 'project-billable' },
    { label: 'Start', value: data.expected_start_date ? formatDate(data.expected_start_date) : '—' },
    { label: 'End', value: data.expected_end_date ? formatDate(data.expected_end_date) : '—' },
  ]
})

const paneTab = ref('tasks')
const paneTabs = computed(() => [
  { key: 'tasks', label: 'Tasks', count: tasks.value.length },
  { key: 'people', label: 'People', count: members.value.length },
])

/** A pane's attributes: shown side by side on desktop, a tabpanel on a phone. */
function paneAttrs(key, headingId) {
  const desktop = isDesktop.value
  return {
    id: !desktop && paneTab.value === key ? 'project-pane' : undefined,
    role: desktop ? undefined : 'tabpanel',
    'aria-labelledby': desktop ? headingId : `project-pane-tab-${key}`,
  }
}

function setDetail(field, value) {
  if (detail.data) detail.data[field] = value
}

// Declared after every ref it resets: `immediate` runs it during setup, and a
// ref still in its temporal dead zone throws there, skipping the first mount's
// resets (HR-01). The panes reset their own state on a project change.
watch(
  () => props.project,
  (project) => {
    if (project) detail.reload()
    // Back on the list after working on a project: its counts and flags
    // may have changed.
    else if (projects.data) projects.reload()
    paneTab.value = 'tasks'
    showCreateForm.value = false
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <!-- The project view. Reached at /projects/:project. -->
    <template v-if="project">
      <PageHeader
        :title="detail.data?.project_name || 'Project'"
        :subtitle="detail.data?.status"
      >
        <template #actions>
          <Button
            variant="ghost"
            @click="router.push({ path: '/projects', query: filtersToQuery(filters) })"
          >
            Back to projects
          </Button>
        </template>
      </PageHeader>

      <AsyncState
        section="project"
        :resource="detail"
        :empty="false"
        skeleton="block"
        skeleton-height="h-96"
      >
        <div
          v-if="detail.data"
          class="space-y-4"
        >
          <p
            v-if="!canEdit"
            class="text-sm text-ink-gray-6"
            data-testid="project-view-only"
          >
            View only. This project is outside the ones you manage, so it can't be changed here.
          </p>

          <dl
            class="flex flex-wrap gap-x-5 gap-y-1 text-sm"
            data-testid="project-facts"
          >
            <div
              v-for="fact in facts"
              :key="fact.label"
              class="flex gap-1.5"
            >
              <dt class="text-ink-gray-6">
                {{ fact.label }}
              </dt>
              <dd
                class="font-medium text-ink-gray-9"
                :data-testid="fact.testid"
              >
                {{ fact.value }}
              </dd>
            </div>
          </dl>

          <TabBar
            v-if="!isDesktop"
            v-model="paneTab"
            :tabs="paneTabs"
            label="Project sections"
            panel-id="project-pane"
          />

          <div :class="isDesktop ? 'grid grid-cols-2 items-start gap-4' : ''">
            <ProjectTasksPane
              v-show="isDesktop || paneTab === 'tasks'"
              v-bind="paneAttrs('tasks', 'project-tasks-heading')"
              :project="project"
              :tasks="tasks"
              :can-edit="canEdit"
              @update:tasks="(list) => setDetail('tasks', list)"
            />
            <ProjectPeoplePane
              v-show="isDesktop || paneTab === 'people'"
              v-bind="paneAttrs('people', 'project-members-heading')"
              :project="project"
              :members="members"
              :departments="detail.data.departments || []"
              :open-to-all="!!detail.data.open_to_all"
              :can-edit="canEdit"
              :company-name="companyName"
              @update:members="(list) => setDetail('members', list)"
              @update:open-to-all="(value) => setDetail('open_to_all', value)"
            />
          </div>
        </div>
      </AsyncState>
    </template>

    <!-- The project list. Reached at /projects. -->
    <template v-else>
      <PageHeader
        title="Projects"
        subtitle="Manage the projects, tasks and people you administer."
      >
        <template #actions>
          <Button
            v-if="canCreate"
            variant="solid"
            theme="blue"
            @click="startCreate"
          >
            New project
          </Button>
        </template>
      </PageHeader>

      <Dialog
        v-model="showCreateForm"
        :options="{ title: 'New project', size: 'md' }"
      >
        <template #body-content>
          <form
            class="space-y-3"
            data-testid="project-create-form"
            @submit.prevent="submitCreate"
          >
            <!-- P8-U2: `company` is server-derived, never asked for (KTD2 --
                 the create endpoint refuses a company named in the request
                 body). A caller with no company at all -- the Desk-only
                 persona `create_project` itself refuses -- sees why instead
                 of a form that would only fail on submit. -->
            <p
              v-if="creatorCompany"
              class="text-sm text-ink-gray-6"
            >
              This project will be created in <span class="font-medium text-ink-gray-8">{{ creatorCompany }}</span>.
            </p>
            <p
              v-else
              class="surface-alert p-3 text-sm"
              role="alert"
            >
              Your account has no linked employee record, so a project can't be created from here.
              Use Desk instead.
            </p>
            <FormControl
              v-model="newProjectName"
              type="text"
              label="Project name"
              maxlength="140"
            />
            <div>
              <label
                for="project-create-priority"
                class="text-sm text-ink-gray-7"
              >
                Priority
              </label>
              <select
                id="project-create-priority"
                v-model="newProjectPriority"
                class="mt-1 block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
              >
                <option
                  v-for="option in priorityOptions"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </option>
              </select>
            </div>
            <div>
              <label
                for="project-create-type"
                class="text-sm text-ink-gray-7"
              >
                Project type
              </label>
              <select
                id="project-create-type"
                v-model="newProjectType"
                class="mt-1 block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
              >
                <option
                  v-for="option in projectTypeOptions"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </option>
              </select>
            </div>
            <FormControl
              v-model="newProjectBillable"
              type="checkbox"
              label="Billable"
            />
            <p
              v-if="createError"
              class="surface-alert p-3 text-sm"
              role="alert"
            >
              {{ createError }}
            </p>
            <div class="flex items-center gap-2 pt-1">
              <Button
                type="submit"
                variant="solid"
                theme="blue"
                :disabled="!creatorCompany"
                :loading="createProject.loading"
              >
                Create
              </Button>
              <Button
                type="button"
                variant="ghost"
                @click="showCreateForm = false"
              >
                Cancel
              </Button>
            </div>
          </form>
        </template>
      </Dialog>

      <div
        v-if="rows.length"
        class="mb-3 flex flex-wrap items-end gap-3"
        data-testid="projects-filters"
      >
        <div class="w-full lg:w-72">
          <SearchField
            v-model="filters.q"
            label="Search"
            placeholder="Project name or code"
            maxlength="140"
          />
        </div>
        <div v-if="typeOptions.length">
          <label
            for="projects-filter-type"
            class="text-sm text-ink-gray-7"
          >
            Type
          </label>
          <select
            id="projects-filter-type"
            v-model="filters.type"
            class="mt-1 block min-h-11 rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
          >
            <option :value="null">
              All types
            </option>
            <option
              v-for="type in typeOptions"
              :key="type"
              :value="type"
            >
              {{ type }}
            </option>
          </select>
        </div>
        <div>
          <label
            for="projects-filter-billable"
            class="text-sm text-ink-gray-7"
          >
            Billable
          </label>
          <select
            id="projects-filter-billable"
            v-model="filters.billable"
            class="mt-1 block min-h-11 rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            data-testid="projects-filter-billable"
          >
            <option
              v-for="option in BILLABLE_OPTIONS"
              :key="String(option.value)"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
        <div v-if="manyCompanies">
          <label
            for="projects-filter-company"
            class="text-sm text-ink-gray-7"
          >
            Company
          </label>
          <select
            id="projects-filter-company"
            v-model="filters.company"
            class="mt-1 block min-h-11 rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
          >
            <option :value="null">
              All companies
            </option>
            <option
              v-for="company in companyOptions"
              :key="company"
              :value="company"
            >
              {{ company }}
            </option>
          </select>
        </div>
        <!-- The table sorts from its headers; a phone has no headers. -->
        <div v-if="!isDesktop && rows.length > 1">
          <label
            for="projects-sort"
            class="text-sm text-ink-gray-7"
          >
            Sort
          </label>
          <select
            id="projects-sort"
            :value="sort.key"
            class="mt-1 block min-h-11 rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            data-testid="projects-sort"
            @change="pickSort($event.target.value)"
          >
            <option
              v-for="(label, key) in SORT_LABELS"
              :key="key"
              :value="key"
            >
              {{ label }}
            </option>
          </select>
        </div>
      </div>

      <!-- U17 (HR-15): outside AsyncState, so a status with nothing in it
           shows the empty sentence with the chips still there to undo it.
           One status draws no chips. -->
      <template v-if="rows.length">
        <SegmentedChips
          v-if="statusOptions.length > 2"
          v-model="filters.status"
          class="mb-3"
          label="Filter projects by status"
          :options="statusOptions"
        />
        <p
          class="mb-3 text-sm text-ink-gray-6"
          aria-live="polite"
          data-testid="projects-count"
        >
          <span class="tabular">{{ narrowed ? `${filteredRows.length} of ${projectCount(rows.length)}` : projectCount(rows.length) }}</span>.
        </p>
      </template>

      <AsyncState
        section="projects"
        :resource="projects"
        :empty="filteredRows.length === 0"
        :empty-title="emptyTitle"
        :empty-body="emptyBody"
        skeleton="row"
        :skeleton-rows="6"
      >
        <template
          v-if="narrowed"
          #empty-action
        >
          <Button
            variant="outline"
            @click="clearFilters"
          >
            Clear filters
          </Button>
        </template>

        <!-- Desktop: plain table markup with `aria-sort` headers, the
             People.vue / ReportTable pattern (KTD1). -->
        <div
          v-if="isDesktop"
          class="surface-card elev-1 overflow-x-auto"
        >
          <table
            class="w-full border-separate border-spacing-0 text-sm"
            data-testid="projects-table"
          >
            <caption class="sr-only">
              Projects
            </caption>
            <thead>
              <tr>
                <th
                  scope="col"
                  :aria-sort="ariaSort('name')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('name')"
                  >
                    Name
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('name') }}</span>
                  </button>
                </th>
                <th
                  scope="col"
                  :aria-sort="ariaSort('status')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('status')"
                  >
                    Status
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('status') }}</span>
                  </button>
                </th>
                <th
                  v-if="manyCompanies"
                  scope="col"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  Company
                </th>
                <th
                  scope="col"
                  :aria-sort="ariaSort('members')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-right"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('members')"
                  >
                    Members
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('members') }}</span>
                  </button>
                </th>
                <th
                  scope="col"
                  :aria-sort="ariaSort('tasks')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-right"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('tasks')"
                  >
                    Open tasks
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('tasks') }}</span>
                  </button>
                </th>
                <th
                  scope="col"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  Type
                </th>
                <th
                  scope="col"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  Billable
                </th>
                <th
                  scope="col"
                  :aria-sort="ariaSort('end')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('end')"
                  >
                    End
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('end') }}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in filteredRows"
                :key="row.name"
                class="text-ink-gray-7 hover:bg-surface-gray-2"
                data-testid="project-row"
              >
                <td class="border-b border-outline-gray-2 px-3 py-0.5">
                  <button
                    type="button"
                    class="flex min-h-11 w-full min-w-0 cursor-pointer flex-col justify-center text-left"
                    @click="openProject(row)"
                  >
                    <span class="break-words font-medium text-ink-gray-9">{{ row.project_name }}</span>
                    <span
                      v-if="row.name !== row.project_name"
                      class="break-words text-xs text-ink-gray-5"
                    >{{ row.name }}</span>
                  </button>
                </td>
                <td class="border-b border-outline-gray-2 px-3 py-2">
                  {{ row.status }}
                </td>
                <td
                  v-if="manyCompanies"
                  class="border-b border-outline-gray-2 px-3 py-2"
                >
                  {{ row.company }}
                </td>
                <td
                  class="tabular border-b border-outline-gray-2 px-3 py-2 text-right"
                  data-testid="project-members-cell"
                >
                  {{ membersLabel(row) }}
                </td>
                <td class="tabular border-b border-outline-gray-2 px-3 py-2 text-right">
                  {{ row.open_task_count ?? '' }}
                </td>
                <td class="border-b border-outline-gray-2 px-3 py-2">
                  {{ row.project_type || '—' }}
                </td>
                <td class="border-b border-outline-gray-2 px-3 py-2">
                  {{ row.billable ? 'Yes' : 'No' }}
                </td>
                <td class="tabular whitespace-nowrap border-b border-outline-gray-2 px-3 py-2">
                  {{ row.expected_end_date ? formatDate(row.expected_end_date) : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Phone: one compact card per row, in the table's order. -->
        <ul
          v-else
          class="space-y-2"
        >
          <li
            v-for="row in filteredRows"
            :key="row.name"
          >
            <button
              type="button"
              class="surface-card elev-1 flex w-full min-w-0 items-center gap-3 p-3 text-left"
              data-testid="project-card"
              @click="openProject(row)"
            >
              <span class="min-w-0 flex-1">
                <span class="block break-words font-medium text-ink-gray-9">{{ row.project_name }}</span>
                <span class="block break-words text-sm text-ink-gray-6">
                  {{ projectLine(row) }}
                </span>
              </span>
              <Icon
                name="chevronRight"
                class="shrink-0 text-ink-gray-4"
              />
            </button>
          </li>
        </ul>
      </AsyncState>
    </template>
  </div>
</template>
