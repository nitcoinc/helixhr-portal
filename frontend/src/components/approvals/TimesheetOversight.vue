<script setup>
// Plan 2026-10-15-002 U6 (R6, R9-R12). "All pending timesheets": every
// Pending Approval and Pending HR timesheet in the caller's company, grouped
// by approver on the server, with one Send reminder per approver. HR Manager
// and HR User only; `get_pending_by_approver` and `send_timesheet_reminder`
// are the real gates.
import { computed, ref } from 'vue'
import { createResource, Button, Dialog } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import { session } from '@/lib/session'
import { formatDate, formatDateTime } from '@/lib/dates'
import { APPROVER_REASON_TEXT } from '@/lib/approvalLanes'

const overdueOnly = ref(false)
const pending = createResource({
  url: 'helixhr.api.get_pending_by_approver',
  makeParams: () => ({ kind: 'timesheet', overdue_only: overdueOnly.value ? 1 : 0 }),
  auto: true,
})
const groups = computed(() => pending.data?.groups || [])

function toggleOverdue() {
  overdueOnly.value = !overdueOnly.value
  pending.reload()
}

/** Per-group text under the button: why it is blocked, or when it frees up. */
function blockedText(group) {
  if (group.approver_role) return 'HR is the approver for these. No reminder.'
  if (group.reason && group.reason !== 'none') return APPROVER_REASON_TEXT[group.reason]
  if (group.next_reminder_at) return `Reminded recently. Next reminder allowed ${formatDateTime(group.next_reminder_at)}.`
  return ''
}
const canRemind = (group) => group.remindable && !group.next_reminder_at
const showsButton = (group) => !group.approver_role && (!group.reason || group.reason === 'none')
const statusId = (group) => `remind-status-${group.key.replace(/[^a-z0-9]/gi, '-')}`

// --- Send reminder ---------------------------------------------------------
const confirming = ref(null) // the group the dialog is about
const sending = ref(false)
const results = ref({}) // group key -> { ok, text }

const reminder = createResource({ url: 'helixhr.api.send_timesheet_reminder', method: 'POST' })

function askToRemind(group) {
  if (!canRemind(group)) return
  confirming.value = group
}

async function sendReminder() {
  const group = confirming.value
  if (!group || sending.value) return
  sending.value = true
  try {
    const result = await reminder.submit({ approver_user: group.approver_user })
    const next = result?.next_reminder_at ? ` Next reminder allowed ${formatDateTime(result.next_reminder_at)}.` : ''
    results.value = {
      ...results.value,
      [group.key]: { ok: !!result?.sent, text: `${result?.message || ''}${next}`.trim() },
    }
    confirming.value = null
    pending.reload()
  } catch (error) {
    results.value = {
      ...results.value,
      [group.key]: { ok: false, text: error?.messages?.[0] || "That reminder didn't send. Try again." },
    }
    confirming.value = null
    pending.reload()
  } finally {
    sending.value = false
  }
}

function rowAge(row) {
  if (row.since_unknown) return 'Since unknown'
  return `${row.age_days} ${row.age_days === 1 ? 'day' : 'days'}`
}
</script>

<template>
  <section
    aria-labelledby="oversight-heading"
    data-testid="timesheet-oversight"
  >
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <h2
        id="oversight-heading"
        class="text-base font-semibold text-ink-gray-9"
      >
        All pending timesheets
        <span
          v-if="pending.data?.count"
          class="tabular text-sm font-normal text-ink-gray-5"
        >{{ pending.data.count }}</span>
      </h2>
      <button
        type="button"
        class="min-h-11 rounded-full border px-4 text-sm font-medium"
        :class="
          overdueOnly
            ? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
            : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2'
        "
        :aria-pressed="overdueOnly"
        data-testid="oversight-overdue-toggle"
        @click="toggleOverdue"
      >
        Overdue only
      </button>
    </div>

    <AsyncState
      section="approvals-timesheet-oversight"
      :resource="pending"
      :empty="!groups.length"
      :empty-title="overdueOnly ? 'No overdue timesheets' : 'No timesheets waiting'"
      :empty-body="`Pending timesheets in ${session.employee?.company || 'your company'} appear here, grouped by approver.`"
      :skeleton-rows="3"
    >
      <p
        v-if="pending.data.total_is_capped"
        class="mb-3 text-sm text-ink-amber-3"
      >
        Showing the oldest 500. Narrow by approver.
      </p>
      <div class="space-y-4">
        <article
          v-for="group in groups"
          :key="group.key"
          class="surface-card elev-1 p-3"
          data-testid="oversight-group"
          :data-approver="group.approver_user || group.approver_name"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="font-medium text-ink-gray-9">
                {{ group.approver_name }}
                <span
                  v-if="group.is_you"
                  class="ml-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-bold text-ink-gray-7"
                >You</span>
              </p>
              <p class="tabular text-sm text-ink-gray-5">
                {{ group.count }} {{ group.count === 1 ? 'timesheet' : 'timesheets' }}
                <template v-if="group.oldest_days != null">
                  · oldest {{ group.oldest_days }} {{ group.oldest_days === 1 ? 'day' : 'days' }}
                </template>
              </p>
            </div>
            <Button
              v-if="showsButton(group)"
              variant="solid"
              theme="blue"
              class="min-h-11"
              :aria-disabled="!canRemind(group)"
              :aria-describedby="statusId(group)"
              :class="canRemind(group) ? '' : 'cursor-not-allowed opacity-60'"
              data-testid="send-reminder"
              @click="askToRemind(group)"
            >
              Send reminder
            </Button>
          </div>

          <p
            :id="statusId(group)"
            class="mt-1 text-sm"
            :class="results[group.key]?.ok === false ? 'text-ink-red-4' : 'text-ink-gray-6'"
            :role="results[group.key] ? 'status' : undefined"
            data-testid="reminder-status"
          >
            <template v-if="results[group.key]">
              {{ results[group.key].text }}
            </template>
            <template v-else>
              {{ blockedText(group) }}
            </template>
            <router-link
              v-if="group.reason && group.reason !== 'none' && !group.approver_role"
              to="/settings/approvers"
              class="ml-1 font-medium text-ink-gray-9 underline"
            >
              Settings → Approvers
            </router-link>
          </p>

          <ul class="mt-2 divide-y divide-outline-gray-2 border-t border-outline-gray-2">
            <li
              v-for="row in group.items"
              :key="row.name"
            >
              <router-link
                :to="{ name: 'ApprovalDetail', params: { kind: 'timesheet', name: row.name }, query: { view: 'timesheets' } }"
                class="flex min-h-11 items-center justify-between gap-3 py-2 text-sm hover:bg-surface-gray-2"
                data-testid="oversight-row"
                :data-name="row.name"
              >
                <span class="min-w-0 truncate">
                  <span class="font-medium text-ink-gray-9">{{ row.employee_name }}</span>
                  <span
                    v-if="row.date"
                    class="text-ink-gray-6"
                  > · week of {{ formatDate(row.date) }}</span>
                </span>
                <span class="tabular shrink-0 text-ink-gray-7">
                  {{ rowAge(row) }}
                  <span
                    v-if="row.overdue"
                    class="ml-1 rounded-full bg-surface-amber-1 px-2 text-xs font-medium text-ink-amber-3"
                  >Overdue</span>
                </span>
              </router-link>
            </li>
          </ul>
        </article>
      </div>
    </AsyncState>

    <!-- R9: a focus-trapped confirm that names the approver and the count. -->
    <Dialog
      :model-value="!!confirming"
      :options="{ title: 'Send a reminder?' }"
      @update:model-value="(value) => !value && !sending && (confirming = null)"
    >
      <template #body-content>
        <p
          v-if="confirming"
          class="text-sm text-ink-gray-7"
        >
          {{ confirming.approver_name }} gets one email and one portal notification listing
          {{ confirming.count }} pending {{ confirming.count === 1 ? 'timesheet' : 'timesheets' }}.
          Another reminder to them is possible after 24 hours.
        </p>
      </template>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button
            variant="subtle"
            :disabled="sending"
            @click="confirming = null"
          >
            Cancel
          </Button>
          <Button
            variant="solid"
            :loading="sending"
            data-testid="reminder-confirm"
            @click="sendReminder"
          >
            Send reminder
          </Button>
        </div>
      </template>
    </Dialog>
  </section>
</template>
