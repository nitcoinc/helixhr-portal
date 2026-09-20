<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { formatDate } from '@/lib/dates'

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
const router = useRouter()

// --- the project list ------------------------------------------------------
//
// `search_projects` takes no query/paging parameters (a caller's scope is
// already small -- a company, or one person's memberships), so narrowing by
// name is a client-side filter over the one response, not a second request
// per keystroke.
const projects = createResource({
  url: 'helixhr.api.search_projects',
  auto: true,
})

const query = ref('')
const rows = computed(() => projects.data?.projects || [])
const filteredRows = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return rows.value
  return rows.value.filter((row) => (row.project_name || '').toLowerCase().includes(needle))
})
const emptyTitle = computed(() =>
  query.value.trim() ? 'Nothing matches that search' : 'No projects yet',
)
const emptyBody = computed(() =>
  query.value.trim()
    ? 'Try a different name.'
    : 'Create a project to start assigning tasks and people.',
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

function cancelCreate() {
  showCreateForm.value = false
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
    // Shows in the list without a reload of `search_projects` -- the write's
    // own response already carries everything the row needs to render.
    projects.data = {
      ...projects.data,
      projects: [
        { name: created.name, project_name: created.project_name, status: created.status },
        ...rows.value,
      ],
    }
    showCreateForm.value = false
    router.push(`/projects/${created.name}`)
  } catch (error) {
    createError.value = error?.messages?.[0] || 'Could not create that project. Please try again.'
  }
}

// --- the project view --------------------------------------------------------

const detail = createResource({
  url: 'helixhr.api.get_project',
  makeParams: () => ({ project: props.project }),
  auto: false,
})

watch(
  () => props.project,
  (project) => {
    if (project) detail.reload()
    memberQuery.value = ''
    taskError.value = ''
    memberError.value = ''
    showCreateForm.value = false
  },
  { immediate: true },
)

const tasks = computed(() => detail.data?.tasks || [])
const members = computed(() => detail.data?.members || [])
const memberEmployeeIds = computed(() => members.value.map((m) => m.employee).filter(Boolean))

// --- tasks -------------------------------------------------------------------

const newTaskSubject = ref('')
const taskError = ref('')
const editingTask = ref(null)
const editingSubject = ref('')
const saveTask = createResource({ url: 'helixhr.api.save_task', method: 'POST' })

async function addTask() {
  taskError.value = ''
  const subject = newTaskSubject.value.trim()
  if (!subject) return
  try {
    const task = await saveTask.submit({ project: props.project, subject })
    detail.data.tasks = [...(detail.data.tasks || []), task]
    newTaskSubject.value = ''
  } catch (error) {
    taskError.value = error?.messages?.[0] || 'Could not add that task. Please try again.'
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
// No dedicated people-picker component exists in the portal today. The
// employee-facing `get_directory` read (Directory.vue's own search) is
// reused here rather than `search_people`: the latter is gated by
// `resolve_admin_scope`, which a HelixHR Delivery Manager -- an
// "assigned"-scope caller, never an admin one -- would be refused by. The
// plan's own note for the Delivery Manager role (P7-U1) is that the picker
// "resolves names through existing scoped reads", and `get_directory` is
// exactly that: every employee's own company, no admin permission required.
// P8-U3: `_DIRECTORY_QUERY_MIN` on the server (helixhr/api.py) -- below this,
// `get_directory` ignores the search entirely and returns an *unfiltered*
// page, which this picker must never show as if it were a set of matches.
// The limit is raised well past the old `8`: at eight or more people
// already on the project, `memberMatches` filtering assigned people out
// *after* the server's page could leave nothing to show even though real
// matches exist further down the company.
const MEMBER_QUERY_MIN = 2
const MEMBER_RESULTS_LIMIT = 20

const memberQuery = ref('')
const memberSearch = ref('')
const memberResults = createResource({
  url: 'helixhr.api.get_directory',
  makeParams: () => ({ query: memberSearch.value, limit: MEMBER_RESULTS_LIMIT }),
  auto: false,
})
const memberError = ref('')
const setMembers = createResource({ url: 'helixhr.api.set_project_members', method: 'POST' })

let memberPending = null
watch(memberQuery, (value) => {
  clearTimeout(memberPending)
  const trimmed = value.trim()
  if (trimmed.length < MEMBER_QUERY_MIN) {
    // Below the server's own minimum, `get_directory` would return everyone
    // in the company unfiltered -- clear the panel instead of showing that
    // as a set of "matches" for a query the server never actually ran.
    memberSearch.value = ''
    memberResults.data = null
    return
  }
  memberPending = setTimeout(() => {
    memberSearch.value = trimmed
    memberResults.reload()
  }, 250)
})
onUnmounted(() => clearTimeout(memberPending))

const memberMatches = computed(() =>
  (memberResults.data?.people || []).filter((person) => !memberEmployeeIds.value.includes(person.name)),
)
// Distinguishes "haven't searched enough yet" from "searched and found
// nobody" -- both look like an empty `memberMatches`, but only the second
// should render "Nobody matches that".
const memberSearchActive = computed(() => memberQuery.value.trim().length >= MEMBER_QUERY_MIN)

function focusMemberSearch() {
  // Re-running the *previous* search's stale results on focus (P8-U3) --
  // e.g. right after `addMember` clears `memberQuery` -- showed a panel that
  // did not match anything currently typed. Only reload when there is an
  // actual query in the box to reload.
  if (memberSearchActive.value) memberResults.reload()
}

async function addMember(person) {
  memberError.value = ''
  const employees = [...memberEmployeeIds.value, person.name]
  try {
    await setMembers.submit({ project: props.project, employees })
    detail.data.members = [
      ...members.value,
      { employee: person.name, employee_name: person.employee_name, initials: person.initials },
    ]
    memberQuery.value = ''
    memberSearch.value = ''
    memberResults.data = null
  } catch (error) {
    memberError.value = error?.messages?.[0] || 'Could not assign that person. Please try again.'
  }
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
            variant="outline"
            @click="router.push('/projects')"
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
          class="space-y-6"
        >
          <section class="surface-card elev-1 grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-6">
            <div>
              <p class="text-sm text-ink-gray-6">
                Status
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ detail.data.status }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Priority
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ detail.data.priority || '—' }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Type
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ detail.data.project_type || '—' }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Billable
              </p>
              <p
                class="font-medium text-ink-gray-9"
                data-testid="project-billable"
              >
                {{ detail.data.billable ? 'Yes' : 'No' }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Start
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ detail.data.expected_start_date ? formatDate(detail.data.expected_start_date) : '—' }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                End
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ detail.data.expected_end_date ? formatDate(detail.data.expected_end_date) : '—' }}
              </p>
            </div>
          </section>

          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="project-tasks-heading"
          >
            <h2
              id="project-tasks-heading"
              class="font-heading text-base font-semibold text-ink-gray-9"
            >
              Tasks
            </h2>

            <ul
              v-if="tasks.length"
              class="mt-2 divide-y divide-outline-gray-1"
            >
              <li
                v-for="task in tasks"
                :key="task.name"
                class="flex items-center gap-3 py-2"
              >
                <template v-if="editingTask === task.name">
                  <input
                    v-model="editingSubject"
                    type="text"
                    maxlength="140"
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
                    type="button"
                    class="min-w-0 flex-1 truncate text-left text-sm font-medium text-ink-gray-9"
                    @click="startRename(task)"
                  >
                    {{ task.subject }}
                  </button>
                  <span class="shrink-0 text-sm text-ink-gray-5">{{ task.status }}</span>
                  <Button
                    variant="ghost"
                    @click="closeTask(task)"
                  >
                    Close
                  </Button>
                </template>
              </li>
            </ul>
            <p
              v-else
              class="mt-2 text-sm text-ink-gray-6"
            >
              No open tasks.
            </p>

            <p
              v-if="taskError"
              class="surface-alert mt-3 p-3 text-sm"
              role="alert"
            >
              {{ taskError }}
            </p>

            <div class="mt-4 flex flex-wrap items-center gap-2">
              <input
                v-model="newTaskSubject"
                type="text"
                maxlength="140"
                placeholder="New task"
                class="min-w-0 flex-1 rounded-md border border-outline-gray-2 px-2.5 py-1.5 text-sm text-ink-gray-8"
                @keyup.enter="addTask"
              >
              <Button
                variant="outline"
                :loading="saveTask.loading"
                @click="addTask"
              >
                Add task
              </Button>
            </div>
          </section>

          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="project-members-heading"
          >
            <h2
              id="project-members-heading"
              class="font-heading text-base font-semibold text-ink-gray-9"
            >
              Members
            </h2>

            <ul
              v-if="members.length"
              class="mt-2 divide-y divide-outline-gray-1"
            >
              <li
                v-for="member in members"
                :key="member.employee || member.employee_name"
                class="flex items-center gap-3 py-2"
              >
                <span
                  class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-xs font-bold text-ink-green-3"
                  aria-hidden="true"
                >{{ member.initials }}</span>
                <span class="min-w-0 flex-1 truncate text-sm font-medium text-ink-gray-9">
                  {{ member.employee_name }}
                </span>
                <Button
                  v-if="member.employee"
                  variant="ghost"
                  @click="removeMember(member)"
                >
                  Remove
                </Button>
              </li>
            </ul>
            <p
              v-else
              class="mt-2 text-sm text-ink-gray-6"
            >
              Nobody is assigned to this project yet.
            </p>

            <p
              v-if="memberError"
              class="surface-alert mt-3 p-3 text-sm"
              role="alert"
            >
              {{ memberError }}
            </p>

            <div class="mt-4">
              <FormControl
                v-model="memberQuery"
                type="text"
                label="Add a person"
                placeholder="Name, employee ID or work email"
                maxlength="60"
                data-testid="project-member-search"
                @focus="focusMemberSearch"
              />
              <div
                v-if="memberSearchActive"
                class="surface-card elev-1 mt-2 divide-y divide-outline-gray-1"
                data-testid="project-member-results"
              >
                <p
                  v-if="memberResults.loading"
                  class="p-2.5 text-sm text-ink-gray-6"
                >
                  Searching…
                </p>
                <ul
                  v-else-if="memberMatches.length"
                  class="divide-y divide-outline-gray-1"
                >
                  <li
                    v-for="person in memberMatches"
                    :key="person.name"
                  >
                    <button
                      type="button"
                      class="flex w-full min-w-0 items-center gap-3 p-2.5 text-left"
                      :disabled="setMembers.loading"
                      @click="addMember(person)"
                    >
                      <span
                        class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                        aria-hidden="true"
                      >{{ person.initials }}</span>
                      <span class="min-w-0 flex-1 truncate text-sm text-ink-gray-8">
                        {{ person.employee_name }}
                      </span>
                    </button>
                  </li>
                </ul>
                <p
                  v-else
                  class="p-2.5 text-sm text-ink-gray-6"
                >
                  Nobody matches that.
                </p>
              </div>
            </div>
          </section>
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
            v-if="!showCreateForm"
            variant="solid"
            theme="blue"
            @click="startCreate"
          >
            New project
          </Button>
        </template>
      </PageHeader>

      <div
        v-if="showCreateForm"
        class="surface-card elev-1 mb-4 space-y-3 p-4"
        data-testid="project-create-form"
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
            class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
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
            class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
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
        <div class="flex items-center gap-2">
          <Button
            variant="solid"
            theme="blue"
            :disabled="!creatorCompany"
            :loading="createProject.loading"
            @click="submitCreate"
          >
            Create
          </Button>
          <Button
            variant="ghost"
            @click="cancelCreate"
          >
            Cancel
          </Button>
        </div>
      </div>

      <div class="mb-4 lg:w-80">
        <FormControl
          v-model="query"
          type="text"
          label="Search"
          placeholder="Project name"
          maxlength="140"
        />
      </div>

      <AsyncState
        section="projects"
        :resource="projects"
        :empty="filteredRows.length === 0"
        :empty-title="emptyTitle"
        :empty-body="emptyBody"
        skeleton="row"
        :skeleton-rows="6"
      >
        <ul class="space-y-2">
          <li
            v-for="row in filteredRows"
            :key="row.name"
          >
            <button
              type="button"
              class="surface-card elev-1 flex w-full min-w-0 items-center gap-3 p-3 text-left"
              @click="openProject(row)"
            >
              <span class="min-w-0 flex-1">
                <span class="block truncate font-medium text-ink-gray-9">{{ row.project_name }}</span>
                <span class="block truncate text-sm text-ink-gray-6">
                  {{ [row.status, row.company].filter(Boolean).join(' · ') }}
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
