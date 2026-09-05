<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Button } from 'frappe-ui'
import WeekGrid from '@/components/WeekGrid.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'

function pad(n) {
  return String(n).padStart(2, '0')
}
function isoDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
function mondayOf(date) {
  const d = new Date(date)
  const day = (d.getDay() + 6) % 7 // Monday = 0
  d.setDate(d.getDate() - day)
  return d
}

const weekStart = ref(isoDate(mondayOf(new Date())))

const weekDates = computed(() => {
  const monday = new Date(weekStart.value)
  const labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  return labels.map((label, i) => {
    const d = new Date(monday)
    d.setDate(d.getDate() + i)
    return { label: `${label} ${d.getDate()}`, iso: isoDate(d) }
  })
})

const week = createResource({
  url: 'helixhr.api.get_my_week',
  makeParams: () => ({ week_start: weekStart.value }),
  auto: true,
})

const projects = createResource({
  url: 'helixhr.api.get_my_projects',
  auto: true,
})

const rows = ref([])
const error = ref('')

function loadRowsFromServer() {
  const server = week.data?.timesheet?.rows
  rows.value = server?.length
    ? server.map((r) => ({ ...r }))
    : [{ date: weekDates.value[0].iso, project: '', task: '', hours: '', note: '' }]
}
watch(() => week.data, loadRowsFromServer)

function addRow() {
  rows.value.push({ date: weekDates.value[0].iso, project: '', task: '', hours: '', note: '' })
}
function removeRow(index) {
  rows.value.splice(index, 1)
}

const workflowState = computed(() => week.data?.timesheet?.workflow_state)
const isReadOnly = computed(() => workflowState.value && workflowState.value !== 'Draft' && workflowState.value !== 'Rejected')

function prevWeek() {
  const d = new Date(weekStart.value)
  d.setDate(d.getDate() - 7)
  weekStart.value = isoDate(d)
}
function nextWeek() {
  const d = new Date(weekStart.value)
  d.setDate(d.getDate() + 7)
  weekStart.value = isoDate(d)
}
function thisWeek() {
  weekStart.value = isoDate(mondayOf(new Date()))
}

// The manager's reason now arrives with the week itself. It used to be read
// here with `frappe.client.get_list` on Comment, which the Employee Self
// Service role cannot read: the call 403'd every time, so the page told the
// employee their week was sent back and never told them why.
const rejectionComment = computed(() => week.data?.timesheet?.rejection_comment)

const save = createResource({ url: 'helixhr.api.save_my_week', method: 'POST' })
const submit = createResource({ url: 'frappe.model.workflow.apply_workflow', method: 'POST' })

async function saveDraft() {
  error.value = ''
  try {
    await save.submit({ week_start: weekStart.value, rows: JSON.stringify(rows.value) })
    await week.reload()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not save. Please check your rows.'
  }
}

async function submitWeek() {
  error.value = ''
  try {
    await saveDraft()
    if (!week.data?.timesheet?.name) return
    await submit.submit({ doc: JSON.stringify({ doctype: 'Timesheet', name: week.data.timesheet.name }), action: 'Submit' })
    await week.reload()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not submit this week.'
  }
}

async function editAndResubmit() {
  error.value = ''
  try {
    await submit.submit({ doc: JSON.stringify({ doctype: 'Timesheet', name: week.data.timesheet.name }), action: 'Edit' })
    await week.reload()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not reopen this week.'
  }
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Timesheet">
      <template #actions>
        <router-link
          to="/timesheet/history"
          class="-my-2 inline-flex min-h-11 cursor-pointer items-center text-sm text-ink-gray-6 underline underline-offset-2 hover:text-ink-gray-9"
        >
          Past timesheets
        </router-link>
      </template>
    </PageHeader>

    <div class="flex items-center justify-between">
      <button
        class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-gray-6 hover:bg-surface-gray-2"
        aria-label="Previous week"
        @click="prevWeek"
      >
        <Icon name="chevronLeft" />
      </button>
      <div class="flex items-center gap-2">
        <span class="tabular font-medium text-ink-gray-9">{{ weekStart }} – {{ week.data?.week_end }}</span>
        <button
          class="cursor-pointer rounded-full bg-surface-gray-2 px-3 py-1.5 text-xs text-ink-gray-7 hover:bg-surface-gray-3"
          @click="thisWeek"
        >
          This week
        </button>
        <StatusBadge
          v-if="workflowState"
          kind="timesheet"
          :status="workflowState"
        />
      </div>
      <button
        class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-gray-6 hover:bg-surface-gray-2"
        aria-label="Next week"
        @click="nextWeek"
      >
        <Icon name="chevronRight" />
      </button>
    </div>

    <div
      v-if="workflowState === 'Rejected'"
      class="surface-alert mb-4 p-3 text-sm"
      role="alert"
    >
      This week was sent back<span v-if="rejectionComment">: “{{ rejectionComment }}”</span>.
      Fix it up and resubmit.
    </div>

    <AsyncState
      section="timesheet-week"
      :resource="week"
      :empty="false"
      skeleton="block"
      skeleton-height="h-72"
    >
      <WeekGrid
        :rows="rows"
        :projects="projects.data || []"
        :week-dates="weekDates"
        :read-only="isReadOnly"
        @add-row="addRow"
        @remove-row="removeRow"
      />
    </AsyncState>

    <p
      v-if="error"
      class="surface-alert mt-3 p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </p>

    <!-- `.action-bar` (index.css) sits above the phone tab bar and inside the
         safe area. This bar used to be `sticky bottom-0`, which put Submit
         *underneath* the fixed tab bar at 360px -- the primary action on the
         page was unreachable without scrolling past the end of the document
         (P2-U3 scenario 2). -->
    <div class="action-bar mt-4 flex gap-2">
      <template v-if="workflowState === 'Rejected'">
        <Button
          variant="solid"
          theme="blue"
          @click="editAndResubmit"
        >
          Edit and resubmit
        </Button>
      </template>
      <template v-else-if="!isReadOnly">
        <Button
          variant="subtle"
          :loading="save.loading"
          @click="saveDraft"
        >
          Save draft
        </Button>
        <Button
          variant="solid"
          theme="blue"
          :loading="submit.loading"
          @click="submitWeek"
        >
          Submit
        </Button>
      </template>
    </div>
  </div>
</template>
