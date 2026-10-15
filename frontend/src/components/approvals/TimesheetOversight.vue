<script setup>
// Plan 2026-10-15-002 U6: the All pending timesheets view with Send reminder.
// Shows timesheets grouped by approver, with the option to send a reminder.
import { computed, ref } from 'vue'
import { call } from '@/lib/api'
import { session } from '@/lib/session'
import { groupByApprover } from '@/lib/approvalLanes'

const props = defineProps({
  company: { type: String, default: '' },
})

const resource = ref(null)
const loading = ref(false)
const error = ref('')
const groups = ref([])
const totalCount = ref(0)
const overdueOnly = ref(false)

const isHrManager = computed(() => session.canConfigure)
const isHrUser = computed(() => session.canOverseeTimesheets && !session.canConfigure)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await call('helixhr.api.get_pending_by_approver', {
      kind: 'timesheet',
      overdue_only: overdueOnly.value ? 1 : 0,
    })
    groups.value = groupByApprover(data.groups?.flatMap((g) => g.items) || [])
    totalCount.value = data.count || 0
  } catch (failure) {
    error.value = failure?.messages?.[0] || "Could not load pending timesheets."
    groups.value = []
  } finally {
    loading.value = false
  }
}

// Send reminder state.
const reminding = ref('')
const remindResult = ref('')
const remindError = ref('')

async function sendReminder(approverUser) {
  reminding.value = approverUser
  remindResult.value = ''
  remindError.value = ''
  try {
    const result = await call('helixhr.api.send_timesheet_reminder', {
      approver_user: approverUser,
    })
    if (result.sent) {
      remindResult.value = result.message
      if (result.next_reminder_at) remindResult.value += ` Next reminder allowed at: ${result.next_reminder_at}`
    } else {
      remindResult.value = result.message || 'Reminder could not be sent.'
    }
  } catch (failure) {
    remindError.value = failure?.messages?.[0] || "That reminder didn't send. Try again."
  } finally {
    reminding.value = ''
  }
}

function toggleOverdue() {
  overdueOnly.value = !overdueOnly.value
  load()
}

const LABEL_CLASS = 'mb-1 block text-sm text-ink-gray-7'
</script>

<template>
  <div>
    <div class="mb-3 flex items-center gap-3">
      <h3 class="text-sm font-medium text-ink-gray-9">
        All pending timesheets
        <span v-if="totalCount">({{ totalCount }})</span>
      </h3>
      <label class="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          :checked="overdueOnly"
          class="mr-1.5 inline-flex accent-signal-3"
          @change="toggleOverdue"
        >
        Overdue only
      </label>
      <button
        type="button"
        class="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2"
        @click="load"
      >
        Refresh
      </button>
    </div>

    <div
      v-if="loading"
      class="surface-inset p-4 text-sm text-ink-gray-7"
    >
      Loading…
    </div>

    <div
      v-else-if="error"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </div>

    <div
      v-else-if="!groups.length"
      class="surface-inset p-4 text-sm text-ink-gray-7"
    >
      No timesheets pending.
    </div>

    <div
      v-else
      class="space-y-3"
    >
      <div
        v-for="group in groups"
        :key="group.approver_user || 'unknown'"
        class="rounded-md border border-outline-gray-2 bg-surface-card p-3"
      >
        <div class="flex items-center justify-between gap-3">
          <div>
            <span class="font-medium text-ink-gray-9">{{ group.approver_name }}</span>
            <span class="text-ink-gray-6"> · {{ group.count }} items · oldest {{ group.oldest_days }} days</span>
          </div>
          <button
            v-if="isHrManager || isHrUser"
            type="button"
            class="inline-flex min-h-11 cursor-pointer items-center rounded-lg bg-signal px-3 text-sm font-medium text-white disabled:opacity-50"
            :disabled="reminding === group.approver_user"
            :aria-describedby="`remind-${group.approver_user}-status`"
            @click="sendReminder(group.approver_user)"
          >
            {{ reminding === group.approver_user ? 'Sending…' : 'Send reminder' }}
          </button>
        </div>
        <p
          v-if="remindResult && !remindError"
          :id="`remind-${group.approver_user}-status`"
          class="mt-1 text-xs text-ink-green-6"
        >
          {{ remindResult }}
        </p>
        <p
          v-if="remindError"
          :id="`remind-${group.approver_user}-status`"
          class="mt-1 text-xs text-ink-red-4"
        >
          {{ remindError }}
        </p>

        <div class="mt-2 grid gap-2 text-sm">
          <div
            v-for="item in group.rows.slice(0, 10)"
            :key="item.name"
            class="flex items-center justify-between"
          >
            <div>
              <span class="font-medium text-ink-gray-9">{{ item.employee_name }}</span>
              <span class="text-ink-gray-6"> · {{ item.age_days }}d</span>
              <span
                v-if="item.overdue"
                class="inline-flex rounded-full bg-surface-amber-1 px-2 text-xs text-ink-amber-6"
              >Overdue</span>
            </div>
          </div>
          <p
            v-if="group.rows.length > 10"
            class="text-xs text-ink-gray-6"
          >
            And {{ group.rows.length - 10 }} more…
          </p>
        </div>
      </div>
    </div>

    <div
      v-if="isHrUser"
      class="surface-inset mt-4 p-3 text-sm text-ink-gray-7"
    >
      View only: you can't approve timesheets.
    </div>
  </div>
</template>