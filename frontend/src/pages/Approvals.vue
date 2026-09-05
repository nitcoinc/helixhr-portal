<script setup>
import { ref, computed } from 'vue'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { session } from '@/lib/session'
import { formatDateRange } from '@/lib/dates'

// P2-U3 / P2-R21. Identity from the one bootstrap, not a page-local copy of
// `hrms.api.get_current_employee_info`.
const me = computed(() => session.employee || {})

// get_leave_applications only filters by leave_approver when approver_id
// is actually passed -- omitting it would list every pending leave in
// the system, not just this manager's own reports (R26). employee is
// used the other direction, to exclude the manager's own leave from the
// list. The server still independently re-checks who may act in
// act_on_approval; this filter only decides what's shown.
const leaves = createResource({
  url: 'hrms.api.get_leave_applications',
  makeParams: () => ({
    employee: me.value.name,
    approver_id: me.value.user_id,
    for_approval: 1,
  }),
  auto: true,
})

const timesheets = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'Timesheet',
    filters: { workflow_state: 'Pending Approval' },
    fields: ['name', 'employee', 'employee_name', 'start_date', 'end_date', 'total_hours'],
    limit_page_length: 0,
  }),
  auto: true,
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
  leaves.reload()
  timesheets.reload()
}
</script>

<template>
  <div>
    <PageHeader title="Approvals" />

    <p
      v-if="error"
      class="surface-alert mb-4 p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </p>

    <section
      class="mb-6"
      data-testid="approvals-leave-section"
      aria-labelledby="approvals-leave-heading"
    >
      <h2
        id="approvals-leave-heading"
        class="label mb-2"
      >
        Leave
      </h2>
      <AsyncState
        section="approvals-leave"
        :resource="leaves"
        :empty="leaveRows.length === 0"
        empty-title="Nothing waiting on you"
        empty-body="Leave your team sends for approval appears here."
        :skeleton-rows="2"
      >
        <ul class="space-y-2">
          <li
            v-for="row in leaveRows"
            :key="row.name"
            class="surface-card elev-1 p-3"
          >
            <p class="font-medium text-ink-gray-9">
              {{ row.employee_name }} · {{ row.leave_type }}
            </p>
            <p class="mt-0.5 text-sm text-ink-gray-6">
              {{ formatDateRange(row.from_date, row.to_date) }}
              · <span class="tabular">{{ row.total_leave_days }}</span>
              day{{ row.total_leave_days === 1 ? '' : 's' }}
            </p>
            <div class="mt-3 flex gap-2">
              <Button
                variant="solid"
                theme="green"
                @click="approve('Leave Application', row.name)"
              >
                Approve
              </Button>
              <Button
                variant="outline"
                theme="red"
                @click="openReject('Leave Application', row.name)"
              >
                Send back
              </Button>
            </div>
          </li>
        </ul>
      </AsyncState>
    </section>

    <section
      data-testid="approvals-timesheet-section"
      aria-labelledby="approvals-timesheet-heading"
    >
      <h2
        id="approvals-timesheet-heading"
        class="label mb-2"
      >
        Timesheets
      </h2>
      <AsyncState
        section="approvals-timesheet"
        :resource="timesheets"
        :empty="timesheetRows.length === 0"
        empty-title="Nothing waiting on you"
        empty-body="Weeks your team sends for approval appear here."
        :skeleton-rows="2"
      >
        <ul class="space-y-2">
          <li
            v-for="row in timesheetRows"
            :key="row.name"
            class="surface-card elev-1 p-3"
          >
            <p class="font-medium text-ink-gray-9">
              {{ row.employee_name }}
            </p>
            <p class="mt-0.5 text-sm text-ink-gray-6">
              {{ formatDateRange(row.start_date, row.end_date) }}
              · <span class="tabular">{{ row.total_hours }}</span> hours
            </p>
            <div class="mt-3 flex gap-2">
              <Button
                variant="solid"
                theme="green"
                @click="approve('Timesheet', row.name)"
              >
                Approve
              </Button>
              <Button
                variant="outline"
                theme="red"
                @click="openReject('Timesheet', row.name)"
              >
                Send back
              </Button>
            </div>
          </li>
        </ul>
      </AsyncState>
    </section>

    <!-- A phone sheet, a bounded dialog on a desktop: one overlay, shaped by
         index.css rather than by a second component (P2-R6). -->
    <Dialog
      :model-value="!!rejecting"
      :options="{ title: 'Send this back' }"
      @update:model-value="(v) => !v && (rejecting = null)"
    >
      <template #body-content>
        <FormControl
          v-model="rejectComment"
          type="textarea"
          label="Why are you sending it back?"
          required
        />
        <p class="mt-1 text-sm text-ink-gray-6">
          The person who sent it sees this, so say what to change.
        </p>
        <div class="mt-4 flex gap-2">
          <Button
            variant="solid"
            theme="red"
            :disabled="!rejectComment"
            :loading="act.loading"
            @click="confirmReject"
          >
            Send back
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
