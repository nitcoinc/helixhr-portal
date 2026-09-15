<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import { attachToRequestReply } from '@/lib/api'
import { session } from '@/lib/session'
import { formatDate, formatDateRange, formatDateTime } from '@/lib/dates'
import { toPlainLeaveError } from '@/lib/errorMap'
import { useIsDesktop } from '@/lib/useIsDesktop'
import { ATTENDANCE_LABEL } from '@/lib/week'

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

// P4-R11. The queue's own sentence used to promise "you only see people who
// report to you", which is still true for a line manager and false for an HR
// Manager, whose queue is their reports *plus* everything handed to HR. The
// rows themselves are the honest signal, so the sentence follows them rather
// than asking the server a second question about roles.
const hasHrWork = computed(() => pending.value.some((row) => row.for_hr))

// P5-R12: a routed request has a claim state the other three kinds do not --
// nobody "picks up" a leave application. The counts are read off the one flat
// queue rather than a second request, so they can never disagree with what is
// actually in `pending`.
const requestRows = computed(() => pending.value.filter((row) => row.kind === 'request'))
const unclaimedRequestCount = computed(
  () => requestRows.value.filter((row) => !row.picked_up_by).length,
)
const myRequestCount = computed(
  () => requestRows.value.filter((row) => row.picked_up_by === session.user).length,
)

/** The claim chip on a request row: who has it, or that nobody does yet. */
function requestClaimLabel(row) {
  if (row.kind !== 'request') return ''
  if (!row.picked_up_by) return 'Unclaimed'
  return row.picked_up_by === session.user ? 'You have this' : 'Picked up'
}

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

const isDesktop = useIsDesktop()

// --- deciding ------------------------------------------------------------

const act = createResource({ url: 'helixhr.api.act_on_approval', method: 'POST' })
const acting = ref('') // the one item in flight, by record name
const actionError = ref('')
const reason = ref('')
const reasonError = ref('')

// P4-U4 / P5-U11. Which of the three reason-bearing outcomes the one reason
// surface is currently armed for: '' (closed), 'Send Back', 'Reject' or
// 'Need info'. One surface, not three, and it knows which button will fire
// it -- see `openReason`.
const reasonFor = ref('')
// Send to HR's note is optional and is not a reason, so it gets its own
// smaller field rather than borrowing the required one above.
const noteOpen = ref(false)
const note = ref('')

// P4-R1 / P4-KTD6. The outcomes the *server* says are legal for this record
// and this approver. The screen renders exactly this list: a button that is
// absent is not drawn, not drawn-and-disabled, because a disabled Reject on
// a timesheet would still teach a manager that timesheets can be rejected.
const actions = computed(() => selected.value?.actions || [])

function may(action) {
  return actions.value.includes(action)
}

function clearDecisionSurfaces() {
  reason.value = ''
  reasonError.value = ''
  reasonFor.value = ''
  note.value = ''
  noteOpen.value = false
}

// The selected record drives the fetch, and clears whatever the last
// decision left behind -- a half-typed reason must never follow the manager
// onto somebody else's record. Declared here rather than beside the resource
// because `immediate: true` runs during setup, and the refs it resets are
// below.
watch(
  () => [props.kind, props.name].join('/'),
  () => {
    actionError.value = ''
    clearDecisionSurfaces()
    if (props.name) detail.fetch()
  },
  { immediate: true },
)

/**
 * Arm the one reason surface for one outcome (P4-U4).
 *
 * Switching from Send back to Reject -- or back -- **clears the typed reason
 * and its error and relabels the field**. That is the point of the reset, not
 * tidiness: a sentence written to tell somebody what to change ("add the
 * Friday hours") must never be submittable as the justification for a
 * terminal no. The two outcomes are different messages to a person, so they
 * never share a draft.
 */
function openReason(action) {
  actionError.value = ''
  noteOpen.value = false
  if (reasonFor.value !== action) {
    reason.value = ''
    reasonError.value = ''
    reasonFor.value = action
  }
}

function openNote() {
  actionError.value = ''
  reason.value = ''
  reasonError.value = ''
  reasonFor.value = ''
  noteOpen.value = true
}

// What the reason field is called, per outcome. The label says which button
// the sentence is going to, so the field can never be read as the other one.
const REASON_COPY = {
  'Send Back': {
    heading: 'Send back with a reason',
    placeholder: 'What should they change?',
    // The server's own sentence, so the screen and the refusal agree.
    missing: 'Say what should change before sending it back.',
  },
  Reject: {
    heading: 'Reject with a reason',
    placeholder: 'Why is the answer final?',
    missing: 'Say why before rejecting this.',
  },
  'Need info': {
    heading: 'Ask a question',
    placeholder: 'What do you need from them?',
    missing: 'Say what you need before sending this.',
  },
}

const reasonCopy = computed(() => REASON_COPY[reasonFor.value] || REASON_COPY['Send Back'])

/** The field's own label, naming the person the sentence is written to. */
const reasonPlaceholder = computed(() =>
  reasonFor.value === 'Send Back'
    ? `What should ${firstName.value || 'they'} change?`
    : reasonCopy.value.placeholder,
)

/**
 * P2-U7 steps 3 and 4. One decision at a time, always carrying the `modified`
 * and the state the evidence on screen was rendered from, so a decision made
 * against a record that has since moved is refused by the server instead of
 * overwriting somebody else's.
 *
 * P4-U4 gives the same function four outcomes. Send back and Reject open the
 * shared reason surface on the first tap and fire on the second; Send to HR
 * opens its optional note the same way, so every outcome that carries words
 * is confirmed once. Approve carries none and goes straight through.
 */
async function decide(action) {
  const item = selected.value
  // Double-tap protection is here rather than only on `:disabled`: a second
  // pointerdown can land before Vue has flushed the disabled attribute
  // (P2-U7 scenario 3).
  if (!item || acting.value) return
  // Belt and braces over the render: `actions` is what draws the row, but a
  // stale detail behind a slow reload must not be able to fire an outcome the
  // server would refuse anyway.
  if (!may(action)) return

  let comment
  if (action === 'Send Back' || action === 'Reject' || action === 'Need info') {
    if (reasonFor.value !== action) {
      openReason(action)
      return
    }
    comment = reason.value.trim()
    if (!comment) {
      reasonError.value = reasonCopy.value.missing
      return
    }
  } else if (action === 'Send to HR') {
    if (!noteOpen.value) {
      openNote()
      return
    }
    comment = note.value.trim() || undefined
  }

  actionError.value = ''
  reasonError.value = ''
  acting.value = item.name
  try {
    await act.submit({
      doctype: item.doctype,
      name: item.name,
      action,
      comment,
      expected_modified: item.modified,
      expected_state: item.state,
    })
    clearDecisionSurfaces()
    closeDetail()
    queue.reload()
  } catch (error) {
    // Stale, already decided, reassigned or refused. The queue is reloaded
    // so the item that is genuinely gone leaves -- but nothing else on the
    // list is touched, and the manager is told what happened rather than
    // being shown a queue that silently changed under them.
    // P3-U6 scenario 7. HRMS reports two of its Attendance Request
    // refusals as an HTML table or a list of lists, not as a sentence, and
    // both can arrive mid-decision -- an overlapping request, or a range
    // that would mark nothing. `toPlainLeaveError` flattens either into one
    // readable line and maps the sentences this app has plain words for --
    // including "send this to HR instead", which is how a manager's Approve
    // is refused once a day in the range already has attendance (P4-KTD5).
    actionError.value =
      toPlainLeaveError(error) || "We couldn't record that decision. Reload and try again."
    queue.reload()
    if (props.name) detail.fetch()
  } finally {
    acting.value = ''
  }
}

// --- request attachments (P5-R10a) ---------------------------------------
//
// The worker's side of the conversation's attachments -- what makes an HR
// Letter completable without opening Desk. A plain file input rather than a
// resource: `attachToRequestReply` is a multipart POST, the same shape
// `RequestForm.vue` already uses for the employee's own upload.

const attachingReply = ref(false)
const attachReplyError = ref('')
const requestFileInputMobile = ref(null)
const requestFileInputDesktop = ref(null)

async function attachReplyFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !selected.value) return
  attachingReply.value = true
  attachReplyError.value = ''
  try {
    await attachToRequestReply(file, { name: selected.value.name })
    detail.fetch()
  } catch (error) {
    attachReplyError.value = error?.messages?.[0] || 'That file did not upload.'
  } finally {
    attachingReply.value = false
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

// P3-U6 step 0. One entry per kind, named explicitly. These three used to
// branch on `kind === 'leave'` and let everything else fall through to the
// timesheet copy, so the attendance request P3-U5 added would have arrived in
// this queue reading "Timesheet · 3 September" with an hours total of
// `undefined h`. A kind missing from this map is now visible as itself.
const KIND = {
  leave: {
    summary: (row) => `${row.leave_type} · ${formatDateRange(row.from_date, row.to_date)}`,
    amount: (row) => `${row.total_days} day${row.total_days === 1 ? '' : 's'}`,
    approve: (item) => `Approve ${item.total_days} day${item.total_days === 1 ? '' : 's'}`,
    quote: (item) => item.reason,
  },
  timesheet: {
    summary: (row) => `Timesheet · ${formatDateRange(row.from_date, row.to_date)}`,
    amount: (row) => `${row.total_hours} h`,
    approve: (item) => `Approve ${item.total_hours} h`,
    quote: (item) => item.note,
  },
  attendance: {
    summary: (row) => `${row.reason} · ${formatDateRange(row.from_date, row.to_date)}`,
    amount: (row) => `${row.total_days} day${row.total_days === 1 ? '' : 's'}`,
    // P4-R6. Attendance requests are approved in one step now, so this
    // button is the decision again rather than a hand-over: the manager's
    // Approve submits the request and writes the Attendance, and the
    // quantity it commits belongs on the control that commits it. Handing
    // one to HR is its own button (P4-R5), not this one wearing HR's name.
    approve: (item) => `Approve ${item.total_days} day${item.total_days === 1 ? '' : 's'}`,
    quote: (item) => item.explanation,
  },
  // P5-U11: a request has no single "amount" (a leave's days, a timesheet's
  // hours) and no `Approve` action at all -- `may('Pick up')`/`may('Done')`
  // draw its buttons instead of `approve`, so that entry is never called.
  // `quote` is the request's own opening line; the full back-and-forth is the
  // conversation block below, not this one-line surface.
  request: {
    summary: (row) => `${row.category} · ${row.subject}`,
    amount: (row) => requestClaimLabel(row),
    approve: () => '',
    // The full back-and-forth renders as its own conversation block, not the
    // shared one-line quote surface every other kind uses.
    quote: () => null,
  },
}

const FALLBACK_KIND = {
  summary: (row) => formatDateRange(row.from_date, row.to_date),
  amount: () => '',
  approve: () => 'Approve',
  quote: () => null,
}

function spec(kind) {
  return KIND[kind] || FALLBACK_KIND
}

function rowSummary(row) {
  return spec(row.kind).summary(row)
}

function rowAmount(row) {
  return spec(row.kind).amount(row)
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
const approveLabel = computed(() =>
  selected.value ? spec(selected.value.kind).approve(selected.value) : 'Approve',
)

/** The employee's own words, wherever this kind keeps them. */
const quote = computed(() => (selected.value ? spec(selected.value.kind).quote(selected.value) : null))

const firstName = computed(() => (selected.value?.employee_name || '').split(/\s+/)[0])

/** P3-R16. What the calendar shows for each day a request names -- the
 * difference between a correction and a request over a day that is already
 * accounted for. */
const DAY_SHOWS = { holiday: 'holiday', weekly_off: 'weekly off' }

function requestedDayLabel(day) {
  if (day.holiday) return DAY_SHOWS[day.holiday] || day.holiday
  if (day.status) return ATTENDANCE_LABEL[day.status] || day.status
  return 'nothing recorded'
}

/** P4-R11. Who handed this to HR, and what they said -- one line, on the
 * queue row and again on the detail head, so the HR chip is never the only
 * thing distinguishing HR's work from a manager's own. Both halves are
 * optional: a request of an HR-approves leave type reached HR with nobody
 * sending it (P4-R7). */
function hrLine(row) {
  if (!row?.for_hr) return ''
  const parts = []
  if (row.sent_to_hr_by) parts.push(`Sent by ${row.sent_to_hr_by}`)
  if (row.hr_note) parts.push(`“${row.hr_note}”`)
  return parts.join(' · ')
}
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
      <template v-if="hasHrWork">
        Oldest first. Your own team, plus anything marked HR.
      </template>
      <template v-else>
        Oldest first. You only see people who report to you.
      </template>
    </p>

    <!-- P5-R12: requests split into what nobody has claimed and what the
         viewer already has, counted separately from the rest of the queue. -->
    <p
      v-if="requestRows.length"
      class="mb-4 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-gray-5"
      data-testid="request-claim-counts"
    >
      <span data-testid="unclaimed-count">
        <span class="tabular font-medium text-ink-gray-9">{{ unclaimedRequestCount }}</span>
        unclaimed
      </span>
      <span data-testid="my-request-count">
        <span class="tabular font-medium text-ink-gray-9">{{ myRequestCount }}</span>
        picked up by you
      </span>
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
          empty-body="Leave, weeks and attendance requests your team sends for approval appear here."
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
                  <span class="flex min-w-0 items-center gap-2">
                    <span class="min-w-0 truncate font-medium text-ink-gray-9">
                      {{ row.employee_name }}
                    </span>
                    <!-- P4-R11. One queue, two hats. The chip is a word, not
                         a tint, because it is the only thing that says whether
                         this row is HR's work or this manager's own. -->
                    <span
                      v-if="row.for_hr"
                      class="shrink-0 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-bold text-ink-gray-7"
                      data-testid="hr-chip"
                    >HR</span>
                  </span>
                  <span class="block truncate text-sm text-ink-gray-6">
                    {{ rowSummary(row) }}
                  </span>
                  <span
                    v-if="hrLine(row)"
                    class="block truncate text-xs text-ink-gray-5"
                  >
                    {{ hrLine(row) }}
                  </span>
                </span>

                <span class="shrink-0 text-right">
                  <span
                    class="tabular block font-medium text-ink-gray-9"
                    :data-testid="row.kind === 'request' ? 'request-claim' : null"
                  >{{ rowAmount(row) }}</span>
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
                      v-else-if="selected.kind === 'leave'"
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
                            :docstatus="selected.docstatus"
                          />
                        </dd>
                      </div>
                    </dl>

                    <!-- P3-R16. The days themselves, and what the calendar
                         already shows for each: the manager is agreeing that
                         these particular days were worked. -->
                    <dl
                      v-else-if="selected.kind === 'attendance'"
                      class="border-t border-outline-gray-2 pt-3 text-sm"
                    >
                      <div class="flex justify-between gap-3">
                        <dt class="text-ink-gray-6">
                          {{ selected.reason }}
                        </dt>
                        <dd class="text-ink-gray-9">
                          {{ formatDateRange(selected.from_date, selected.to_date) }}
                        </dd>
                      </div>
                      <div
                        v-for="day in selected.days"
                        :key="day.date"
                        class="mt-1 flex justify-between gap-3"
                      >
                        <dt class="tabular text-ink-gray-6">
                          {{ formatDate(day.date) }}
                        </dt>
                        <dd class="text-ink-gray-9">
                          {{ requestedDayLabel(day) }}
                        </dd>
                      </div>
                      <div
                        v-if="selected.half_day"
                        class="mt-1 flex justify-between gap-3"
                      >
                        <dt class="text-ink-gray-6">
                          Half day
                        </dt>
                        <dd class="text-ink-gray-9">
                          {{ formatDate(selected.half_day_date) }}
                        </dd>
                      </div>
                    </dl>

                    <!-- P5-R10: the request and every reply after it, one
                         conversation, oldest first. -->
                    <div
                      v-else-if="selected.kind === 'request'"
                      class="border-t border-outline-gray-2 pt-3"
                      data-testid="request-thread"
                    >
                      <p class="text-sm text-ink-gray-6">
                        {{ selected.category }}
                      </p>
                      <ul
                        v-if="selected.thread?.length"
                        class="mt-2 space-y-2"
                      >
                        <li
                          v-for="(entry, index) in selected.thread"
                          :key="index"
                          class="surface-inset p-2 text-sm"
                        >
                          <p class="text-xs font-medium text-ink-gray-5">
                            {{ entry.by === 'employee' ? firstName || 'Employee' : 'Worker' }}
                            · {{ formatDateTime(entry.on) }}
                          </p>
                          <p class="mt-0.5 whitespace-pre-line text-ink-gray-8">
                            {{ entry.message }}
                          </p>
                        </li>
                      </ul>
                      <p
                        v-else
                        class="mt-2 text-sm text-ink-gray-5"
                      >
                        No details were written with this request.
                      </p>

                      <!-- P5-R10a: what makes an HR Letter completable
                           without opening Desk. -->
                      <div
                        v-if="actions.length"
                        class="mt-3"
                      >
                        <Button
                          variant="outline"
                          :loading="attachingReply"
                          :disabled="attachingReply"
                          @click="requestFileInputMobile?.click()"
                        >
                          Attach a file
                        </Button>
                        <input
                          ref="requestFileInputMobile"
                          type="file"
                          class="sr-only"
                          data-testid="attach-reply-input"
                          @change="attachReplyFile"
                        >
                        <p
                          v-if="attachReplyError"
                          class="mt-1 text-sm text-signal"
                          role="alert"
                        >
                          {{ attachReplyError }}
                        </p>
                      </div>
                    </div>

                    <p
                      v-if="quote"
                      class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
                    >
                      {{ firstName }}: “{{ quote }}”
                    </p>

                    <p
                      v-if="actionError"
                      class="surface-alert mt-3 p-3 text-sm"
                      role="alert"
                    >
                      {{ actionError }}
                    </p>

                    <!-- P4-U4. One reason surface, armed for one outcome at a
                         time, on the deep field where the portal's other
                         anchored write regions live. -->
                    <div
                      v-if="reasonFor"
                      class="surface-field elev-2 mt-3 p-3"
                      data-testid="decision-reason"
                    >
                      <p class="text-sm font-medium text-white">
                        {{ reasonCopy.heading }}
                      </p>
                      <FormControl
                        v-model="reason"
                        class="mt-2"
                        type="textarea"
                        :placeholder="reasonPlaceholder"
                        :aria-label="reasonCopy.heading"
                        required
                      />
                      <p
                        v-if="reasonError"
                        class="mt-1 text-sm font-medium text-signal"
                        role="alert"
                      >
                        {{ reasonError }}
                      </p>
                    </div>

                    <!-- Send to HR is a routing act, so its words are a note
                         to a colleague and optional (P4-R5). -->
                    <div
                      v-if="noteOpen"
                      class="mt-3"
                      data-testid="hr-note"
                    >
                      <FormControl
                        v-model="note"
                        type="textarea"
                        label="Anything HR should know? (optional)"
                        placeholder="Why this needs HR"
                      />
                    </div>

                    <div
                      class="mt-3 flex flex-wrap items-center gap-2"
                      data-testid="decision-actions"
                    >
                      <Button
                        v-if="may('Approve')"
                        variant="solid"
                        theme="green"
                        :loading="acting === selected.name"
                        :disabled="acting === selected.name"
                        @click="decide('Approve')"
                      >
                        {{ approveLabel }}
                      </Button>
                      <Button
                        v-if="may('Pick up')"
                        variant="solid"
                        theme="green"
                        :loading="acting === selected.name"
                        :disabled="acting === selected.name"
                        data-testid="pick-up"
                        @click="decide('Pick up')"
                      >
                        Pick up
                      </Button>
                      <Button
                        v-if="may('Done')"
                        variant="solid"
                        theme="green"
                        :loading="acting === selected.name"
                        :disabled="acting === selected.name"
                        data-testid="done"
                        @click="decide('Done')"
                      >
                        Done
                      </Button>
                      <Button
                        v-if="may('Need info')"
                        variant="outline"
                        :disabled="acting === selected.name"
                        data-testid="need-info"
                        @click="decide('Need info')"
                      >
                        Need info
                      </Button>
                      <Button
                        v-if="may('Send Back')"
                        variant="outline"
                        :disabled="acting === selected.name"
                        data-testid="send-back"
                        @click="decide('Send Back')"
                      >
                        Send back
                      </Button>
                      <Button
                        v-if="may('Reject')"
                        variant="outline"
                        theme="red"
                        :disabled="acting === selected.name"
                        data-testid="reject"
                        @click="decide('Reject')"
                      >
                        Reject
                      </Button>
                      <Button
                        v-if="may('Send to HR')"
                        variant="ghost"
                        :disabled="acting === selected.name"
                        data-testid="send-to-hr"
                        @click="decide('Send to HR')"
                      >
                        Send to HR
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
                :docstatus="row.docstatus"
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
                <h2 class="flex min-w-0 items-center gap-2 font-heading text-lg font-bold text-ink-gray-9">
                  <span class="min-w-0 truncate">{{ selected.employee_name }}</span>
                  <span
                    v-if="selected.for_hr"
                    class="shrink-0 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-bold text-ink-gray-7"
                    data-testid="hr-chip"
                  >HR</span>
                </h2>
                <!-- P4-R11: the same line the queue row carries, so opening
                     an HR row does not lose who handed it over or why. -->
                <p
                  v-if="hrLine(selected)"
                  class="text-sm text-ink-gray-5"
                >
                  {{ hrLine(selected) }}
                </p>
                <p class="text-sm text-ink-gray-6">
                  <template v-if="selected.kind === 'timesheet'">
                    Timesheet · {{ formatDateRange(selected.week_start, selected.week_end) }}
                  </template>
                  <template v-else-if="selected.kind === 'attendance'">
                    {{ selected.reason }} ·
                    {{ formatDateRange(selected.from_date, selected.to_date) }}
                  </template>
                  <template v-else-if="selected.kind === 'request'">
                    {{ selected.category }} · {{ selected.subject }}
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
                :kind="selected.kind"
                :status="selected.status"
                :docstatus="selected.docstatus"
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

            <!-- P3-R16. One row per day the request names, with what the
                 calendar shows for it beside the date. -->
            <div
              v-else-if="selected.kind === 'attendance'"
              class="mt-4"
            >
              <p
                v-if="!selected.working_days_known"
                class="surface-alert mb-3 p-3 text-sm"
              >
                We can't tell which of these are working days --
                {{ firstName }} has no holiday list. Ask HR before deciding.
              </p>
              <dl class="divide-y divide-outline-gray-2 border-t border-outline-gray-2">
                <div
                  v-for="day in selected.days"
                  :key="day.date"
                  class="flex items-baseline justify-between gap-3 py-2"
                >
                  <dt class="tabular text-sm text-ink-gray-9">
                    {{ formatDate(day.date) }}
                  </dt>
                  <dd class="text-sm text-ink-gray-6">
                    the calendar shows {{ requestedDayLabel(day) }}
                  </dd>
                </div>
                <div
                  v-if="selected.half_day"
                  class="flex items-baseline justify-between gap-3 py-2"
                >
                  <dt class="text-sm text-ink-gray-9">
                    Half day
                  </dt>
                  <dd class="text-sm text-ink-gray-6">
                    {{ formatDate(selected.half_day_date) }}
                  </dd>
                </div>
              </dl>
            </div>

            <dl
              v-else-if="selected.kind === 'leave'"
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

            <!-- P5-R10 / P5-R10a: the request and every reply after it, plus
                 the routed worker's own attach affordance -- the desktop twin
                 of the phone conversation block above. -->
            <div
              v-else-if="selected.kind === 'request'"
              class="mt-4"
              data-testid="request-thread"
            >
              <p class="text-sm text-ink-gray-6">
                {{ selected.category }}
              </p>
              <ul
                v-if="selected.thread?.length"
                class="mt-2 space-y-2"
              >
                <li
                  v-for="(entry, index) in selected.thread"
                  :key="index"
                  class="surface-inset p-3 text-sm"
                >
                  <p class="text-xs font-medium text-ink-gray-5">
                    {{ entry.by === 'employee' ? firstName || 'Employee' : 'Worker' }}
                    · {{ formatDateTime(entry.on) }}
                  </p>
                  <p class="mt-0.5 whitespace-pre-line text-ink-gray-8">
                    {{ entry.message }}
                  </p>
                </li>
              </ul>
              <p
                v-else
                class="mt-2 text-sm text-ink-gray-5"
              >
                No details were written with this request.
              </p>

              <div
                v-if="actions.length"
                class="mt-3"
              >
                <Button
                  variant="outline"
                  :loading="attachingReply"
                  :disabled="attachingReply"
                  @click="requestFileInputDesktop?.click()"
                >
                  Attach a file
                </Button>
                <input
                  ref="requestFileInputDesktop"
                  type="file"
                  class="sr-only"
                  data-testid="attach-reply-input"
                  @change="attachReplyFile"
                >
                <p
                  v-if="attachReplyError"
                  class="mt-1 text-sm text-signal"
                  role="alert"
                >
                  {{ attachReplyError }}
                </p>
              </div>
            </div>

            <p
              v-if="quote"
              class="surface-inset mt-4 p-3 text-sm text-ink-gray-7"
            >
              {{ firstName }}: “{{ quote }}”
            </p>

            <p
              v-if="actionError"
              class="surface-alert mt-4 p-3 text-sm"
              role="alert"
            >
              {{ actionError }}
            </p>

            <!-- Send back and Reject need their reason on the same surface as
                 the button, not behind a dialog: the employee reads this
                 sentence, so it is written next to the evidence it is about
                 (P2-U7 step 4).
                 P4-U4: one surface for both, armed by whichever button opened
                 it and relabelled -- and emptied -- if the other one does. -->
            <div
              v-if="reasonFor"
              class="surface-field elev-2 mt-4 p-4"
              data-testid="decision-reason"
            >
              <p class="text-sm font-medium text-white">
                {{ reasonCopy.heading }}
              </p>
              <FormControl
                v-model="reason"
                class="mt-2"
                type="textarea"
                :placeholder="reasonPlaceholder"
                :aria-label="reasonCopy.heading"
                required
              />
              <p
                v-if="reasonError"
                class="mt-1 text-sm font-medium text-signal"
                role="alert"
              >
                {{ reasonError }}
              </p>
            </div>

            <div
              v-if="noteOpen"
              class="mt-4"
              data-testid="hr-note"
            >
              <FormControl
                v-model="note"
                type="textarea"
                label="Anything HR should know? (optional)"
                placeholder="Why this needs HR"
              />
            </div>

            <!-- P4-R1. Exactly the outcomes the server allows, in one order
                 on every kind: the decision, the recoverable no, the final no,
                 the hand-over. An outcome missing from `actions` is not
                 rendered rather than rendered disabled -- a greyed-out Reject
                 on a timesheet would still teach a manager that a week can be
                 rejected, which it cannot (P4-KTD2). -->
            <div
              class="mt-4 flex flex-wrap items-center justify-end gap-2"
              data-testid="decision-actions"
            >
              <Button
                v-if="may('Send to HR')"
                variant="ghost"
                :disabled="acting === selected.name"
                data-testid="send-to-hr"
                @click="decide('Send to HR')"
              >
                Send to HR
              </Button>
              <Button
                v-if="may('Reject')"
                variant="outline"
                theme="red"
                :disabled="acting === selected.name"
                data-testid="reject"
                @click="decide('Reject')"
              >
                Reject
              </Button>
              <Button
                v-if="may('Need info')"
                variant="outline"
                :disabled="acting === selected.name"
                data-testid="need-info"
                @click="decide('Need info')"
              >
                Need info
              </Button>
              <Button
                v-if="may('Send Back')"
                variant="outline"
                :disabled="acting === selected.name"
                data-testid="send-back"
                @click="decide('Send Back')"
              >
                Send back
              </Button>
              <Button
                v-if="may('Pick up')"
                variant="solid"
                theme="green"
                :loading="acting === selected.name"
                :disabled="acting === selected.name"
                data-testid="pick-up"
                @click="decide('Pick up')"
              >
                Pick up
              </Button>
              <Button
                v-if="may('Done')"
                variant="solid"
                theme="green"
                :loading="acting === selected.name"
                :disabled="acting === selected.name"
                data-testid="done"
                @click="decide('Done')"
              >
                Done
              </Button>
              <Button
                v-if="may('Approve')"
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
