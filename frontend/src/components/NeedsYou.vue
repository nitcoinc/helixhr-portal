<script setup>
import Icon from '@/components/Icon.vue'
import { formatDate } from '@/lib/dates'

defineProps({
  items: { type: Array, default: () => [] },
  // Rows the queue holds but did not show, so a long backlog is disclosed
  // rather than silently truncated.
  more: { type: Number, default: 0 },
  // Work that is this person's but is not this person's *move* -- leave
  // sitting with a manager. It stays visible, in its own quieter section,
  // instead of padding the queue with rows whose only honest action is
  // "wait" (P2-U4, P2-R8, P2-R11).
  waiting: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  // Shown in the empty state so a clear queue still tells you where you
  // stand rather than just going blank -- the direction's named risk.
  weekHours: { type: Number, default: 0 },
  timesheetState: { type: String, default: null },
})

const TONE = {
  danger: 'bg-red-50 text-red-600',
  action: 'bg-blue-50 text-blue-700',
  info: 'bg-green-50 text-green-700',
  muted: 'bg-surface-gray-2 text-ink-gray-6',
}
// An out-of-week row has no day to dock against, so it says how overdue it
// is instead. Losing two words of context was not enough of a difference:
// the older item is the more urgent one and has to look like it.
function ageLabel(item) {
  const days = item.age_days
  if (days === null || days === undefined || days < 7) return null
  if (days < 14) return 'Over a week ago'
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`
  return `${Math.floor(days / 30)} months ago`
}

const ICON = {
  timesheet_rejected: 'timesheet',
  request_answered: 'requests',
  approval_leave: 'approvals',
  approval_timesheet: 'approvals',
  leave_waiting: 'leave',
}
</script>

<template>
  <section aria-labelledby="needs-you-heading">
    <h2
      id="needs-you-heading"
      class="mb-2 font-heading text-base font-semibold text-ink-gray-9"
    >
      Needs you
    </h2>

    <div
      v-if="loading"
      class="space-y-2"
      aria-busy="true"
    >
      <div
        v-for="n in 2"
        :key="n"
        class="h-16 animate-pulse rounded-xl bg-surface-gray-2"
      />
    </div>

    <!-- An empty queue is the good outcome, so it says so and then names the
         one thing still outstanding rather than reading as a broken page. -->
    <div
      v-else-if="items.length === 0"
      class="elev-1 rounded-xl border border-outline-gray-1 bg-surface-white p-5"
    >
      <p class="font-heading text-base font-medium text-ink-gray-9">
        Nothing needs you.
      </p>
      <p class="mt-1 text-sm text-ink-gray-6">
        <template v-if="timesheetState === 'Approved'">
          This week's timesheet is approved. You're all clear.
        </template>
        <template v-else-if="timesheetState">
          This week's timesheet is with your manager.
        </template>
        <template v-else>
          When you're ready, log this week's hours —
          <span class="tabular">{{ weekHours }}</span> so far.
        </template>
      </p>
      <router-link
        v-if="timesheetState !== 'Approved'"
        to="/timesheet"
        class="mt-3 inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-blue-700 hover:underline"
      >
        Open this week's timesheet
      </router-link>
    </div>

    <ul
      v-else
      class="space-y-2"
    >
      <!-- The key is the server's own record identity, never the index: the
           queue re-orders as work is done, and an index key reuses the wrong
           row's DOM state when it does (P2-U4 step 1). -->
      <li
        v-for="item in items"
        :key="item.id"
        class="elev-1 flex flex-wrap items-start gap-x-3 gap-y-2 rounded-xl border border-outline-gray-1 bg-surface-white p-3 sm:flex-nowrap"
        :data-kind="item.kind"
      >
        <span
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
          :class="TONE[item.tone] || TONE.muted"
        >
          <Icon
            :name="ICON[item.kind] || 'requests'"
            size="h-4 w-4"
          />
        </span>

        <div class="min-w-0 flex-1 basis-[calc(100%-2.75rem)]">
          <p class="text-sm font-medium text-ink-gray-9">
            {{ item.title }}
          </p>
          <!-- The manager's reason, or HR's reply, inline: the whole point is
               not having to open the record to find out what it says. -->
          <p
            v-if="item.detail"
            class="mt-0.5 text-sm text-ink-gray-6"
          >
            “{{ item.detail }}”
          </p>
          <p
            v-if="item.day || item.date"
            class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs"
          >
            <span
              v-if="ageLabel(item)"
              class="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700"
            >
              {{ ageLabel(item) }}
            </span>
            <span class="tabular text-ink-gray-5">
              <span v-if="item.day">Week of </span>{{ formatDate(item.date) }}
            </span>
          </p>
        </div>

        <router-link
          :to="item.to"
          class="ml-11 inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-1 self-center rounded-lg px-3 text-sm font-medium text-blue-700 hover:bg-blue-50 sm:ml-0"
        >
          {{ item.action }}
          <Icon
            name="chevronRight"
            size="h-4 w-4"
          />
        </router-link>
      </li>
    </ul>

    <p
      v-if="more > 0"
      class="mt-2 text-sm text-ink-gray-6"
    >
      and <span class="tabular font-medium">{{ more }}</span> more not shown here.
    </p>

    <!-- Waiting on others. Same rows, deliberately quieter: no tinted tile,
         no verb of its own, and it never competes with the queue above it. -->
    <div
      v-if="waiting.length"
      class="mt-5"
    >
      <h3 class="label mb-2">
        Waiting on others
      </h3>
      <ul class="space-y-2">
        <li
          v-for="item in waiting"
          :key="item.id"
          :data-kind="item.kind"
        >
          <router-link
            :to="item.to"
            class="surface-card elev-1 flex min-h-11 cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors duration-200 hover:border-blue-600"
          >
            <Icon
              :name="ICON[item.kind] || 'leave'"
              size="h-4 w-4"
              class="shrink-0 text-ink-gray-4"
            />
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm text-ink-gray-7">{{ item.title }}</span>
              <span class="tabular block text-xs text-ink-gray-5">
                {{ formatDate(item.date) }}
              </span>
            </span>
            <Icon
              name="chevronRight"
              size="h-4 w-4"
              class="shrink-0 text-ink-gray-4"
            />
          </router-link>
        </li>
      </ul>
    </div>
  </section>
</template>
