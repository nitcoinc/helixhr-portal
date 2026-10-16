<script setup>
import { computed, ref } from 'vue'
import { createResource } from 'frappe-ui'
import { RouterLink } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import Celebrations from '@/components/Celebrations.vue'
import { session } from '@/lib/session'

// P5-U15 / P5-R20, P5-R23. Management's read-only snapshot of the
// organisation: counts, backlog ages, and nothing per person.
//
// `get_organisation_view` is the whole authorization story here (its read
// scope, the same predicate `can_see_organisation` mirrors in the bootstrap)
// -- a caller without one hitting this page directly gets AsyncState's
// 'forbidden' region below, not a client-side redirect. There is no action
// anywhere on this page: it reads one resource and draws it, on purpose
// (P5-R20's "grants no power to act"). Plan 2026-10-17-001 U17 (HR-13): the
// queue rows link to where that work is done -- navigation, not an action.
//
// Plan 2026-10-14-001 U6: a company access holder may read more than one
// company; the picker shows one at a time, from the server's own list.
const company = ref('')
const view = createResource({
  url: 'helixhr.api.get_organisation_view',
  makeParams: () => (company.value ? { company: company.value } : {}),
  auto: true,
})
const companies = computed(() => view.data?.companies || [])

function pickCompany(value) {
  company.value = value
  view.reload()
}

const QUEUE_LABELS = {
  leave: 'Leave',
  timesheet: 'Timesheets',
  attendance: 'Attendance requests',
  request: 'Requests',
}

/** Where a queue's work is done, or null. Gated by the same bootstrap flags
 * the nav reads, so no row links into a page that would refuse or fall back
 * to another tab. The query shapes are Approvals' `?view=` / `?kind=` and
 * Requests' `?tab=work` (the team inbox). */
function queueLink(key) {
  if (key === 'leave' || key === 'attendance') {
    return session.canApprove ? { path: '/approvals', query: { view: 'queue', kind: key } } : null
  }
  if (key === 'timesheet') {
    return session.canApprove || session.canOverseeTimesheets
      ? { path: '/approvals', query: { view: 'timesheets' } }
      : null
  }
  if (key === 'request') return session.canHandleRequests ? { path: '/requests', query: { tab: 'work' } } : null
  return null
}

const queues = computed(() => {
  const data = view.data?.queues || {}
  return Object.keys(QUEUE_LABELS).map((key) => ({
    key,
    label: QUEUE_LABELS[key],
    pending: data[key]?.pending ?? 0,
    oldestPendingDays: data[key]?.oldest_pending_days ?? null,
    to: queueLink(key),
  }))
})

const headcount = computed(() => view.data?.headcount ?? 0)
const onLeaveToday = computed(() => view.data?.on_leave_today ?? 0)
const birthdays = computed(() => view.data?.celebrations?.birthdays || [])
const anniversaries = computed(() => view.data?.celebrations?.anniversaries || [])
const hasCelebrations = computed(() => birthdays.value.length + anniversaries.value.length > 0)

function ageLabel(days) {
  if (days === null || days === undefined) return null
  if (days === 0) return 'opened today'
  return `oldest is ${days} ${days === 1 ? 'day' : 'days'} old`
}
</script>

<template>
  <div>
    <PageHeader
      title="Organisation"
      subtitle="A read-only view of your company: counts, never names, and nothing to act on here."
    />

    <div
      v-if="companies.length > 1"
      class="mb-4 max-w-xs"
    >
      <label
        for="organisation-company"
        class="text-sm text-ink-gray-6"
      >
        Company
      </label>
      <select
        id="organisation-company"
        :value="view.data?.company"
        class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
        data-testid="organisation-company"
        @change="pickCompany($event.target.value)"
      >
        <option
          v-for="name in companies"
          :key="name"
          :value="name"
        >
          {{ name }}
        </option>
      </select>
    </div>

    <AsyncState
      section="organisation"
      :resource="view"
      :empty="false"
      skeleton="block"
      skeleton-height="h-64"
    >
      <div class="space-y-6">
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div class="surface-card elev-1 p-4">
            <p class="text-sm text-ink-gray-6">
              Headcount
            </p>
            <p class="tabular font-heading text-3xl font-semibold text-ink-gray-9">
              {{ headcount }}
            </p>
          </div>
          <div class="surface-card elev-1 p-4">
            <p class="text-sm text-ink-gray-6">
              On leave today
            </p>
            <p class="tabular font-heading text-3xl font-semibold text-ink-gray-9">
              {{ onLeaveToday }}
            </p>
          </div>
        </div>

        <section
          class="surface-card elev-1 p-4"
          aria-labelledby="organisation-queues-heading"
        >
          <h2
            id="organisation-queues-heading"
            class="font-heading text-base font-semibold text-ink-gray-9"
          >
            Queues
          </h2>
          <!-- HR-13: these count the whole company; Approvals counts what is
               routed to this person, so the two numbers differ on purpose. -->
          <p class="mt-1 text-sm text-ink-gray-6">
            Everything waiting in the company. Approvals and the team inbox show what is routed to you.
          </p>
          <ul class="mt-2 divide-y divide-outline-gray-1">
            <li
              v-for="queue in queues"
              :key="queue.key"
              :data-testid="`organisation-queue-${queue.key}`"
            >
              <component
                :is="queue.to ? RouterLink : 'div'"
                :to="queue.to || undefined"
                class="flex min-h-11 items-center justify-between gap-3 py-3"
                :class="queue.to ? '-mx-2 rounded px-2 hover:bg-surface-gray-2' : ''"
              >
                <span class="text-sm text-ink-gray-7">{{ queue.label }}</span>
                <span class="flex items-baseline gap-2">
                  <span class="tabular font-heading text-xl font-semibold text-ink-gray-9">
                    {{ queue.pending }}
                  </span>
                  <span
                    v-if="ageLabel(queue.oldestPendingDays)"
                    class="text-xs text-ink-gray-5"
                  >{{ ageLabel(queue.oldestPendingDays) }}</span>
                  <Icon
                    v-if="queue.to"
                    name="chevronRight"
                    class="h-4 w-4 self-center text-ink-gray-4"
                  />
                </span>
              </component>
            </li>
          </ul>
        </section>

        <Celebrations
          v-if="hasCelebrations"
          :birthdays="birthdays"
          :anniversaries="anniversaries"
        />
      </div>
    </AsyncState>
  </div>
</template>
