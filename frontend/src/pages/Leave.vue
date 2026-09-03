<script setup>
import { ref, computed } from 'vue'
import { createResource, Button, Badge, Dialog } from 'frappe-ui'
import LeaveForm from '@/components/LeaveForm.vue'

const me = createResource({
  url: 'hrms.api.get_current_employee_info',
  auto: true,
  onSuccess: () => {
    if (me.data?.name) {
      balances.fetch()
      applications.fetch()
    }
  },
})

const balances = createResource({
  url: 'hrms.api.get_leave_balance_map',
  auto: false,
})

const applications = createResource({
  url: 'hrms.api.get_leave_applications',
  makeParams: () => ({ employee: me.data.name }),
  auto: false,
  onSuccess: () => loadApproverNames(),
})

const balanceEntries = computed(() => Object.entries(balances.data || {}))

const FILTERS = [
  { label: 'All', value: 'all' },
  { label: 'Waiting', value: 'Open' },
  { label: 'Approved', value: 'Approved' },
  { label: 'Sent back', value: 'Rejected' },
]
const activeFilter = ref('all')

const filteredApplications = computed(() => {
  const list = applications.data || []
  if (activeFilter.value === 'all') return list
  return list.filter((a) => a.status === activeFilter.value)
})

function statusLabel(app) {
  if (app.status === 'Open') {
    return approverNames.value[app.leave_approver]
      ? `Waiting for ${approverNames.value[app.leave_approver]}`
      : 'Waiting'
  }
  if (app.status === 'Rejected') return 'Sent back'
  return app.status
}

// get_leave_applications returns leave_approver as a user id, not a
// display name -- resolve the small set of distinct approvers in one call
// rather than one lookup per row.
const approverNames = ref({})
const approverNamesResource = createResource({
  url: 'frappe.client.get_list',
  auto: false,
})

async function loadApproverNames() {
  const ids = [...new Set((applications.data || []).map((a) => a.leave_approver).filter(Boolean))]
  if (!ids.length) return
  const rows = await approverNamesResource.submit({
    doctype: 'User',
    filters: [['name', 'in', ids]],
    fields: ['name', 'full_name'],
    limit_page_length: 0,
  })
  approverNames.value = Object.fromEntries(rows.map((r) => [r.name, r.full_name]))
}

function statusTheme(app) {
  if (app.status === 'Approved') return 'green'
  if (app.status === 'Rejected') return 'red'
  return 'orange'
}

const showForm = ref(false)

function onApplied() {
  showForm.value = false
  applications.fetch()
  balances.fetch()
}

const withdrawing = ref(null)
const withdraw = createResource({
  url: 'frappe.client.delete',
  method: 'POST',
})

async function withdrawLeave(app) {
  withdrawing.value = app.name
  try {
    await withdraw.submit({ doctype: 'Leave Application', name: app.name })
    applications.fetch()
    balances.fetch()
  } finally {
    withdrawing.value = null
  }
}
</script>

<template>
  <div class="min-h-screen bg-surface-gray-1 pb-24">
    <header class="flex items-center justify-between border-b border-outline-gray-2 bg-surface-white px-4 py-4">
      <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
        Leave
      </h1>
      <Button
        variant="solid"
        theme="blue"
        @click="showForm = true"
      >
        Ask for leave
      </Button>
    </header>

    <div class="px-4 py-4">
      <div
        v-if="balanceEntries.length"
        class="flex flex-wrap gap-2"
      >
        <span
          v-for="[type, balance] in balanceEntries"
          :key="type"
          class="rounded-full bg-surface-blue-1 px-3 py-1 text-sm text-ink-blue-3"
        >
          {{ type }}: {{ balance.balance_leaves }}
        </span>
      </div>
      <p
        v-else-if="!balances.loading"
        class="text-sm text-ink-gray-5"
      >
        No leave set up yet.
      </p>
    </div>

    <div class="flex gap-2 px-4">
      <button
        v-for="filter in FILTERS"
        :key="filter.value"
        class="rounded-full px-3 py-1 text-sm"
        :class="
          activeFilter === filter.value
            ? 'bg-surface-gray-7 text-ink-white'
            : 'bg-surface-gray-2 text-ink-gray-7'
        "
        @click="activeFilter = filter.value"
      >
        {{ filter.label }}
      </button>
    </div>

    <div class="space-y-3 px-4 py-4">
      <p
        v-if="applications.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <p
        v-else-if="filteredApplications.length === 0"
        class="text-ink-gray-5"
      >
        You have no leave requests yet. Ask for leave to get started.
      </p>
      <div
        v-for="app in filteredApplications"
        :key="app.name"
        class="rounded-lg border border-outline-gray-2 bg-surface-white p-4"
      >
        <div class="flex items-start justify-between">
          <div>
            <p class="font-medium text-ink-gray-9">
              {{ app.leave_type }}
            </p>
            <p class="text-sm text-ink-gray-6">
              {{ app.from_date }}<span v-if="app.to_date !== app.from_date"> – {{ app.to_date }}</span>
              · {{ app.total_leave_days }} day{{ app.total_leave_days === 1 ? '' : 's' }}
            </p>
          </div>
          <Badge :theme="statusTheme(app)">
            {{ statusLabel(app) }}
          </Badge>
        </div>
        <p
          v-if="app.description"
          class="mt-2 text-sm text-ink-gray-6"
        >
          {{ app.description }}
        </p>
        <div
          v-if="app.status === 'Open'"
          class="mt-3"
        >
          <Button
            size="sm"
            variant="outline"
            theme="red"
            :loading="withdrawing === app.name"
            @click="withdrawLeave(app)"
          >
            Withdraw
          </Button>
        </div>
        <p
          v-else-if="app.status === 'Approved'"
          class="mt-2 text-sm text-ink-gray-5"
        >
          Need to cancel this? <router-link
            to="/requests"
            class="underline"
          >
            Ask HR
          </router-link>.
        </p>
      </div>
    </div>

    <Dialog
      v-model="showForm"
      :options="{ title: 'Ask for leave' }"
    >
      <template #body-content>
        <LeaveForm
          v-if="me.data?.name"
          :employee="me.data.name"
          @applied="onApplied"
          @cancel="showForm = false"
        />
      </template>
    </Dialog>
  </div>
</template>
