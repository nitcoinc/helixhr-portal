<script setup>
import { computed, reactive, ref } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { formatDate } from '@/lib/dates'
import { splitByActivity } from '@/lib/lists'

// Plan 2026-10-05-001 U6 / R10. The projects this employee belongs to, read
// from `get_my_project_overview` -- a sibling of `get_my_projects` so the
// timesheet dropdown's payload stays as it is. The server orders by my hours
// this month and returns only my own tasks and hours; no cost or billing.
const overview = createResource({
  url: 'helixhr.api.get_my_project_overview',
  auto: true,
})

const rows = computed(() => overview.data || [])

// EP-23. The projects with my hours or open tasks this month are the cards;
// the rest -- usually most of them -- fold into "Other projects" as one-line
// rows. Open by default only when nothing is active, because then the rest
// *is* the page. `null` means "not touched", so a tap wins over the default.
const split = computed(() => splitByActivity(rows.value))
const otherToggled = ref(null)
const otherOpen = computed(() => otherToggled.value ?? split.value.active.length === 0)

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
      <ul
        v-if="split.active.length"
        class="grid grid-cols-1 gap-4 lg:grid-cols-2"
      >
        <li
          v-for="project in split.active"
          :key="project.name"
          class="surface-card elev-1 p-4"
          data-testid="my-project-card"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h2 class="break-words font-heading text-base font-bold text-ink-gray-9">
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

          <!-- EP-23: no "My open tasks / No open tasks" block on a card that
               is here for its hours -- an empty heading is height, not news. -->
          <h3
            v-if="project.tasks.length"
            class="label mb-1 mt-4"
          >
            My open tasks
          </h3>
          <ul
            v-if="project.tasks.length"
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

      <div
        v-if="split.other.length"
        :class="split.active.length ? 'mt-6' : ''"
      >
        <!-- The Approvals "Decided" disclosure: a real button that says
             whether it is open and which list it opens. -->
        <button
          type="button"
          class="flex min-h-11 w-full cursor-pointer items-center gap-2 text-left"
          :aria-expanded="otherOpen ? 'true' : 'false'"
          aria-controls="my-projects-other"
          @click="otherToggled = !otherOpen"
        >
          <Icon
            name="chevronRight"
            class="shrink-0 text-ink-gray-5 transition-transform duration-200"
            :class="otherOpen ? 'rotate-90' : ''"
          />
          <span class="label">Other projects (<span class="tabular">{{ split.other.length }}</span>)</span>
        </button>
        <ul
          v-show="otherOpen"
          id="my-projects-other"
          class="mt-2 space-y-2 lg:grid lg:grid-cols-2 lg:gap-2 lg:space-y-0"
        >
          <li
            v-for="project in split.other"
            :key="project.name"
            class="surface-card elev-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-4 py-3"
            data-testid="my-project-row"
          >
            <span class="min-w-0 break-words font-medium text-ink-gray-9">
              {{ project.project_name || project.name }}
            </span>
            <span class="text-sm text-ink-gray-6">
              {{ project.customer || 'No customer' }} · {{ project.status }}
            </span>
          </li>
        </ul>
      </div>
    </AsyncState>
  </div>
</template>
