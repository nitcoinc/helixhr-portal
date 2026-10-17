<script setup>
import { computed, ref } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Celebrations from '@/components/Celebrations.vue'

// P5-U15 / P5-R20, P5-R23. Management's read-only snapshot of the
// organisation: counts, backlog ages, and nothing per person.
//
// `get_organisation_view` is the whole authorization story here (its read
// scope, the same predicate `can_see_organisation` mirrors in the bootstrap)
// -- a caller without one hitting this page directly gets AsyncState's
// 'forbidden' region below, not a client-side redirect. There is no action
// anywhere on this page: it reads one resource and draws it, on purpose
// (P5-R20's "grants no power to act").
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

const queues = computed(() => {
  const data = view.data?.queues || {}
  return Object.keys(QUEUE_LABELS).map((key) => ({
    key,
    label: QUEUE_LABELS[key],
    pending: data[key]?.pending ?? 0,
    oldestPendingDays: data[key]?.oldest_pending_days ?? null,
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
          <ul class="mt-2 divide-y divide-outline-gray-1">
            <li
              v-for="queue in queues"
              :key="queue.key"
              class="flex items-center justify-between gap-3 py-3"
              :data-testid="`organisation-queue-${queue.key}`"
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
              </span>
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
