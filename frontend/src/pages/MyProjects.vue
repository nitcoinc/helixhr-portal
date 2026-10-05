<script setup>
import { computed, reactive } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { formatDate } from '@/lib/dates'

// Plan 2026-10-05-001 U6 / R10. The projects this employee belongs to, read
// from `get_my_project_overview` -- a sibling of `get_my_projects` so the
// timesheet dropdown's payload stays as it is. The server orders by my hours
// this month and returns only my own tasks and hours; no cost or billing.
const overview = createResource({
  url: 'helixhr.api.get_my_project_overview',
  auto: true,
})

const rows = computed(() => overview.data || [])

// Five tasks per card, then an "N more" expander.
const TASK_LIMIT = 5
const expanded = reactive({})
function visibleTasks(project) {
  return expanded[project.name] ? project.tasks : project.tasks.slice(0, TASK_LIMIT)
}

function hoursLabel(hours) {
  return `${Number(hours || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })} h`
}
</script>

<template>
  <div>
    <PageHeader title="My projects" />

    <AsyncState
      section="my-projects"
      :resource="overview"
      :empty="rows.length === 0"
      empty-title="You're not on any open projects"
      empty-body="A project's delivery manager or HR adds people to projects. Ask them if you should be on one."
      skeleton="block"
      skeleton-height="h-48"
    >
      <ul class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <li
          v-for="project in rows"
          :key="project.name"
          class="surface-card elev-1 p-4"
          data-testid="my-project-card"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h2 class="font-heading text-base font-bold text-ink-gray-9">
                {{ project.project_name || project.name }}
              </h2>
              <p class="text-sm text-ink-gray-6">
                {{ project.customer || 'No customer' }} · {{ project.status }}
              </p>
            </div>
            <div class="shrink-0 text-right">
              <p class="tabular font-medium text-ink-gray-9">
                {{ hoursLabel(project.hours_this_month) }}
              </p>
              <p class="text-sm text-ink-gray-6">
                this month
              </p>
            </div>
          </div>

          <h3 class="label mb-1 mt-4">
            My open tasks
          </h3>
          <p
            v-if="project.tasks.length === 0"
            class="text-sm text-ink-gray-6"
          >
            No open tasks
          </p>
          <ul
            v-else
            class="space-y-1"
          >
            <li
              v-for="task in visibleTasks(project)"
              :key="task.name"
              class="flex justify-between gap-3 text-sm"
            >
              <span class="text-ink-gray-8">{{ task.subject }}</span>
              <span class="shrink-0 text-ink-gray-6">
                {{ task.due ? `Due ${formatDate(task.due)}` : task.status }}
              </span>
            </li>
          </ul>
          <button
            v-if="project.tasks.length > TASK_LIMIT"
            type="button"
            class="mt-2 min-h-11 cursor-pointer text-sm font-medium text-ink-gray-8 underline"
            :aria-expanded="!!expanded[project.name]"
            @click="expanded[project.name] = !expanded[project.name]"
          >
            {{ expanded[project.name] ? 'Show fewer' : `${project.tasks.length - TASK_LIMIT} more` }}
          </button>
        </li>
      </ul>
    </AsyncState>
  </div>
</template>
