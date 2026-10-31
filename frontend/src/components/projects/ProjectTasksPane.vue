<script setup>
// Plan 2026-10-31-001 U3 / R4. A project's open tasks: search, show more,
// rename, close, and "paste several" (one `add_tasks` call, all or none).
// The parent owns `get_project`; this pane hands back the changed list as
// `update:tasks`. A write that lands after the route moved to another
// project is dropped, so it never lands on the wrong one.
import { computed, ref, toRef, watch } from 'vue'
import { createResource, Button } from 'frappe-ui'
import SearchField from '@/components/SearchField.vue'
import { TASK_PASTE_LIMIT, parseTaskLines } from '@/lib/projects'
import { useProjectPane } from '@/lib/useProjectPane'

const props = defineProps({
  project: { type: String, required: true },
  /** `get_project().tasks`: the open tasks. */
  tasks: { type: Array, default: () => [] },
  canEdit: { type: Boolean, default: false },
})
const emit = defineEmits(['update:tasks'])

const pane = useProjectPane(toRef(props, 'tasks'), 'subject')
const { query, shown, more, showMore } = pane

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
  const project = props.project
  try {
    const response = await addTasks.submit({ project, subjects })
    if (props.project !== project) return
    // Newest first, so what was just added is in view however long the
    // list is; a reload shows the server's own order.
    emit('update:tasks', [...(response?.tasks || []), ...props.tasks])
    newTasks.value = ''
    query.value = ''
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
    if (props.project !== project) return
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
  const project = props.project
  try {
    const updated = await saveTask.submit({ project, task: task.name, subject })
    if (props.project !== project) return
    emit(
      'update:tasks',
      props.tasks.map((row) => (row.name === task.name ? { ...row, ...updated } : row)),
    )
  } catch (error) {
    if (props.project !== project) return
    taskError.value = error?.messages?.[0] || 'Could not rename that task. Please try again.'
  }
}

async function closeTask(task) {
  taskError.value = ''
  const project = props.project
  try {
    await saveTask.submit({ project, task: task.name, status: 'Completed' })
    if (props.project !== project) return
    // `get_project` only ever returns open tasks (_project_open_tasks), so a
    // closed task's row is removed locally rather than updated in place.
    emit(
      'update:tasks',
      props.tasks.filter((row) => row.name !== task.name),
    )
  } catch (error) {
    if (props.project !== project) return
    taskError.value = error?.messages?.[0] || 'Could not close that task. Please try again.'
  }
}

watch(
  () => props.project,
  () => {
    pane.reset()
    newTasks.value = ''
    taskError.value = ''
    taskNotice.value = ''
    editingTask.value = null
  },
)
</script>

<template>
  <section
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
        v-model="query"
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
      {{ pane.countLine(tasks.length === 1 ? 'task' : 'tasks') }}
    </p>

    <ul
      v-if="shown.length"
      class="divide-y divide-outline-gray-1"
    >
      <li
        v-for="task in shown"
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
      {{ tasks.length ? `No tasks match “${query.trim()}”.` : 'No open tasks yet.' }}
    </p>

    <div
      v-if="more > 0"
      class="mt-2 text-center"
    >
      <Button
        variant="ghost"
        @click="showMore"
      >
        Show {{ more }} more
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
</template>
