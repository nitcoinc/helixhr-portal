<script setup>
import Avatar from '@/components/Avatar.vue'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import ProjectMemberPicker from '@/components/ProjectMemberPicker.vue'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import TabBar from '@/components/TabBar.vue'
import { formatDate } from '@/lib/dates'
import { fieldOptions } from '@/lib/lists'
import {
  NO_FILTERS,
  PROJECT_SORTS,
  TASK_PASTE_LIMIT,
  distinctValues,
  filterProjects,
  filtersFromQuery,
  filtersToQuery,
  isFiltered,
  matchRows,
  membersLabel,
  parseTaskLines,
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
// returns every open task and member).

const detail = createResource({
  url: 'helixhr.api.get_project',
  makeParams: () => ({ project: props.project }),
  auto: false,
})
const canEdit = computed(() => !!detail.data?.can_edit)

const tasks = computed(() => detail.data?.tasks || [])
const members = computed(() => detail.data?.members || [])
const memberEmployeeIds = computed(() => members.value.map((m) => m.employee).filter(Boolean))
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

const PANE_PAGE = 25
const paneTab = ref('tasks')
const paneTabs = computed(() => [
  { key: 'tasks', label: 'Tasks', count: tasks.value.length },
  { key: 'people', label: 'People', count: members.value.length },
])

/** One pane's search and "show more" window over `source`. */
function usePane(source, field) {
  const query = ref('')
  const limit = ref(PANE_PAGE)
  watch(query, () => (limit.value = PANE_PAGE))
  const matched = computed(() => matchRows(source.value, query.value, field))
  const shown = computed(() => matched.value.slice(0, limit.value))
  const more = computed(() => Math.min(PANE_PAGE, matched.value.length - shown.value.length))
  function showMore() {
    limit.value += PANE_PAGE
  }
  function reset() {
    query.value = ''
    limit.value = PANE_PAGE
  }
  return { query, matched, shown, more, showMore, reset }
}

const taskPane = usePane(tasks, 'subject')
const memberPane = usePane(members, 'employee_name')
const taskQuery = taskPane.query
const memberQuery = memberPane.query

function countLine(pane, total, noun) {
  if (!total) return ''
  const shown = pane.shown.value.length
  const matched = pane.matched.value.length
  if (pane.query.value.trim()) return `${matched} of ${total} ${noun} match.`
  return shown < total ? `Showing ${shown} of ${total} ${noun}.` : ''
}

// --- tasks -------------------------------------------------------------------

const newTasks = ref('')
const taskError = ref('')
const taskNotice = ref('')
const editingTask = ref(null)
const editingSubject = ref('')
const saveTask = createResource({ url: 'helixhr.api.save_task', method: 'POST' })
const addTasks = createResource({ url: 'helixhr.api.add_tasks', method: 'POST' })

const parsedTasks = computed(() => parseTaskLines(newTasks.value))
const tooManyTasks = computed(() => parsedTasks.value.total > TASK_PASTE_LIMIT)
const addTaskLabel = computed(() => {
  const count = parsedTasks.value.subjects.length
  return count > 1 ? `Add ${count} tasks` : 'Add task'
})

/** One `add_tasks` call for every line (R4): all of them land, or none. */
async function submitTasks() {
  taskError.value = ''
  taskNotice.value = ''
  const { subjects, duplicates } = parsedTasks.value
  if (!subjects.length || tooManyTasks.value) return
  try {
    const response = await addTasks.submit({ project: props.project, subjects })
    // Newest first, so what was just added is in view however long the
    // list is; a reload shows the server's own order.
    detail.data.tasks = [...(response?.tasks || []), ...tasks.value]
    newTasks.value = ''
    taskQuery.value = ''
    const skipped = response?.duplicates || []
    const notes = []
    if (skipped.length) {
      notes.push(
        `${skipped.length === 1 ? 'One was' : `${skipped.length} were`} already open here, so left out: ${skipped.join(', ')}.`,
      )
    }
    if (duplicates) notes.push(`${duplicates} repeated ${duplicates === 1 ? 'line was' : 'lines were'} left out.`)
    taskNotice.value = notes.join(' ')
  } catch (error) {
    taskError.value = error?.messages?.[0] || 'Could not add those tasks. Please try again.'
  }
}

function startRename(task) {
  editingTask.value = task.name
  editingSubject.value = task.subject
}

function cancelRename() {
  editingTask.value = null
}

async function saveRename(task) {
  const subject = editingSubject.value.trim()
  editingTask.value = null
  if (!subject || subject === task.subject) return
  taskError.value = ''
  try {
    const updated = await saveTask.submit({ project: props.project, task: task.name, subject })
    Object.assign(task, updated)
  } catch (error) {
    taskError.value = error?.messages?.[0] || 'Could not rename that task. Please try again.'
  }
}

async function closeTask(task) {
  taskError.value = ''
  try {
    await saveTask.submit({ project: props.project, task: task.name, status: 'Completed' })
    // `get_project` only ever returns open tasks (_project_open_tasks), so a
    // closed task's row is removed locally rather than updated in place.
    detail.data.tasks = tasks.value.filter((row) => row.name !== task.name)
  } catch (error) {
    taskError.value = error?.messages?.[0] || 'Could not close that task. Please try again.'
  }
}

// --- members -------------------------------------------------------------------
//
// U6: adding goes through ProjectMemberPicker (`add_project_members`, which
// appends). Removing still replaces the list through `set_project_members`.

const memberError = ref('')
const pickerOpen = ref(false)
const setMembers = createResource({ url: 'helixhr.api.set_project_members', method: 'POST' })

function onMembersAdded(response) {
  memberError.value = ''
  if (response?.members) detail.data.members = response.members
}

async function removeMember(member) {
  if (!member.employee) return
  memberError.value = ''
  const employees = memberEmployeeIds.value.filter((id) => id !== member.employee)
  try {
    await setMembers.submit({ project: props.project, employees })
    detail.data.members = members.value.filter((row) => row.employee !== member.employee)
  } catch (error) {
    memberError.value = error?.messages?.[0] || 'Could not remove that person. Please try again.'
  }
}

// --- "Open to everyone" (U6, R8) ----------------------------------------------
//
// Either direction is confirmed first, naming its effect. While the write is
// in flight the switch shows the new state as pending; a refusal puts it
// back and says why.

const confirmOpenToAll = ref(false)
const openTarget = ref(false)
const openPending = ref(null)
const openError = ref('')
const setOpenToAll = createResource({ url: 'helixhr.api.set_project_open_to_all', method: 'POST' })
const openToAll = computed(() => !!detail.data?.open_to_all)
const switchOn = computed(() => openPending.value ?? openToAll.value)

function askOpenToAll() {
  if (openPending.value !== null) return
  openError.value = ''
  openTarget.value = !openToAll.value
  confirmOpenToAll.value = true
}

async function applyOpenToAll() {
  const target = openTarget.value
  confirmOpenToAll.value = false
  openPending.value = target
  try {
    const response = await setOpenToAll.submit({ project: props.project, value: target ? 1 : 0 })
    detail.data.open_to_all = !!response?.open_to_all
  } catch (error) {
    openError.value =
      error?.messages?.[0] || `Could not change that. The project is still ${target ? 'members only' : 'open to everyone'}.`
  } finally {
    openPending.value = null
  }
}

// Declared after every ref it resets: `immediate` runs it during setup, and a
// ref still in its temporal dead zone throws there, skipping the first mount's
// resets (HR-01).
watch(
  () => props.project,
  (project) => {
    if (project) detail.reload()
    // Back on the list after working on a project: its counts and flags
    // may have changed.
    else if (projects.data) projects.reload()
    taskPane.reset()
    memberPane.reset()
    paneTab.value = 'tasks'
    newTasks.value = ''
    taskError.value = ''
    taskNotice.value = ''
    memberError.value = ''
    openError.value = ''
    pickerOpen.value = false
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
            <section
              v-show="isDesktop || paneTab === 'tasks'"
              :id="!isDesktop && paneTab === 'tasks' ? 'project-pane' : undefined"
              :role="isDesktop ? undefined : 'tabpanel'"
              :aria-labelledby="isDesktop ? 'project-tasks-heading' : 'project-pane-tab-tasks'"
              class="surface-card elev-1 min-w-0 p-4"
              data-testid="project-tasks-pane"
            >
              <h2
                id="project-tasks-heading"
                class="font-heading text-base font-semibold text-ink-gray-9"
              >
                Tasks <span class="tabular text-sm font-normal text-ink-gray-5">{{ tasks.length }}</span>
              </h2>

              <div
                v-if="tasks.length"
                class="mt-3"
              >
                <SearchField
                  v-model="taskQuery"
                  label="Search tasks"
                  placeholder="Task name"
                  maxlength="140"
                />
              </div>
              <p
                class="mt-2 text-xs text-ink-gray-5"
                aria-live="polite"
                data-testid="project-tasks-count"
              >
                {{ countLine(taskPane, tasks.length, tasks.length === 1 ? 'task' : 'tasks') }}
              </p>

              <ul
                v-if="taskPane.shown.value.length"
                class="divide-y divide-outline-gray-1"
              >
                <li
                  v-for="task in taskPane.shown.value"
                  :key="task.name"
                  class="flex items-center gap-3 py-2"
                  data-testid="project-task"
                >
                  <template v-if="editingTask === task.name">
                    <input
                      v-model="editingSubject"
                      type="text"
                      maxlength="140"
                      :aria-label="`Rename ${task.subject}`"
                      class="min-w-0 flex-1 rounded-md border border-outline-gray-2 px-2.5 py-1.5 text-sm text-ink-gray-8"
                      @keyup.enter="saveRename(task)"
                      @keyup.esc="cancelRename"
                    >
                    <Button
                      variant="solid"
                      theme="blue"
                      @click="saveRename(task)"
                    >
                      Save
                    </Button>
                    <Button
                      variant="ghost"
                      @click="cancelRename"
                    >
                      Cancel
                    </Button>
                  </template>
                  <template v-else>
                    <button
                      v-if="canEdit"
                      type="button"
                      class="min-w-0 flex-1 break-words text-left text-sm font-medium text-ink-gray-9"
                      @click="startRename(task)"
                    >
                      {{ task.subject }}
                    </button>
                    <span
                      v-else
                      class="min-w-0 flex-1 break-words text-sm font-medium text-ink-gray-9"
                    >
                      {{ task.subject }}
                    </span>
                    <span class="shrink-0 text-sm text-ink-gray-5">{{ task.status }}</span>
                    <Button
                      v-if="canEdit"
                      variant="ghost"
                      :aria-label="`Close ${task.subject}`"
                      @click="closeTask(task)"
                    >
                      Close
                    </Button>
                  </template>
                </li>
              </ul>
              <p
                v-else
                class="text-sm text-ink-gray-6"
              >
                {{ tasks.length ? `No tasks match “${taskQuery.trim()}”.` : 'No open tasks yet.' }}
              </p>

              <div
                v-if="taskPane.more.value > 0"
                class="mt-2 text-center"
              >
                <Button
                  variant="ghost"
                  @click="taskPane.showMore"
                >
                  Show {{ taskPane.more.value }} more
                </Button>
              </div>

              <p
                v-if="taskError"
                class="surface-alert mt-3 p-3 text-sm"
                role="alert"
              >
                {{ taskError }}
              </p>
              <p
                v-if="taskNotice"
                class="mt-3 text-sm text-ink-gray-7"
                role="status"
                data-testid="project-task-notice"
              >
                {{ taskNotice }}
              </p>

              <form
                v-if="canEdit"
                class="mt-4 space-y-2"
                @submit.prevent="submitTasks"
              >
                <label
                  for="project-new-tasks"
                  class="block text-sm text-ink-gray-7"
                >
                  Add tasks
                </label>
                <textarea
                  id="project-new-tasks"
                  v-model="newTasks"
                  rows="3"
                  placeholder="New task"
                  aria-describedby="project-new-tasks-hint"
                  class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                  @keydown.enter.exact.prevent="submitTasks"
                />
                <p
                  id="project-new-tasks-hint"
                  class="text-xs text-ink-gray-5"
                >
                  One task per line; paste up to {{ TASK_PASTE_LIMIT }} at once. Shift+Enter starts a new line.
                </p>
                <p
                  v-if="tooManyTasks"
                  class="text-sm text-ink-red-4"
                  role="alert"
                >
                  That is {{ parsedTasks.total }} lines. Add at most {{ TASK_PASTE_LIMIT }} at a time.
                </p>
                <Button
                  type="submit"
                  variant="outline"
                  :loading="addTasks.loading"
                  :disabled="!parsedTasks.subjects.length || tooManyTasks"
                >
                  {{ addTaskLabel }}
                </Button>
              </form>
            </section>

            <section
              v-show="isDesktop || paneTab === 'people'"
              :id="!isDesktop && paneTab === 'people' ? 'project-pane' : undefined"
              :role="isDesktop ? undefined : 'tabpanel'"
              :aria-labelledby="isDesktop ? 'project-members-heading' : 'project-pane-tab-people'"
              class="surface-card elev-1 min-w-0 p-4"
              data-testid="project-people-pane"
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h2
                  id="project-members-heading"
                  class="font-heading text-base font-semibold text-ink-gray-9"
                >
                  People <span class="tabular text-sm font-normal text-ink-gray-5">{{ members.length }}</span>
                </h2>
                <button
                  v-if="canEdit"
                  type="button"
                  role="switch"
                  class="flex min-h-11 cursor-pointer items-center gap-3 text-left text-sm font-medium text-ink-gray-9 aria-disabled:cursor-progress"
                  :aria-checked="switchOn ? 'true' : 'false'"
                  :aria-disabled="openPending !== null ? 'true' : undefined"
                  :aria-busy="openPending !== null ? 'true' : undefined"
                  data-testid="project-open-toggle"
                  @click="askOpenToAll"
                >
                  <span
                    class="flex h-6 w-10 shrink-0 items-center rounded-full p-0.5 transition-colors duration-200"
                    :class="[
                      switchOn ? 'justify-end bg-heading' : 'justify-start bg-surface-gray-3',
                      openPending !== null ? 'opacity-60' : '',
                    ]"
                    aria-hidden="true"
                  >
                    <span class="h-5 w-5 rounded-full bg-white" />
                  </span>
                  <span class="break-words">Open to everyone in {{ companyName }}</span>
                  <span
                    v-if="openPending !== null"
                    class="text-xs font-normal text-ink-gray-5"
                  >Saving…</span>
                </button>
              </div>

              <p
                v-if="openError"
                class="surface-alert mt-3 p-3 text-sm"
                role="alert"
              >
                {{ openError }}
              </p>
              <p
                v-if="openToAll"
                class="mt-2 text-sm text-ink-gray-7"
                data-testid="project-open-note"
              >
                Everyone active in {{ companyName }} can book time here. People below were added by name.
              </p>

              <div
                v-if="members.length"
                class="mt-3"
              >
                <SearchField
                  v-model="memberQuery"
                  label="Search people"
                  placeholder="Name"
                  maxlength="140"
                />
              </div>
              <p
                class="mt-2 text-xs text-ink-gray-5"
                aria-live="polite"
              >
                {{ countLine(memberPane, members.length, members.length === 1 ? 'person' : 'people') }}
              </p>

              <ul
                v-if="memberPane.shown.value.length"
                class="divide-y divide-outline-gray-1"
              >
                <li
                  v-for="member in memberPane.shown.value"
                  :key="member.employee || member.employee_name"
                  class="flex items-center gap-3 py-2"
                  data-testid="project-member"
                >
                  <Avatar
                    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-xs font-bold text-ink-green-3"
                    :photo-url="member.photo_url"
                    :initials="member.initials"
                    :size="32"
                  />
                  <span class="min-w-0 flex-1 break-words text-sm font-medium text-ink-gray-9">
                    {{ member.employee_name }}
                  </span>
                  <Button
                    v-if="member.employee && canEdit"
                    variant="ghost"
                    :aria-label="`Remove ${member.employee_name}`"
                    :disabled="setMembers.loading"
                    @click="removeMember(member)"
                  >
                    Remove
                  </Button>
                </li>
              </ul>
              <p
                v-else
                class="text-sm text-ink-gray-6"
              >
                {{
                  members.length
                    ? `Nobody here matches “${memberQuery.trim()}”.`
                    : openToAll
                      ? 'Nobody is listed by name.'
                      : 'Nobody is assigned to this project yet.'
                }}
              </p>

              <div
                v-if="memberPane.more.value > 0"
                class="mt-2 text-center"
              >
                <Button
                  variant="ghost"
                  @click="memberPane.showMore"
                >
                  Show {{ memberPane.more.value }} more
                </Button>
              </div>

              <p
                v-if="memberError"
                class="surface-alert mt-3 p-3 text-sm"
                role="alert"
              >
                {{ memberError }}
              </p>

              <Button
                v-if="canEdit"
                class="mt-4"
                variant="outline"
                @click="pickerOpen = true"
              >
                Add people
              </Button>
            </section>
          </div>

          <ProjectMemberPicker
            v-if="canEdit"
            v-model="pickerOpen"
            :project="project"
            :departments="detail.data.departments || []"
            :member-ids="memberEmployeeIds"
            @added="onMembersAdded"
          />

          <Dialog
            v-model="confirmOpenToAll"
            :options="{ title: openTarget ? 'Open to everyone?' : 'Only listed members?', size: 'sm' }"
          >
            <template #body-content>
              <p
                class="text-sm text-ink-gray-7"
                data-testid="project-open-confirm"
              >
                {{
                  openTarget
                    ? `Every active employee in ${companyName} can book time on this project.`
                    : 'Only listed members can book. Saved timesheet weeks that still use this project will need that row removed.'
                }}
              </p>
              <div class="mt-4 flex flex-wrap items-center gap-2">
                <Button
                  variant="solid"
                  theme="blue"
                  @click="applyOpenToAll"
                >
                  {{ openTarget ? 'Open to everyone' : 'Members only' }}
                </Button>
                <Button
                  variant="subtle"
                  @click="confirmOpenToAll = false"
                >
                  Cancel
                </Button>
              </div>
            </template>
          </Dialog>
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
