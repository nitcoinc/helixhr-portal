<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import Icon from '@/components/Icon.vue'
import { formatDate } from '@/lib/dates'
import { NEEDS_YOU_ICON } from '@/lib/icons'

const props = defineProps({
  items: { type: Array, default: () => [] },
  // Rows the queue holds but did not show, so a long backlog is disclosed
  // rather than silently truncated.
  more: { type: Number, default: 0 },
  // Work that is this person's but is not this person's *move* -- leave
  // sitting with a manager. It stays visible, in its own quieter section,
  // instead of padding the queue with rows whose only honest action is
  // "wait" (P2-U4, P2-R8, P2-R11).
  waiting: { type: Array, default: () => [] },
  // Waiting rows past the server's window, for the "View all (N)" count.
  waitingMore: { type: Number, default: 0 },
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

// R12. Each list shows five rows and scrolls the rest, so an HR backlog
// cannot push the rest of Home off the screen. Rows differ in height (a
// reason line, an age tag), so the cap is measured from the fifth row
// rather than guessed in rem.
const VISIBLE_ROWS = 5
const queueList = ref(null)
const waitingList = ref(null)
const queueHeight = ref(null)
const waitingHeight = ref(null)
const queueTotal = computed(() => props.items.length + props.more)
const waitingTotal = computed(() => props.waiting.length + props.waitingMore)

function capHeight(el) {
  const rows = el?.children
  if (!rows || rows.length <= VISIBLE_ROWS) return null
  const last = rows[VISIBLE_ROWS - 1]
  return `${last.offsetTop - rows[0].offsetTop + last.offsetHeight}px`
}

watch(
  () => [props.items, props.waiting, props.loading],
  async () => {
    await nextTick()
    queueHeight.value = capHeight(queueList.value)
    waitingHeight.value = capHeight(waitingList.value)
  },
  { immediate: true, flush: 'post' },
)
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
        class="h-16 animate-pulse rounded bg-surface-gray-2"
      />
    </div>

    <!-- An empty queue is the good outcome, so it says so and then names the
         one thing still outstanding rather than reading as a broken page. -->
    <div
      v-else-if="items.length === 0"
      class="surface-card elev-1 p-4"
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

    <!-- Past five rows the list is a labelled, focusable scroll region so
         a keyboard can scroll it too; the inset border marks the cut. -->
    <ul
      v-else
      ref="queueList"
      class="space-y-2"
      :class="items.length > VISIBLE_ROWS ? 'scroll-queue' : ''"
      :style="queueHeight ? { maxHeight: queueHeight } : null"
      :tabindex="items.length > VISIBLE_ROWS ? 0 : undefined"
      :aria-label="items.length > VISIBLE_ROWS ? 'Needs you, scrollable' : undefined"
    >
      <!-- The key is the server's own record identity, never the index: the
           queue re-orders as work is done, and an index key reuses the wrong
           row's DOM state when it does (P2-U4 step 1). -->
      <li
        v-for="item in items"
        :key="item.id"
        class="surface-card elev-1 flex flex-wrap items-start gap-x-3 gap-y-2 p-3 sm:flex-nowrap"
        :data-kind="item.kind"
      >
        <span
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
          :class="TONE[item.tone] || TONE.muted"
        >
          <Icon
            :name="NEEDS_YOU_ICON[item.kind] || 'requests'"
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
      v-if="!loading && queueTotal > VISIBLE_ROWS"
      class="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm text-ink-gray-6"
    >
      <span v-if="more > 0">
        and <span class="tabular font-medium">{{ more }}</span> more not shown here.
      </span>
      <router-link
        to="/approvals"
        class="tabular ml-auto inline-flex min-h-11 cursor-pointer items-center font-medium text-blue-700 hover:underline"
      >
        View all ({{ queueTotal }})
      </router-link>
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
      <ul
        ref="waitingList"
        class="space-y-2"
        :class="waiting.length > VISIBLE_ROWS ? 'scroll-queue' : ''"
        :style="waitingHeight ? { maxHeight: waitingHeight } : null"
        :tabindex="waiting.length > VISIBLE_ROWS ? 0 : undefined"
        :aria-label="waiting.length > VISIBLE_ROWS ? 'Waiting on others, scrollable' : undefined"
      >
        <li
          v-for="item in waiting"
          :key="item.id"
          :data-kind="item.kind"
        >
          <router-link
            :to="item.to"
            class="surface-card elev-1 flex min-h-11 cursor-pointer items-center gap-3 p-3 transition-colors duration-200 hover:border-blue-600"
          >
            <Icon
              :name="NEEDS_YOU_ICON[item.kind] || 'leave'"
              size="h-4 w-4"
              class="shrink-0 text-ink-gray-4"
            />
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm text-ink-gray-7">{{ item.title }}</span>
              <!-- P4-R4. A terminal rejection reaches this list, and its
                   reason is the whole reason the row exists: without it the
                   employee reads "was rejected" and has to open the record to
                   find out why. -->
              <span
                v-if="item.detail"
                class="block truncate text-xs text-ink-gray-6"
              >“{{ item.detail }}”</span>
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
      <router-link
        v-if="waitingTotal > VISIBLE_ROWS"
        to="/requests"
        class="tabular mt-2 inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-blue-700 hover:underline"
      >
        View all ({{ waitingTotal }})
      </router-link>
    </div>
  </section>
</template>

<style scoped>
/* The scroll affordance: an always-visible scrollbar gutter and a bottom
   rule, so a cut-off list does not read as the whole list. */
.scroll-queue {
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding-right: 0.25rem;
  border-bottom: 1px solid var(--outline-gray-2);
}
</style>
