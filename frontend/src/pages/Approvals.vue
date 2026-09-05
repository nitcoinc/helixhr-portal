<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import { formatDate, formatDateRange } from '@/lib/dates'

// P2-U7 / KTD5. `/approvals` and `/approvals/:kind/:name` are one component:
// the selected decision is a route parameter, so a manager can be sent the
// link to a specific decision from Home, from a notification or from another
// person, and refresh and browser Back all land on the same record.
const props = defineProps({
  kind: { type: String, default: '' },
  name: { type: String, default: '' },
})

const router = useRouter()

// P2-U7 step 1 / P2-R27. One session-scoped read, replacing the two the page
// used to make itself. The timesheet half of that was
// `frappe.client.get_list` with `filters: { workflow_state: 'Pending
// Approval' }` and no employee scope at all -- a caller-controlled generic
// read whose only limit was whatever Frappe happened to allow, and which did
// not even exclude the manager's own week. The server now decides what is in
// this queue, using the same rules that decide who may act on it.
const queue = createResource({
  url: 'helixhr.api.get_my_approvals',
  auto: true,
})

const pending = computed(() => queue.data?.pending || [])
const decided = computed(() => queue.data?.decided || [])
const overflow = computed(() => Math.max(0, (queue.data?.total || 0) - pending.value.length))

// --- the selected decision ----------------------------------------------

// P2-U7 step 2 / P2-R22. Evidence costs a document read plus its child rows,
// so it is fetched for the one item the manager actually opened -- never for
// the whole queue, and never eagerly.
const detail = createResource({
  url: 'helixhr.api.get_approval_detail',
  makeParams: () => ({ kind: props.kind, name: props.name }),
})

const selected = computed(() => (props.name ? detail.data : null))

function open(row) {
  router.push({ name: 'ApprovalDetail', params: { kind: row.kind, name: row.name } })
}

function closeDetail() {
  router.push({ name: 'Approvals' })
}

// One breakpoint decides the *shape*, never the identity (KTD5): at lg: the
// evidence sits beside the queue, below it the selected row expands in place.
// The two are genuinely different layouts -- a project x day grid does not
// fit 360px -- so exactly one of them is rendered rather than both being in
// the DOM with one hidden.
const isDesktop = ref(false)
let widthQuery = null
function syncWidth(event) {
  isDesktop.value = event.matches
}
onMounted(() => {
  widthQuery = window.matchMedia('(min-width: 1024px)')
  isDesktop.value = widthQuery.matches
  widthQuery.addEventListener('change', syncWidth)
})
onUnmounted(() => widthQuery?.removeEventListener('change', syncWidth))

// --- deciding ------------------------------------------------------------

const act = createResource({ url: 'helixhr.api.act_on_approval', method: 'POST' })
const acting = ref('') // the one item in flight, by record name
const actionError = ref('')
const reason = ref('')
const reasonError = ref('')
const showReason = ref(false)

// The selected record drives the fetch, and clears whatever the last
// decision left behind -- a half-typed reason must never follow the manager
// onto somebody else's record. Declared here rather than beside the resource
// because `immediate: true` runs during setup, and the refs it resets are
// below.
watch(
  () => [props.kind, props.name].join('/'),
  () => {
    actionError.value = ''
    reason.value = ''
    showReason.value = false
    if (props.name) detail.fetch()
  },
  { immediate: true },
)

function askForReason() {
  actionError.value = ''
  showReason.value = true
}

/**
 * P2-U7 steps 3 and 4. One decision at a time, always carrying the `modified`
 * and the state the evidence on screen was rendered from, so a decision made
 * against a record that has since moved is refused by the server instead of
 * overwriting somebody else's.
 */
async function decide(action) {
  const item = selected.value
  // Double-tap protection is here rather than only on `:disabled`: a second
  // pointerdown can land before Vue has flushed the disabled attribute
  // (P2-U7 scenario 3).
  if (!item || acting.value) return

  if (action === 'Reject' && !reason.value.trim()) {
    showReason.value = true
    reasonError.value = 'Say what should change before sending it back.'
    return
  }

  actionError.value = ''
  reasonError.value = ''
  acting.value = item.name
  try {
    await act.submit({
      doctype: item.doctype,
      name: item.name,
      action,
      comment: action === 'Reject' ? reason.value.trim() : undefined,
      expected_modified: item.modified,
      expected_state: item.state,
    })
    reason.value = ''
    showReason.value = false
    closeDetail()
    queue.reload()
  } catch (error) {
    // Stale, already decided, reassigned or refused. The queue is reloaded
    // so the item that is genuinely gone leaves -- but nothing else on the
    // list is touched, and the manager is told what happened rather than
    // being shown a queue that silently changed under them.
    actionError.value =
      error?.messages?.[0] || "We couldn't record that decision. Reload and try again."
    queue.reload()
    if (props.name) detail.fetch()
  } finally {
    acting.value = ''
  }
}

// --- presentation --------------------------------------------------------

const DAY_LETTERS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

/** The tallest bar in the 7-day strip. Scaled against a full day, so a light
 * week reads as a light week rather than being stretched to fill the strip. */
const dayScale = computed(() => {
  const hours = (selected.value?.day_totals || []).map((day) => day.hours)
  return Math.max(8, ...hours, 0)
})

function barHeight(hours) {
  return `${Math.round((Math.min(hours, dayScale.value) / dayScale.value) * 100)}%`
}

function rowSummary(row) {
  if (row.kind === 'leave') {
    return `${row.leave_type} · ${formatDateRange(row.from_date, row.to_date)}`
  }
  return `Timesheet · ${formatDateRange(row.from_date, row.to_date)}`
}

function rowAmount(row) {
  if (row.kind === 'leave') {
    return `${row.total_days} day${row.total_days === 1 ? '' : 's'}`
  }
  return `${row.total_hours} h`
}

/** "2 d" beside a row: how long this person has been waiting on the manager.
 * The number is the point, so it is never softened into "recently". */
function ageLabel(row) {
  if (row.age_days === null || row.age_days === undefined) return ''
  if (row.age_days === 0) return 'today'
  return `${row.age_days} d`
}

/** The quantity on the primary button. A decision that consumes 38.5 hours or
 * 3 days of somebody's balance says so on the control that does it. */
const approveLabel = computed(() => {
  const item = selected.value
  if (!item) return 'Approve'
  if (item.kind === 'leave') {
    return `Approve ${item.total_days} day${item.total_days === 1 ? '' : 's'}`
  }
  return `Approve ${item.total_hours} h`
})

const firstName = computed(() => (selected.value?.employee_name || '').split(/\s+/)[0])
</script>

<template>
  <div>
    <PageHeader title="Approvals">
      <template #actions>
        <span
          v-if="pending.length"
          class="rounded-full bg-surface-amber-1 px-3 py-1 text-sm font-medium text-ink-amber-3"
        >
          <span class="tabular">{{ pending.length }}</span> waiting
        </span>
      </template>
    </PageHeader>

    <p class="mb-4 text-sm text-ink-gray-5">
      Oldest first. You only see people who report to you.
    </p>

    <div class="lg:flex lg:items-start lg:gap-6">
      <!-- The queue. One list, leave and timesheets together: they are the
           same job -- somebody is waiting on a decision -- and splitting them
           into two sections made a manager check two places to find out
           whether they were done (P2-U7 step 7). -->
      <div
        v-show="!name || isDesktop"
        class="min-w-0 lg:flex-1"
        data-testid="approvals-queue"
      >
        <AsyncState
          section="approvals-queue"
          :resource="queue"
          :empty="pending.length === 0"
          empty-title="Nothing waiting on you"
          empty-body="Leave and weeks your team sends for approval appear here."
          :skeleton-rows="3"
        >
          <ul class="space-y-2">
            <li
              v-for="row in pending"
              :key="row.id"
              class="surface-card elev-1"
              :class="row.name === name ? 'ring-2 ring-field' : ''"
              data-testid="approval-row"
              :data-approval-kind="row.kind"
              :data-approval-name="row.name"
            >
              <button
                type="button"
                class="flex w-full min-w-0 cursor-pointer items-center gap-3 p-3 text-left"
                :aria-expanded="row.name === name"
                @click="row.name === name ? closeDetail() : open(row)"
              >
                <span
                  class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                  aria-hidden="true"
                >{{ row.initials }}</span>

                <span class="min-w-0 flex-1">
                  <span class="block truncate font-medium text-ink-gray-9">
                    {{ row.employee_name }}
                  </span>
                  <span class="block truncate text-sm text-ink-gray-6">
                    {{ rowSummary(row) }}
                  </span>
                </span>

                <span class="shrink-0 text-right">
                  <span class="tabular block font-medium text-ink-gray-9">{{ rowAmount(row) }}</span>
                  <span class="tabular block text-xs text-ink-gray-5">{{ ageLabel(row) }}</span>
                </span>

                <Icon
                  v-if="!isDesktop"
                  name="chevronRight"
                  class="shrink-0 text-ink-gray-4 transition-transform duration-200"
                  :class="row.name === name ? 'rotate-90' : ''"
                />
              </button>

              <!-- Phone: the selected item opens where it is, so the manager
                   never loses their place in the queue (P2-U7 step 7). -->
              <div
                v-if="!isDesktop && row.name === name"
                class="border-t border-outline-gray-2 px-3 pb-3"
              >
                <AsyncState
                  section="approvals-detail"
                  class="pt-3"
                  :resource="detail"
                  :empty="!detail.data"
                  empty-title="That request isn't here any more"
                  empty-body="It may have been withdrawn or already decided."
                  skeleton="block"
                  skeleton-height="h-40"
                >
                  <template #error-title>
                    We couldn't load this request
                  </template>
                  <div v-if="selected">
                    <!-- The 7-day hours strip: the shape of the week, before
                         the numbers under it. -->
                    <div
                      v-if="selected.kind === 'timesheet'"
                      class="flex gap-1.5"
                      aria-hidden="true"
                    >
                      <div
                        v-for="(day, index) in selected.day_totals"
                        :key="day.date"
                        class="min-w-0 flex-1 text-center"
                      >
                        <p class="label mb-1">
                          {{ DAY_LETTERS[index] }}
                        </p>
                        <div class="flex h-10 items-end justify-center rounded bg-surface-gray-2">
                          <div
                            class="w-full rounded bg-blue-500"
                            :style="{ height: day.hours ? barHeight(day.hours) : '0' }"
                          />
                        </div>
                        <p class="tabular mt-1 text-xs text-ink-gray-6">
                          {{ day.hours || '–' }}
                        </p>
                      </div>
                    </div>
                    <p
                      v-if="selected.kind === 'timesheet'"
                      class="sr-only"
                    >
                      <span
                        v-for="(day, index) in selected.day_totals"
                        :key="day.date"
                      >{{ DAY_LETTERS[index] }} {{ day.hours }} hours.
                      </span>
                    </p>


                    <!-- Evidence, then the decision. Never the other way
                         round: the buttons live below everything that
                         justifies them (P2-AE6). -->
                    <dl
                      v-if="selected.kind === 'timesheet'"
                      class="mt-3 divide-y divide-outline-gray-2 border-t border-outline-gray-2"
                    >
                      <div
                        v-for="line in selected.lines"
                        :key="`${line.project}:${line.task}`"
                        class="flex items-baseline justify-between gap-3 py-2"
                      >
                        <dt class="min-w-0 text-sm">
                          <span class="font-medium text-ink-gray-9">{{ line.project_name }}</span>
                          <span
                            v-if="line.task_subject"
                            class="text-ink-gray-6"
                          > · {{ line.task_subject }}</span>
                        </dt>
                        <dd class="tabular shrink-0 text-sm text-ink-gray-9">
                          {{ line.total }}
                        </dd>
                      </div>
                      <div class="flex items-baseline justify-between gap-3 py-2">
                        <dt class="text-sm font-medium text-ink-gray-9">
                          Week total
                        </dt>
                        <dd class="tabular shrink-0 font-bold text-ink-gray-9">
                          {{ selected.total_hours }}
                        </dd>
                      </div>
                    </dl>

                    <dl
                      v-else
                      class="border-t border-outline-gray-2 pt-3 text-sm"
                    >
                      <div class="flex justify-between gap-3">
                        <dt class="text-ink-gray-6">
                          {{ selected.leave_type }}
                        </dt>
                        <dd class="text-ink-gray-9">
                          {{ formatDateRange(selected.from_date, selected.to_date) }}
                        </dd>
                      </div>
                      <div class="mt-1 flex justify-between gap-3">
                        <dt class="text-ink-gray-6">
                          Days
                        </dt>
                        <dd class="tabular text-ink-gray-9">
                          {{ selected.total_days }}
                          <span v-if="selected.half_day">(half day)</span>
                        </dd>
                      </div>
                      <div class="mt-1 flex justify-between gap-3">
                        <dt class="text-ink-gray-6">
                          Status
                        </dt>
                        <dd>
                          <StatusBadge
                            kind="leave"
                            :status="selected.status"
                          />
                        </dd>
                      </div>
                    </dl>

                    <p
                      v-if="selected.note || selected.reason"
                      class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
                    >
                      {{ firstName }}: “{{ selected.note || selected.reason }}”
                    </p>

                    <p
                      v-if="actionError"
                      class="surface-alert mt-3 p-3 text-sm"
                      role="alert"
                    >
                      {{ actionError }}
                    </p>

                    <div
                      v-if="showReason"
                      class="mt-3"
                    >
                      <FormControl
                        v-model="reason"
                        type="textarea"
                        :label="`What should ${firstName} change?`"
                        required
                      />
                      <p
                        v-if="reasonError"
                        class="mt-1 text-sm text-ink-red-4"
                        role="alert"
                      >
                        {{ reasonError }}
                      </p>
                    </div>

                    <div class="mt-3 flex flex-wrap gap-2">
                      <Button
                        variant="solid"
                        theme="green"
                        :loading="acting === selected.name"
                        :disabled="acting === selected.name"
                        @click="decide('Approve')"
                      >
                        {{ approveLabel }}
                      </Button>
                      <Button
                        variant="outline"
                        theme="red"
                        :disabled="acting === selected.name"
                        @click="showReason ? decide('Reject') : askForReason()"
                      >
                        Send back
                      </Button>
                    </div>
                  </div>
                </AsyncState>
              </div>
            </li>
          </ul>

          <p
            v-if="overflow"
            class="mt-3 text-sm text-ink-gray-5"
          >
            Showing the <span class="tabular">{{ pending.length }}</span> oldest.
            <span class="tabular">{{ overflow }}</span> more are waiting.
          </p>
        </AsyncState>

        <!-- Decided this week: a receipt, so a manager can see that what they
             did actually happened. -->
        <section
          v-if="decided.length"
          class="mt-6"
          aria-labelledby="approvals-decided-heading"
        >
          <h2
            id="approvals-decided-heading"
            class="label mb-2"
          >
            Decided this week
          </h2>
          <ul class="space-y-2">
            <li
              v-for="row in decided"
              :key="row.id"
              class="surface-card elev-1 flex items-center gap-3 p-3"
            >
              <span
                class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                aria-hidden="true"
              >{{ row.initials }}</span>
              <p class="min-w-0 flex-1 truncate text-sm text-ink-gray-7">
                {{ row.employee_name }} · {{ row.label }}
                · {{ formatDateRange(row.from_date, row.to_date) }}
              </p>
              <StatusBadge
                :kind="row.kind"
                :status="row.status"
              />
            </li>
          </ul>
        </section>
      </div>

      <!-- Desktop: the full evidence beside the queue. Approve is not on
           screen at all until it has loaded, which is the whole of P2-AE6 --
           the decision is only available once the thing being decided is
           visible. -->
      <aside
        v-if="name && isDesktop"
        class="min-w-0 lg:w-[36rem] lg:shrink-0"
        data-testid="approval-detail"
      >
        <AsyncState
          section="approvals-detail"
          :resource="detail"
          :empty="!detail.data"
          empty-title="That request isn't here any more"
          empty-body="It may have been withdrawn or already decided."
          skeleton="block"
          skeleton-height="h-80"
        >
          <template #error-title>
            We couldn't load this request
          </template>

          <article
            v-if="selected"
            class="surface-card elev-1 p-4"
            aria-label="Request detail"
          >
            <div class="flex items-start gap-3">
              <span
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                aria-hidden="true"
              >{{ selected.initials }}</span>
              <div class="min-w-0 flex-1">
                <h2 class="font-heading text-lg font-bold text-ink-gray-9">
                  {{ selected.employee_name }}
                </h2>
                <p class="text-sm text-ink-gray-6">
                  <template v-if="selected.kind === 'timesheet'">
                    Timesheet · {{ formatDateRange(selected.week_start, selected.week_end) }}
                  </template>
                  <template v-else>
                    {{ selected.leave_type }} ·
                    {{ formatDateRange(selected.from_date, selected.to_date) }}
                  </template>
                  <template v-if="selected.sent_on">
                    · sent {{ formatDate(selected.sent_on) }}
                  </template>
                </p>
              </div>
              <p
                v-if="selected.kind === 'timesheet'"
                class="shrink-0 text-right"
              >
                <span class="tabular font-heading text-2xl font-bold text-ink-gray-9">
                  {{ selected.total_hours }}
                </span>
                <span class="tabular text-sm text-ink-gray-5"> / {{ selected.full_week_hours }} h</span>
              </p>
              <StatusBadge
                v-else
                kind="leave"
                :status="selected.status"
              />
            </div>

            <!-- The week, project by project and day by day. This table is
                 the evidence; it scrolls inside its own container rather than
                 widening the page (P2-R3). -->
            <div
              v-if="selected.kind === 'timesheet'"
              class="mt-4 overflow-x-auto"
            >
              <table class="w-full min-w-[34rem] text-sm">
                <thead>
                  <tr class="border-b border-outline-gray-2">
                    <th
                      scope="col"
                      class="label py-2 text-left"
                    >
                      Project / task
                    </th>
                    <th
                      v-for="(day, index) in selected.day_totals"
                      :key="day.date"
                      scope="col"
                      class="label py-2 text-right"
                    >
                      {{ DAY_LETTERS[index] }}
                    </th>
                    <th
                      scope="col"
                      class="label py-2 text-right"
                    >
                      Total
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="line in selected.lines"
                    :key="`${line.project}:${line.task}`"
                    class="border-b border-outline-gray-2"
                  >
                    <th
                      scope="row"
                      class="py-2 pr-3 text-left font-medium text-ink-gray-9"
                    >
                      {{ line.project_name }}
                      <span
                        v-if="line.task_subject"
                        class="block text-xs font-normal text-ink-gray-6"
                      >{{ line.task_subject }}</span>
                    </th>
                    <td
                      v-for="day in selected.day_totals"
                      :key="day.date"
                      class="tabular py-2 text-right text-ink-gray-7"
                    >
                      {{ line.hours_by_date[day.date] || '–' }}
                    </td>
                    <td class="tabular py-2 text-right font-medium text-ink-gray-9">
                      {{ line.total }}
                    </td>
                  </tr>
                  <tr class="bg-surface-gray-2">
                    <th
                      scope="row"
                      class="py-2 pr-3 text-left font-medium text-ink-gray-9"
                    >
                      Day total
                    </th>
                    <td
                      v-for="day in selected.day_totals"
                      :key="day.date"
                      class="tabular py-2 text-right text-ink-gray-9"
                    >
                      {{ day.hours }}
                    </td>
                    <td class="tabular py-2 text-right font-bold text-ink-gray-9">
                      {{ selected.total_hours }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <dl
              v-else
              class="mt-4 grid grid-cols-2 gap-4"
            >
              <div>
                <dt class="label">
                  From
                </dt>
                <dd class="mt-0.5 text-sm text-ink-gray-9">
                  {{ formatDate(selected.from_date) }}
                </dd>
              </div>
              <div>
                <dt class="label">
                  To
                </dt>
                <dd class="mt-0.5 text-sm text-ink-gray-9">
                  {{ formatDate(selected.to_date) }}
                </dd>
              </div>
              <div>
                <dt class="label">
                  Days
                </dt>
                <dd class="tabular mt-0.5 text-sm text-ink-gray-9">
                  {{ selected.total_days }}
                  <span v-if="selected.half_day">(half day)</span>
                </dd>
              </div>
              <div>
                <dt class="label">
                  Left after this
                </dt>
                <dd class="tabular mt-0.5 text-sm text-ink-gray-9">
                  {{ selected.leave_balance }}
                </dd>
              </div>
            </dl>

            <p
              v-if="selected.note || selected.reason"
              class="surface-inset mt-4 p-3 text-sm text-ink-gray-7"
            >
              {{ firstName }}: “{{ selected.note || selected.reason }}”
            </p>

            <p
              v-if="actionError"
              class="surface-alert mt-4 p-3 text-sm"
              role="alert"
            >
              {{ actionError }}
            </p>

            <!-- Send back needs its reason on the same surface as the button,
                 not behind a dialog: the employee reads this sentence, so it
                 is written next to the evidence it is about (P2-U7 step 4). -->
            <div class="mt-4 border-t border-outline-gray-2 pt-4">
              <FormControl
                v-model="reason"
                type="textarea"
                label="Send back with a reason (required to send back)"
                :placeholder="`What should ${firstName} change?`"
              />
              <p
                v-if="reasonError"
                class="mt-1 text-sm text-ink-red-4"
                role="alert"
              >
                {{ reasonError }}
              </p>
            </div>

            <div class="mt-4 flex flex-wrap justify-end gap-2">
              <Button
                variant="outline"
                theme="red"
                :disabled="acting === selected.name"
                @click="decide('Reject')"
              >
                Send back
              </Button>
              <Button
                variant="solid"
                theme="green"
                :loading="acting === selected.name"
                :disabled="acting === selected.name"
                @click="decide('Approve')"
              >
                {{ approveLabel }}
              </Button>
            </div>
          </article>
        </AsyncState>
      </aside>
    </div>
  </div>
</template>
