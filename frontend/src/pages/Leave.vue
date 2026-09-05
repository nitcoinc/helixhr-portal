<script setup>
import { ref, computed } from 'vue'
import { createResource, Button, Dialog } from 'frappe-ui'
import LeaveForm from '@/components/LeaveForm.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { session } from '@/lib/session'
import { formatDateRange, isCalendarDate } from '@/lib/dates'

// P2-U3 / P2-R21. Identity from the one bootstrap, not a page-local copy of
// `hrms.api.get_current_employee_info`.
const employeeId = computed(() => session.employee?.name)

const balances = createResource({
  url: 'hrms.api.get_leave_balance_map',
  auto: true,
})

const applications = createResource({
  url: 'hrms.api.get_leave_applications',
  makeParams: () => ({ employee: employeeId.value }),
  auto: true,
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

const rows = computed(() => applications.data || [])
const filteredApplications = computed(() => {
  if (activeFilter.value === 'all') return rows.value
  return rows.value.filter((a) => a.status === activeFilter.value)
})

// get_leave_applications returns leave_approver as a user id, not a
// display name -- resolve the small set of distinct approvers in one call
// rather than one lookup per row.
const approverNames = ref({})
const approverNamesResource = createResource({
  url: 'frappe.client.get_list',
  auto: false,
})

async function loadApproverNames() {
  const ids = [...new Set(rows.value.map((a) => a.leave_approver).filter(Boolean))]
  if (!ids.length) return
  const list = await approverNamesResource.submit({
    doctype: 'User',
    filters: [['name', 'in', ids]],
    fields: ['name', 'full_name'],
    limit_page_length: 0,
  })
  approverNames.value = Object.fromEntries(list.map((r) => [r.name, r.full_name]))
}

/** The approver's *first* name, which is what the canvas puts on the badge
 * ("Waiting for Priya"). A full name turns a 92px pill into a two-line block
 * at 360px. */
function approverFirstName(app) {
  const full = approverNames.value[app.leave_approver]
  return full ? full.split(/\s+/)[0] : ''
}

// The date tile (index.css, `.date-tile`). Parsed straight off the
// date-only string rather than through a Date object: `new Date('2026-09-14')`
// is midnight UTC and renders as the 13th west of Greenwich, which is exactly
// the class of bug P2-R5 and P2-AE3 exist to prevent.
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
function tile(value) {
  if (!isCalendarDate(value)) return null
  const [, month, day] = value.split('-')
  return { month: MONTHS[Number(month) - 1], day: String(Number(day)) }
}

const showForm = ref(false)

function onApplied() {
  showForm.value = false
  applications.reload()
  balances.reload()
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
    applications.reload()
    balances.reload()
  } finally {
    withdrawing.value = null
  }
}
</script>

<template>
  <div>
    <PageHeader title="Leave">
      <template #actions>
        <Button
          variant="solid"
          theme="blue"
          @click="showForm = true"
        >
          Ask for leave
        </Button>
      </template>
    </PageHeader>

    <!-- The anchored region: what you have left, on the field, with a bar per
         type. It is the one place on this page the signal yellow is legal. -->
    <AsyncState
      section="leave-balances"
      class="mb-6"
      :resource="balances"
      :empty="balanceEntries.length === 0"
      empty-title="No leave allocated yet"
      empty-body="HR sets your leave allocation each year. Ask HR if you think this is wrong."
      skeleton="field"
      skeleton-height="h-36"
    >
      <section
        class="surface-field elev-2 space-y-3 p-4"
        aria-label="Leave balances"
      >
        <div
          v-for="[type, balance] in balanceEntries"
          :key="type"
        >
          <p class="flex items-baseline justify-between gap-3 text-sm">
            <span class="truncate text-white">{{ type }}</span>
            <span class="shrink-0 text-blue-200">
              <span class="tabular font-heading text-base font-bold text-white">
                {{ balance.balance_leaves }}
              </span>
              of <span class="tabular">{{ balance.allocated_leaves }}</span> left
            </span>
          </p>
          <!-- The bar is a second reading of the same number, not the only
               reading of it -- the figure above it is always present, so the
               bar carries no meaning of its own (WCAG 1.4.1). -->
          <div
            class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-field-deep"
            aria-hidden="true"
          >
            <div
              class="h-full rounded-full bg-signal"
              :style="{
                width: `${
                  balance.allocated_leaves
                    ? Math.max(0, Math.min(100, (balance.balance_leaves / balance.allocated_leaves) * 100))
                    : 0
                }%`,
              }"
            />
          </div>
        </div>
      </section>
    </AsyncState>

    <div class="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
      <button
        v-for="filter in FILTERS"
        :key="filter.value"
        class="min-h-11 shrink-0 cursor-pointer whitespace-nowrap rounded-full px-4 text-sm font-medium transition-colors duration-200"
        :class="
          activeFilter === filter.value
            ? 'bg-blue-700 text-white'
            : 'bg-surface-gray-2 text-ink-gray-7 hover:bg-surface-gray-3'
        "
        :aria-pressed="activeFilter === filter.value"
        @click="activeFilter = filter.value"
      >
        {{ filter.label }}
      </button>
    </div>

    <AsyncState
      section="leave-list"
      :resource="applications"
      :empty="filteredApplications.length === 0"
      :empty-title="activeFilter === 'all' ? 'No leave requests yet' : 'Nothing in this list'"
      :empty-body="
        activeFilter === 'all'
          ? 'Ask for leave to get started.'
          : 'Try another filter to see the rest of your leave.'
      "
      :skeleton-rows="3"
    >
      <template #empty-action>
        <!-- Deliberately not the header's wording: with an empty list both
             buttons are on screen at once, and two controls with the same
             name 200px apart is a duplicate, not an affordance. -->
        <Button
          v-if="activeFilter === 'all'"
          variant="solid"
          theme="blue"
          @click="showForm = true"
        >
          Ask for your first leave
        </Button>
      </template>

      <ul class="space-y-2">
        <li
          v-for="app in filteredApplications"
          :key="app.name"
          class="surface-card elev-1 flex gap-3 p-3"
        >
          <span
            v-if="tile(app.from_date)"
            class="date-tile mt-0.5"
            aria-hidden="true"
          >
            <span class="date-tile-month">{{ tile(app.from_date).month }}</span>
            <span class="date-tile-day">{{ tile(app.from_date).day }}</span>
          </span>

          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-start justify-between gap-2">
              <p class="font-medium text-ink-gray-9">
                {{ app.leave_type }}
              </p>
              <StatusBadge
                kind="leave"
                :status="app.status"
                :approver="approverFirstName(app)"
              />
            </div>
            <p class="mt-0.5 text-sm text-ink-gray-6">
              {{ formatDateRange(app.from_date, app.to_date) }}
              · <span class="tabular">{{ app.total_leave_days }}</span>
              day{{ app.total_leave_days === 1 ? '' : 's' }}
            </p>
            <p
              v-if="app.description"
              class="surface-inset mt-2 p-2 text-sm text-ink-gray-7"
            >
              {{ app.description }}
            </p>
            <div
              v-if="app.status === 'Open'"
              class="mt-2"
            >
              <Button
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
              Need to cancel this?
              <router-link
                to="/requests"
                class="cursor-pointer text-blue-700 underline underline-offset-2"
              >
                Ask HR
              </router-link>.
            </p>
          </div>
        </li>
      </ul>
    </AsyncState>

    <Dialog
      v-model="showForm"
      :options="{ title: 'Ask for leave' }"
    >
      <template #body-content>
        <LeaveForm
          v-if="employeeId"
          :employee="employeeId"
          @applied="onApplied"
          @cancel="showForm = false"
        />
      </template>
    </Dialog>
  </div>
</template>
