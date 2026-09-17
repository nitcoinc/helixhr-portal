<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { call } from '@/lib/api'
import PageHeader from '@/components/PageHeader.vue'
import { session } from '@/lib/session'

// P6-U6 / P6-R9, P6-R10, P6-R11, P6-R12. A curated launcher into Frappe's
// own reports -- named questions, not a report tree.
//
// This screen never re-implements a report or export (P6-R11): every entry
// opens Frappe's own report view in a new tab, where the export the person
// wants is already the report view's own toolbar. `get_report_link` is the
// server's own gate (`can_open_desk`, and -- when filtered to a person --
// the same admin scope `get_person` uses), so a caller this page should not
// have reached still cannot open one.
//
// Kept as a short, named list in the style `ADMIN_REPORTS`
// (helixhr/utils.py) defines server-side, deliberately duplicated here as
// copy rather than fetched: `get_report_link` is the single source of truth
// for whether a report actually opens, and a report named here that drifts
// from that list simply 403s on click rather than silently widening what
// the launcher offers.
const REPORTS = [
  {
    report: 'Employee Leave Balance',
    label: 'Leave balance',
    question: 'How much leave does someone have left, by type?',
  },
  {
    report: 'Employee Leave Balance Summary',
    label: 'Leave balance summary',
    question: 'Leave balances across the whole company, one row per person.',
  },
  {
    report: 'Monthly Attendance Sheet',
    label: 'Monthly attendance',
    question: 'A month of attendance, one row per person per day.',
  },
  {
    report: 'Shift Attendance',
    label: 'Shift attendance',
    question: 'Who was on which shift, and when.',
  },
  {
    report: 'Leave Ledger',
    label: 'Leave ledger',
    question: 'Every leave transaction that moved a balance.',
  },
  {
    report: 'Employee Information',
    label: 'Employee information',
    question: 'A directory-style export of the whole company.',
  },
  {
    report: 'Employee Exits',
    label: 'Employee exits',
    question: 'Who has left, and when.',
  },
]

const route = useRoute()
const router = useRouter()

// Arrived from a person's view (People.vue) with ?employee=<id>: every
// report opens pre-filtered to them (P6-R10), which is what makes this a
// launcher rather than a bookmark list.
const employee = computed(() => (typeof route.query.employee === 'string' ? route.query.employee : ''))

function clearEmployee() {
  router.replace({ name: 'Reports' })
}

const openError = ref('')
const opening = ref('')

async function open(report) {
  openError.value = ''
  opening.value = report
  try {
    const url = await call('helixhr.api.get_report_link', {
      report,
      employee: employee.value || undefined,
    })
    window.open(url, '_blank', 'noopener')
  } catch (error) {
    openError.value = error?.messages?.[0] || "That report didn't open. Try again."
  } finally {
    opening.value = ''
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="Reports"
      subtitle="Frappe's own reports, pre-filtered where the portal already knows the answer. Reports open in Frappe -- that's where export happens too."
    />

    <p
      v-if="employee"
      class="surface-inset mb-4 flex items-center justify-between gap-3 p-3 text-sm text-ink-gray-7"
    >
      Filtered to one person.
      <button
        type="button"
        class="cursor-pointer text-blue-700 underline underline-offset-2"
        @click="clearEmployee"
      >
        Clear
      </button>
    </p>

    <!-- `get_report_link` is the server's own gate (P6-R8's posture, applied
         to reports): this is a client-side mirror of it, drawn once at page
         load, so the page states the reason plainly rather than letting a
         caller click through to a 403 first. -->
    <div
      v-if="!session.canSeePeople"
      class="surface-card p-5 text-sm text-ink-gray-6"
      role="alert"
    >
      <p class="font-medium text-ink-gray-9">
        You don't have access to this
      </p>
      <p class="mt-1">
        If you think that's wrong, ask HR to check your access.
      </p>
    </div>

    <p
      v-else-if="!session.canOpenDesk"
      class="surface-card p-5 text-sm text-ink-gray-6"
      role="alert"
    >
      You don't have access to Frappe's Desk, so reports can't open from here. Ask HR if you think
      that's wrong.
    </p>

    <template v-else>
      <p
        v-if="openError"
        class="surface-alert mb-4 p-3 text-sm"
        role="alert"
      >
        {{ openError }}
      </p>

      <ul class="space-y-2 lg:grid lg:grid-cols-2 lg:gap-3 lg:space-y-0">
        <li
          v-for="entry in REPORTS"
          :key="entry.report"
        >
          <button
            type="button"
            class="surface-card elev-1 flex h-full w-full flex-col items-start gap-1 p-4 text-left"
            :disabled="opening === entry.report"
            @click="open(entry.report)"
          >
            <span class="font-medium text-ink-gray-9">{{ entry.label }}</span>
            <span class="text-sm text-ink-gray-6">{{ entry.question }}</span>
            <span class="mt-1 text-xs text-ink-gray-5">
              {{ opening === entry.report ? 'Opening…' : 'Opens in a new tab' }}
            </span>
          </button>
        </li>
      </ul>
    </template>
  </div>
</template>
