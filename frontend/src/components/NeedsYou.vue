<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import Icon from '@/components/Icon.vue'
import { formatDate } from '@/lib/dates'
import { NEEDS_YOU_ICON } from '@/lib/needsYouIcons'
import { queueDestination, sideListDestination } from '@/lib/needsYou'
import { session } from '@/lib/session'
import { markReadKeepalive } from '@/lib/unread'

const props = defineProps({
  // One row per record (plan 2026-10-17-001 KTD10): the server collapses
  // sixty replies on one request into one row before it counts or slices.
  items: { type: Array, default: () => [] },
  // Records the queue holds but did not show, so a long backlog is disclosed
  // rather than silently truncated.
  more: { type: Number, default: 0 },
  // Work that is this person's but is not this person's *move* -- leave
  // sitting with a manager. It stays visible, in its own quieter section,
  // instead of padding the queue with rows whose only honest action is
  // "wait" (P2-U4, P2-R8, P2-R11).
  waiting: { type: Array, default: () => [] },
  // Waiting rows past the server's window, for the "View all (N)" count.
  waitingMore: { type: Number, default: 0 },
  // KTD11 / KTD12: final outcomes -- a final rejection, an unread Done or
  // Declined on a request. Nobody's move and never dropped by time, so a
  // sibling of "Waiting on others" rather than a row in it (EP-08).
  decided: { type: Array, default: () => [] },
  decidedMore: { type: Number, default: 0 },
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
const decidedList = ref(null)
const queueHeight = ref(null)
const waitingHeight = ref(null)
const decidedHeight = ref(null)
const queueTotal = computed(() => props.items.length + props.more)
const waitingTotal = computed(() => props.waiting.length + props.waitingMore)
const decidedTotal = computed(() => props.decided.length + props.decidedMore)
// EP-09: each "View all (N)" opens the page that owns those records for
// this person -- never Approvals for an employee who has none.
const viewAllDestination = computed(() => queueDestination(session))
const waitingDestination = computed(() => sideListDestination(props.waiting))
const decidedDestination = computed(() => sideListDestination(props.decided))

function capHeight(el) {
  const rows = el?.children
  if (!rows || rows.length <= VISIBLE_ROWS) return null
  const last = rows[VISIBLE_ROWS - 1]
  return `${last.offsetTop - rows[0].offsetTop + last.offsetHeight}px`
}

watch(
  () => [props.items, props.waiting, props.decided, props.loading],
  async () => {
    await nextTick()
    queueHeight.value = capHeight(queueList.value)
    waitingHeight.value = capHeight(waitingList.value)
    decidedHeight.value = capHeight(decidedList.value)
  },
  { immediate: true, flush: 'post' },
)

/** KTD10: opening a notification-backed row clears every unread log the
 * caller holds about that record ("HR replied · 4" clears all four), not
 * just the newest. `keepalive`, because the tap is also the navigation. */
async function opened(item) {
  if (!item.notification || !item.reference_doctype || !item.reference_name) return
  await markReadKeepalive(
    'helixhr.api.mark_record_notifications_read',
    { reference_doctype: item.reference_doctype, reference_name: item.reference_name },
    item.count || 1,
  )
}
</script>

<template>
  <section aria-labelledby="needs-you-heading">
    <h2
      id="needs-you-heading"
      class="type-section mb-2 font-heading text-ink-gray-9"
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
           row's DOM state when it does (P2-U4 step 1).

           EP-03: the whole row is the one link, with the verb as a chip on
           the title line. The title can wrap, so the link carries
           `-my-2 py-2 min-h-11` (the stretched-link rule in
           docs/design-system.md): the `after:` layer is the tap target even
           when the title wraps. -->
      <li
        v-for="item in items"
        :key="item.id"
        class="surface-card elev-1 relative flex items-start gap-3 p-3 transition-colors duration-200 hover:border-blue-600"
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

        <div class="min-w-0 flex-1">
          <router-link
            :to="item.to"
            class="-my-2 flex min-h-11 flex-wrap items-center justify-between gap-x-2 gap-y-1 py-2 text-sm font-medium text-ink-gray-9 after:absolute after:inset-0 after:rounded after:content-['']"
            @click="opened(item)"
          >
            <!-- The chip rides the title line until the title would be
                 squeezed under 8rem; then it wraps rather than overflow
                 (200% text on a phone). -->
            <span class="min-w-0 flex-[1_1_8rem] break-words">{{ item.title }}</span>
            <span class="shrink-0 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
              {{ item.action }}
            </span>
          </router-link>
          <!-- The manager's reason, or the newest HR reply, inline: the whole
               point is not having to open the record to find out what it
               says. One line beside the date keeps the row short (EP-03);
               the full text is a tap away. -->
          <p
            v-if="item.detail || item.date"
            class="mt-0.5 flex min-w-0 items-center gap-2 text-xs"
          >
            <span
              v-if="item.detail"
              class="min-w-0 flex-1 truncate text-sm text-ink-gray-6"
              :title="item.detail"
            >“{{ item.detail }}”</span>
            <span
              v-if="ageLabel(item)"
              class="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700"
            >
              {{ ageLabel(item) }}
            </span>
            <span
              v-if="item.date"
              class="tabular ml-auto shrink-0 text-ink-gray-5"
            >
              <span v-if="item.day">Week of </span>{{ formatDate(item.date) }}
            </span>
          </p>
        </div>
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
        :to="viewAllDestination"
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
        :to="waitingDestination"
        class="tabular mt-2 inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-blue-700 hover:underline"
      >
        View all ({{ waitingTotal }})
      </router-link>
    </div>

    <!-- Decided (KTD11). A final rejection is not waiting on anyone, so it
         left "Waiting on others" -- but it never drops off by time (P4-R4):
         the reason is the whole content of the row, and an unread one must
         not vanish. An unread Done or Declined on a request lands here too
         (KTD12): an update, not an ask. -->
    <div
      v-if="decided.length"
      class="mt-5"
    >
      <h3 class="label mb-2">
        Decided
      </h3>
      <ul
        ref="decidedList"
        class="space-y-2"
        :class="decided.length > VISIBLE_ROWS ? 'scroll-queue' : ''"
        :style="decidedHeight ? { maxHeight: decidedHeight } : null"
        :tabindex="decided.length > VISIBLE_ROWS ? 0 : undefined"
        :aria-label="decided.length > VISIBLE_ROWS ? 'Decided, scrollable' : undefined"
      >
        <li
          v-for="item in decided"
          :key="item.id"
          :data-kind="item.kind"
        >
          <router-link
            :to="item.to"
            class="surface-card elev-1 flex min-h-11 cursor-pointer items-center gap-3 p-3 transition-colors duration-200 hover:border-blue-600"
            @click="opened(item)"
          >
            <Icon
              :name="NEEDS_YOU_ICON[item.kind] || 'leave'"
              size="h-4 w-4"
              class="shrink-0 text-ink-gray-4"
            />
            <span class="min-w-0 flex-1">
              <span class="flex items-center gap-2">
                <span
                  v-if="item.notification"
                  class="h-2 w-2 shrink-0 rounded-full bg-blue-600"
                  aria-hidden="true"
                />
                <span class="min-w-0 truncate text-sm text-ink-gray-7">{{ item.title }}</span>
                <span
                  v-if="item.notification"
                  class="sr-only"
                >(unread)</span>
              </span>
              <!-- P4-R4. Without the reason the employee reads "was
                   rejected" and has to open the record to find out why. -->
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
        v-if="decidedTotal > VISIBLE_ROWS"
        :to="decidedDestination"
        class="tabular mt-2 inline-flex min-h-11 cursor-pointer items-center text-sm font-medium text-blue-700 hover:underline"
      >
        View all ({{ decidedTotal }})
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
