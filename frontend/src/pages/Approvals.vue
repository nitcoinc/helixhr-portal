<script setup>
import { ref, computed } from 'vue'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'

const me = createResource({
  url: 'hrms.api.get_current_employee_info',
  auto: true,
  onSuccess: () => {
    if (me.data?.name) {
      leaves.fetch()
      timesheets.fetch()
    }
  },
})

// get_leave_applications only filters by leave_approver when approver_id
// is actually passed -- omitting it would list every pending leave in
// the system, not just this manager's own reports (R26). employee is
// used the other direction, to exclude the manager's own leave from the
// list. The server still independently re-checks who may act in
// act_on_approval; this filter only decides what's shown.
const leaves = createResource({
  url: 'hrms.api.get_leave_applications',
  makeParams: () => ({ employee: me.data.name, approver_id: me.data.user_id, for_approval: 1 }),
  auto: false,
})

const timesheets = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'Timesheet',
    filters: { workflow_state: 'Pending Approval' },
    fields: ['name', 'employee', 'employee_name', 'start_date', 'end_date', 'total_hours'],
    limit_page_length: 0,
  }),
  auto: false,
})

const leaveRows = computed(() => leaves.data || [])
const timesheetRows = computed(() => timesheets.data || [])

const act = createResource({ url: 'helixhr.api.act_on_approval', method: 'POST' })
const rejecting = ref(null) // { doctype, name }
const rejectComment = ref('')
const error = ref('')

async function approve(doctype, name) {
  error.value = ''
  try {
    await act.submit({ doctype, name, action: 'Approve' })
    refresh()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not approve.'
  }
}

function openReject(doctype, name) {
  rejecting.value = { doctype, name }
  rejectComment.value = ''
}

async function confirmReject() {
  error.value = ''
  try {
    await act.submit({
      doctype: rejecting.value.doctype,
      name: rejecting.value.name,
      action: 'Reject',
      comment: rejectComment.value,
    })
    rejecting.value = null
    refresh()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not reject.'
  }
}

function refresh() {
  leaves.fetch()
  timesheets.fetch()
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Approvals" />

    <p
      v-if="error"
      class="mt-3 text-sm text-ink-red-4"
    >
      {{ error }}
    </p>

    <section
     
      data-testid="approvals-leave-section"
    >
      <h2 class="mb-2 text-sm font-medium text-ink-gray-6">
        Leave
      </h2>
      <p
        v-if="!leaves.loading && leaveRows.length === 0"
        class="text-ink-gray-5"
      >
        Nothing waiting on you.
      </p>
      <div
        v-for="row in leaveRows"
        :key="row.name"
        class="mb-2 rounded-lg border border-outline-gray-2 bg-surface-white p-3"
      >
        <p class="font-medium text-ink-gray-9">
          {{ row.employee_name }} · {{ row.leave_type }}
        </p>
        <p class="text-sm text-ink-gray-6">
          {{ row.from_date }}<span v-if="row.to_date !== row.from_date"> – {{ row.to_date }}</span>
          · {{ row.total_leave_days }} day{{ row.total_leave_days === 1 ? '' : 's' }}
        </p>
        <div class="mt-2 flex gap-2">
          <Button
            size="sm"
            variant="solid"
            theme="green"
            @click="approve('Leave Application', row.name)"
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            theme="red"
            @click="openReject('Leave Application', row.name)"
          >
            Reject
          </Button>
        </div>
      </div>
    </section>

    <section
     
      data-testid="approvals-timesheet-section"
    >
      <h2 class="mb-2 text-sm font-medium text-ink-gray-6">
        Timesheets
      </h2>
      <p
        v-if="!timesheets.loading && timesheetRows.length === 0"
        class="text-ink-gray-5"
      >
        Nothing waiting on you.
      </p>
      <div
        v-for="row in timesheetRows"
        :key="row.name"
        class="mb-2 rounded-lg border border-outline-gray-2 bg-surface-white p-3"
      >
        <p class="font-medium text-ink-gray-9">
          {{ row.employee_name }}
        </p>
        <p class="text-sm text-ink-gray-6">
          {{ row.start_date }} – {{ row.end_date }} · {{ row.total_hours }} hours
        </p>
        <div class="mt-2 flex gap-2">
          <Button
            size="sm"
            variant="solid"
            theme="green"
            @click="approve('Timesheet', row.name)"
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            theme="red"
            @click="openReject('Timesheet', row.name)"
          >
            Reject
          </Button>
        </div>
      </div>
    </section>

    <Dialog
      :model-value="!!rejecting"
      :options="{ title: 'Reject with a comment' }"
      @update:model-value="(v) => !v && (rejecting = null)"
    >
      <template #body-content>
        <FormControl
          v-model="rejectComment"
          type="textarea"
          label="Comment"
          required
        />
        <div class="mt-4 flex gap-2">
          <Button
            variant="solid"
            theme="red"
            :disabled="!rejectComment"
            :loading="act.loading"
            @click="confirmReject"
          >
            Reject
          </Button>
          <Button
            variant="subtle"
            @click="rejecting = null"
          >
            Cancel
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
