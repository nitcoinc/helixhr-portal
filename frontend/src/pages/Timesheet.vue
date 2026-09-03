<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Button, Badge } from 'frappe-ui'
import WeekGrid from '@/components/WeekGrid.vue'
import PageHeader from '@/components/PageHeader.vue'

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

const badgeTheme = computed(() => {
  if (workflowState.value === 'Approved') return 'green'
  if (workflowState.value === 'Rejected') return 'red'
  if (workflowState.value === 'Pending Approval') return 'orange'
  return 'gray'
})
const badgeLabel = computed(() => {
  if (workflowState.value === 'Pending Approval') return 'Waiting for manager'
  if (workflowState.value === 'Rejected') return 'Sent back'
  return workflowState.value
})

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

const rejectionComment = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'Comment',
    filters: [
      ['reference_doctype', '=', 'Timesheet'],
      ['reference_name', '=', week.data.timesheet.name],
      ['comment_type', '=', 'Comment'],
    ],
    fields: ['content'],
    order_by: 'creation desc',
    limit_page_length: 1,
  }),
  auto: false,
  transform: (rows) => rows?.[0]?.content,
})

watch(workflowState, (state) => {
  if (state === 'Rejected' && week.data?.timesheet?.name) rejectionComment.fetch()
})

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
          class="text-sm text-ink-gray-6 underline"
        >
          Past timesheets
        </router-link>
      </template>
    </PageHeader>

    <div class="flex items-center justify-between">
      <button
        class="rounded-md px-2 py-1 text-ink-gray-6 hover:bg-surface-gray-2"
        @click="prevWeek"
      >
        ‹
      </button>
      <div class="flex items-center gap-2">
        <span class="font-medium text-ink-gray-9">{{ weekStart }} – {{ week.data?.week_end }}</span>
        <button
          class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-6"
          @click="thisWeek"
        >
          This week
        </button>
        <Badge
          v-if="workflowState"
          :theme="badgeTheme"
        >
          {{ badgeLabel }}
        </Badge>
      </div>
      <button
        class="rounded-md px-2 py-1 text-ink-gray-6 hover:bg-surface-gray-2"
        @click="nextWeek"
      >
        ›
      </button>
    </div>

    <div
      v-if="workflowState === 'Rejected'"
      class="mb-4 rounded-lg border border-outline-red-2 bg-surface-red-1 p-3 text-sm text-ink-red-4"
    >
      This week was sent back<span v-if="rejectionComment.data">: "{{ rejectionComment.data }}"</span>.
      Fix it up and resubmit.
    </div>

    <div>
      <WeekGrid
        :rows="rows"
        :projects="projects.data || []"
        :week-dates="weekDates"
        :read-only="isReadOnly"
        @add-row="addRow"
        @remove-row="removeRow"
      />
    </div>

    <p
      v-if="error"
      class="mt-3 text-sm text-ink-red-4"
    >
      {{ error }}
    </p>

    <div class="sticky bottom-0 mt-4 flex gap-2 border-t border-outline-gray-2 bg-surface-white px-4 py-3">
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
