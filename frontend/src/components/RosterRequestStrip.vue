<script setup>
// Plan 2026-10-31-001 U10 / R12, R15, R17. A manager's recent roster
// requests above the team grid, from `get_roster_draft`'s `recent`: waiting
// (Withdraw), sent back (HR's reasons, one per person, and "Reopen as
// draft"), declined (HR's reason), withdrawn (Reopen) and approved. Withdraw
// and reopen are written here; a refusal -- reopening into a draft that
// already names someone -- stays on its own request, in the server's words.
import { ref } from 'vue'
import { createResource } from 'frappe-ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { formatDate } from '@/lib/dates'
import { notify } from '@/lib/notify'
import { draftSummary, reasonLines } from '@/lib/rosterRequests'

defineProps({
  /** `get_roster_draft().recent`, newest first. */
  requests: { type: Array, default: () => [] },
})
/** `changed({ draft })` after a write: `draft` is the reopened draft, or
 * undefined after a withdraw. */
const emit = defineEmits(['changed'])

const withdrawCall = createResource({ url: 'helixhr.api.withdraw_roster_change', method: 'POST' })
const reopenCall = createResource({ url: 'helixhr.api.reopen_roster_change', method: 'POST' })
const busy = ref('')
const errors = ref({})

function names(request) {
  return [...new Set((request.rows || []).map((row) => row.employee_name || row.employee))].join(', ')
}

async function run(request, write, done) {
  if (busy.value) return
  busy.value = request.name
  errors.value = { ...errors.value, [request.name]: '' }
  try {
    const result = await write()
    done(result)
  } catch (e) {
    errors.value = { ...errors.value, [request.name]: toPlainMessage(e?.messages?.[0] ?? e?.message) || FALLBACK_ERROR }
  } finally {
    busy.value = ''
  }
}

function withdraw(request) {
  return run(
    request,
    () => withdrawCall.submit({ name: request.name }),
    () => {
      notify.success('Request withdrawn')
      emit('changed', {})
    },
  )
}

function reopen(request) {
  return run(
    request,
    () => reopenCall.submit({ name: request.name }),
    (result) => {
      notify.success(result?.merged ? 'Added to your draft' : 'Reopened as your draft')
      emit('changed', { draft: result?.draft ?? null })
    },
  )
}
</script>

<template>
  <section
    v-if="requests.length"
    class="mb-4"
    aria-labelledby="roster-requests-heading"
    data-testid="roster-requests"
  >
    <h2
      id="roster-requests-heading"
      class="label mb-2"
    >
      Your roster requests
    </h2>
    <ul class="space-y-2">
      <li
        v-for="request in requests"
        :key="request.name"
        class="surface-card elev-1 p-3"
        data-testid="roster-request"
        :data-status="request.status"
      >
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusBadge
            kind="roster"
            :status="request.status"
          />
          <p class="min-w-0 flex-1 break-words text-sm text-ink-gray-9">
            {{ draftSummary(request.rows) }}
            <span class="text-ink-gray-6">· {{ names(request) }}</span>
            <span
              v-if="request.decided_on"
              class="text-ink-gray-5"
            > · {{ formatDate(request.decided_on) }}</span>
          </p>
          <button
            v-if="request.status === 'Pending'"
            type="button"
            class="inline-flex min-h-11 shrink-0 cursor-pointer items-center rounded px-2 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!!busy"
            @click="withdraw(request)"
          >
            Withdraw
          </button>
          <button
            v-if="request.status === 'Returned' || request.status === 'Withdrawn'"
            type="button"
            class="inline-flex min-h-11 shrink-0 cursor-pointer items-center rounded px-2 text-sm font-medium text-ink-blue-link underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!!busy"
            @click="reopen(request)"
          >
            {{ request.status === 'Returned' ? 'Reopen as draft' : 'Reopen' }}
          </button>
        </div>
        <!-- Sent back: HR's reasons, one line per person as the server
             wrote them. Declined: HR's one reason. -->
        <ul
          v-if="request.status === 'Returned' && reasonLines(request.return_reasons).length"
          class="mt-2 list-disc space-y-0.5 pl-5 text-sm text-ink-gray-7"
          data-testid="roster-request-reasons"
        >
          <li
            v-for="line in reasonLines(request.return_reasons)"
            :key="line"
          >
            {{ line }}
          </li>
        </ul>
        <p
          v-if="request.status === 'Declined' && request.decision_note"
          class="surface-inset mt-2 p-2 text-sm text-ink-gray-7"
        >
          HR: “{{ request.decision_note }}”
        </p>
        <p
          v-if="errors[request.name]"
          class="mt-2 text-sm font-medium text-ink-red-4"
          role="alert"
        >
          {{ errors[request.name] }}
        </p>
      </li>
    </ul>
  </section>
</template>
