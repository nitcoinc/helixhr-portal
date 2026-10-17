<script setup>
import Avatar from '@/components/Avatar.vue'
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import TabBar from '@/components/TabBar.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import DecisionActions from '@/components/approvals/DecisionActions.vue'
import KindTag from '@/components/KindTag.vue'
import { KIND_TAGS, getReasonCopy } from '@/lib/workKinds'
import TimesheetOversight from '@/components/approvals/TimesheetOversight.vue'
import { APPROVER_REASON_TEXT, groupByPerson, laneSections, laneSubsections } from '@/lib/approvalLanes'
import { session } from '@/lib/session'
import { decrementApprovals, setApprovalsCount } from '@/lib/approvalsCount'
import { formatDate, formatDateRange } from '@/lib/dates'
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

const route = useRoute()
const router = useRouter()

// P2-U7 step 1 / P2-R27. One session-scoped read, replacing the two the page
// used to make itself. The timesheet half of that was
// `frappe.client.get_list` with `filters: { workflow_state: 'Pending
// Approval' }` and no employee scope at all -- a caller-controlled generic
// read whose only limit was whatever Frappe happened to allow, and which did
// not even exclude the manager's own week. The server now decides what is in
// this queue, using the same rules that decide who may act on it.
// U6 / R13. The chip lives in the URL query, so a link reproduces the view;
// the server filters and counts, the browser only says which chip is on.
const kindFilter = computed(() => route.query.kind || '')
const queue = createResource({
  url: 'helixhr.api.get_my_approvals',
  makeParams: () => ({
    kind: kindFilter.value || undefined,
  }),
  auto: true,
})
watch(kindFilter, () => queue.reload())

// Plan 2026-10-17-001 KTD8: every load is the shell's badge, which is never
// polled. `total` counts the chip's kind only, so a filtered view sums the
// per-kind counts, which always cover the whole queue.
watch(
  () => queue.data,
  (data) => {
    if (!data) return
    const total = kindFilter.value
      ? (data.counts?.kinds || []).reduce((sum, kind) => sum + (kind.count || 0), 0)
      : data.total
    setApprovalsCount(total, data.total_is_capped)
  },
)

// Plan 2026-10-16-001 R16: in Leave & attendance tab, chips filter leave / attendance.
const kindChips = computed(() =>
  (queue.data?.counts?.kinds || []).filter(
    (chip) => (chip.name === 'leave' || chip.name === 'attendance') && (chip.count || chip.name === kindFilter.value),
  ),
)

const kindOptions = computed(() => [
  { value: '', label: 'All' },
  ...kindChips.value.map((chip) => ({ value: chip.name, label: KIND_TAGS[chip.name].label, count: chip.count })),
])

/** Turn one chip on (or the All chip, with neither value), keeping any open
 * detail where it is. */
function filterQueue(kind) {
  router.push({ name: route.name, params: route.params, query: { ...route.query, kind: kind || undefined } })
}

// Plan 2026-10-16-001 R16. Approvals has two tabs: Timesheets and Leave & attendance.
// HR Managers also get All pending. Tab labels carry counts. The default tab is
// the first one with something waiting.
const timesheetCount = computed(() => {
  const kinds = queue.data?.counts?.kinds || []
  const ts = kinds.find((k) => k.name === 'timesheet')?.count || 0
  const ch = kinds.find((k) => k.name === 'change')?.count || 0
  return ts + ch
})
const leaveAttendanceCount = computed(() => {
  const kinds = queue.data?.counts?.kinds || []
  const lv = kinds.find((k) => k.name === 'leave')?.count || 0
  const at = kinds.find((k) => k.name === 'attendance')?.count || 0
  return lv + at
})

const VIEW_TABS = computed(() =>
  [
    (session.canApprove || session.canOverseeTimesheets) && {
      key: 'timesheets',
      label: 'Timesheets',
      count: timesheetCount.value || null,
    },
    session.canApprove && {
      key: 'queue',
      label: 'Leave & attendance',
      count: leaveAttendanceCount.value || null,
    },
    session.canSeeAllPending && { key: 'all-pending', label: 'All pending' },
  ].filter(Boolean),
)
const OVERDUE_VISIBLE_ROWS = 5

const firstWaitingTab = computed(() => {
  if (timesheetCount.value > 0) return 'timesheets'
  if (leaveAttendanceCount.value > 0) return 'queue'
  const names = VIEW_TABS.value.map((tab) => tab.key)
  return names[0] || 'queue'
})
// Chosen once, on the first answer: deciding the last timesheet must not
// swap the tab out from under the approver.
const landedTab = ref('')
watch(
  () => queue.data,
  (data) => {
    if (data && !landedTab.value) landedTab.value = firstWaitingTab.value
  },
  { immediate: true },
)
const defaultTab = computed(() => landedTab.value || firstWaitingTab.value)

/** The open tab: `?view=`, where the old `overdue` is an alias for All
 * pending with its toggle on; default tab is the first one with something waiting. */
const view = computed(() => {
  // A tab this caller does not have -- the `overdue` alias included -- falls
  // back to the default (R14); the server refuses them regardless.
  const asked = route.query.view === 'overdue' ? 'all-pending' : route.query.view
  return VIEW_TABS.value.some((tab) => tab.key === asked) ? asked : defaultTab.value
})
const overdueOnly = computed(() => route.query.view === 'overdue' || route.query.overdue === '1')
function showView(name) {
  router.push({ path: '/approvals', query: name === defaultTab.value ? {} : { view: name } })
}
// A two-chip filter rather than a lone toggle, so both states are named
// (U15, R17). The testid stays on the chip that used to be the toggle.
const OVERDUE_OPTIONS = [
  { value: false, label: 'All' },
  { value: true, label: 'Overdue only', testid: 'overdue-toggle' },
]
function setOverdue(on) {
  router.push({ path: '/approvals', query: { view: 'all-pending', overdue: on ? '1' : undefined } })
}

const allPending = createResource({
  url: 'helixhr.api.get_pending_by_approver',
  makeParams: () => ({ overdue_only: overdueOnly.value ? 1 : 0 }),
})
watch(
  [view, overdueOnly],
  ([value]) => value === 'all-pending' && allPending.fetch(),
  { immediate: true },
)
// Bulk selection belongs to one tab's rows and clears when the tab changes.
watch(view, () => {
  bulkSelected.value = []
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
  router.push({
    name: 'ApprovalDetail',
    params: { kind: row.kind, name: row.name },
    query: route.query,
  })
}

function closeDetail() {
  router.push({ name: 'Approvals', query: route.query })
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
// Plan 2026-10-05-001 U3 (KTD2). Accept cancels an approved week and
// reopens it for the employee, so it never fires on one click: the first
// click opens this confirm, and only its own button carries the decision.
const confirmAccept = ref(false)
watch(confirmAccept, async (open) => {
  if (!open) return
  // Cancel takes the initial focus, after the dialog's own focus trap has
  // placed it on the first tabbable element.
  // Two frames: reka's FocusScope focuses on its own mount tick. The
  // query, not the component ref: frappe-ui's Button `$el` is not the
  // <button> itself.
  await nextTick()
  requestAnimationFrame(() =>
    requestAnimationFrame(() => document.querySelector('[data-testid="accept-cancel"]')?.focus()),
  )
})

function closeAcceptConfirm() {
  if (acting.value) return
  confirmAccept.value = false
  actionError.value = ''
}

// P4-R1 / P4-KTD6. The outcomes the *server* says are legal for this record
// and this approver. The screen renders exactly this list: a button that is
// absent is not drawn, not drawn-and-disabled, because a disabled Reject on
// a timesheet would still teach a manager that timesheets can be rejected.
const actions = computed(() => selected.value?.actions || [])

function may(action) {
  return actions.value.includes(action)
}

/**
 * Which outcome buttons are drawn. While a reason or the HR note is open,
 * only the button that confirms it stays (plus the surface's Cancel): four
 * buttons under a half-written reason read as four live choices, and a tap
 * on another one would silently discard the draft.
 */
function shown(action) {
  const armed = reasonFor.value || (noteOpen.value ? 'Send to HR' : '')
  return may(action) && (!armed || armed === action)
}

function closeNote() {
  note.value = ''
  noteOpen.value = false
}

function clearDecisionSurfaces() {
  confirmAccept.value = false
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

function closeReason() {
  reason.value = ''
  reasonError.value = ''
  reasonFor.value = ''
}

function openNote() {
  actionError.value = ''
  reason.value = ''
  reasonError.value = ''
  reasonFor.value = ''
  noteOpen.value = true
}

// What the reason field is called, per outcome: the one kind map's copy (KTD6).
const reasonCopy = computed(() => getReasonCopy(reasonFor.value))

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
async function decide(action, { confirmed = false } = {}) {
  const item = selected.value
  // Double-tap protection is here rather than only on `:disabled`: a second
  // pointerdown can land before Vue has flushed the disabled attribute
  // (P2-U7 scenario 3).
  if (!item || acting.value) return
  // Belt and braces over the render: `actions` is what draws the row, but a
  // stale detail behind a slow reload must not be able to fire an outcome the
  // server would refuse anyway.
  if (!may(action)) return

  if (action === 'Accept' && !confirmed) {
    actionError.value = ''
    confirmAccept.value = true
    return
  }

  let comment
  if (action === 'Send Back' || action === 'Reject' || action === 'Need info' || action === 'Decline') {
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
    // KTD8: the badge moves now; the reload below then answers with the
    // queue's own total.
    decrementApprovals(1)
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
  // Plan 2026-10-04-003 U3/U7 (R13): the change request is its own lane --
  // the employee's words are the evidence, the approved week's hours the
  // amount, and the two decisions are Accept and Decline.
  change: {
    summary: (row) => `Change request · ${formatDateRange(row.from_date, row.to_date)}`,
    amount: (row) => (row.total_hours != null ? `${Number(row.total_hours).toFixed(1).replace(/\.0$/, '')} h` : ''),
    approve: () => 'Accept',
    quote: (item) => item.comment,
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
// U4 / R10: why a manager-stage leave is in HR's queue at all.
const HR_REASON_LABEL = {
  approver_away: 'Approver on leave',
  overdue: 'Overdue with approver',
}

function hrLine(row) {
  if (!row?.for_hr) return ''
  const parts = []
  if (HR_REASON_LABEL[row.hr_reason]) parts.push(HR_REASON_LABEL[row.hr_reason])
  if (row.sent_to_hr_by) parts.push(`Sent by ${row.sent_to_hr_by}`)
  if (row.hr_note) parts.push(`“${row.hr_note}”`)
  return parts.join(' · ')
}

// --- the queue's three lanes, and the bulk bar (plan 2026-10-04-003 U7) ---
//
// R19: the queue is sections, not a flat page -- the change requests that are
// never batchable, the rows the server flagged "Needs a look", and everything
// else. R14: rows group under one header per person, oldest waiting first --
// `pending` is already oldest-first, so the first time a person appears is
// their oldest item and the groups inherit the order.

const FLAG_LABELS = {
  hours_off: 'Hours differ from expected',
  missing_day: 'A working day has no hours',
  resubmitted: 'Sent back before',
  amended: 'Amended copy',
  overdue: 'Overdue',
  negative_balance: 'Balance would go negative',
  overlap: 'Overlaps other leave',
  with_hr: 'With HR',
  short_notice: 'Starts within 2 days',
}

function flagLabels(row) {
  return Object.keys(row.flags || {}).map((flag) => FLAG_LABELS[flag] || flag)
}

// Plan 2026-10-16-001 R16: Timesheets holds timesheets and changes;
// Leave & attendance holds leave and attendance.
const timesheetRows = computed(() =>
  pending.value.filter((row) => row.kind === 'timesheet' || row.kind === 'change'),
)
const leaveAttendanceRows = computed(() =>
  pending.value.filter((row) => row.kind === 'leave' || row.kind === 'attendance'),
)
const tabRows = computed(() =>
  view.value === 'timesheets' ? timesheetRows.value : leaveAttendanceRows.value,
)
const lanes = computed(() => {
  if (view.value !== 'timesheets') return laneSections(leaveAttendanceRows.value, decided.value.length)
  return [
    { key: 'yours', title: 'Waiting on you', rows: timesheetRows.value.filter((row) => row.lane !== 'hr') },
    { key: 'hr-stage', title: 'Waiting on HR', rows: timesheetRows.value.filter((row) => row.lane === 'hr') },
  ]
    .filter((lane) => lane.rows.length)
    .map((lane) => ({ ...lane, sections: laneSubsections(lane.rows) }))
})
// A row opened from the oversight list is not in this caller's own queue;
// its detail then takes the column the queue would have used on a phone.
const decidedOpen = ref(false)
const openInQueue = computed(() => !!props.name && pending.value.some((row) => row.name === props.name))

// R20: selection exists from 640px up. A phone queue is one column of
// single decisions; the checkboxes would only crowd it.
const canBulkSelect = ref(window.matchMedia('(min-width: 640px)').matches)
let bulkMediaQuery = null
function bulkMediaChanged(event) {
  canBulkSelect.value = event.matches
  if (!event.matches) bulkSelected.value = []
}
onMounted(() => {
  bulkMediaQuery = window.matchMedia('(min-width: 640px)')
  bulkMediaQuery.addEventListener('change', bulkMediaChanged)
  canBulkSelect.value = bulkMediaQuery.matches
})
onUnmounted(() => bulkMediaQuery?.removeEventListener('change', bulkMediaChanged))

/** The rows selectable together: timesheets and leaves the server itself
 * still calls clean (R17). A flagged row has no checkbox, and neither does
 * a change request -- Accept and Decline are never offered for bulk. */
function selectable(row) {
  return canBulkSelect.value && (row.kind === 'timesheet' || row.kind === 'leave') && !row.needs_look
}

const bulkSelected = ref([])

function isSelected(row) {
  return bulkSelected.value.some((item) => item.name === row.name)
}

function toggleBulk(row) {
  if (!selectable(row)) return
  bulkSelected.value = isSelected(row)
    ? bulkSelected.value.filter((item) => item.name !== row.name)
    : [
        ...bulkSelected.value,
        {
          doctype: row.doctype,
          name: row.name,
          expected_modified: row.token_modified,
          expected_state: row.status,
        },
      ]
}

const bulkHours = computed(() => {
  let hours = 0
  for (const item of bulkSelected.value) {
    const row = pending.value.find((candidate) => candidate.name === item.name)
    if (row?.kind === 'timesheet') hours += Number(row.total_hours || 0)
  }
  return hours
})
const bulkDays = computed(() => bulkSelected.value.filter((item) => item.doctype === 'Leave Application').length)

/** "Approve 8 items · 312 h · 3 days leave" -- the sentence the confirm
 * repeats, so the bar and the confirm can never disagree (R17). */
const bulkBarLabel = computed(() => {
  const parts = [`${bulkSelected.value.length} ${bulkSelected.value.length === 1 ? 'item' : 'items'}`]
  if (bulkHours.value) parts.push(`${Number(bulkHours.value).toFixed(1).replace(/\.0$/, '')} h`)
  if (bulkDays.value) parts.push(`${bulkDays.value} ${bulkDays.value === 1 ? 'day' : 'days'} leave`)
  return `Approve ${parts.join(' · ')}`
})

const confirmBulk = ref(false)
const bulkRunning = ref(false)
const bulkResult = ref(null)
const bulkApprove = createResource({
  url: 'helixhr.api.approve_clean_items',
  method: 'POST',
})

/** The people the confirm names (R17): the confirm states the count, people
 * and total before deciding. */
const bulkPeople = computed(() => {
  const names = []
  for (const item of bulkSelected.value) {
    const row = pending.value.find((candidate) => candidate.name === item.name)
    if (row && !names.includes(row.employee_name)) names.push(row.employee_name)
  }
  return names
})

async function runBulk() {
  if (bulkRunning.value || !bulkSelected.value.length) return
  bulkRunning.value = true
  try {
    const results = await bulkApprove.submit({ items: JSON.stringify(bulkSelected.value) })
    bulkResult.value = Array.isArray(results) ? results : results?.message || results
    decrementApprovals((Array.isArray(bulkResult.value) ? bulkResult.value : []).filter((row) => row?.ok).length)
    bulkSelected.value = []
    confirmBulk.value = false
    queue.reload()
  } catch (error) {
    actionError.value = toPlainLeaveError(error) || 'Could not run the bulk approval. Try again.'
    confirmBulk.value = false
  } finally {
    bulkRunning.value = false
  }
}

function dismissBulkResult() {
  bulkResult.value = null
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

    <!-- Plan 2026-10-15-002 U5. One visible tab hides the bar (R14); TabBar
         renders nothing for a single tab. -->
    <TabBar
      class="mb-4"
      :tabs="VIEW_TABS"
      :model-value="view"
      label="Approvals views"
      @update:model-value="showView"
    />

    <!-- R8: every pending non-timesheet item in HR's company, by approver.
         Overdue only reproduces the old Overdue tab; `?view=overdue` lands
         here with it on. -->
    <section
      v-if="view === 'all-pending'"
      data-testid="overdue-tab"
    >
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <SegmentedChips
          label="Filter pending items by age"
          :options="OVERDUE_OPTIONS"
          :model-value="overdueOnly"
          @update:model-value="setOverdue"
        />
        <p
          v-if="allPending.data?.timesheet_count"
          class="text-sm text-ink-gray-5"
        >
          Timesheets are on their own tab:
          <button
            type="button"
            class="min-h-11 cursor-pointer font-medium text-ink-gray-9 underline"
            @click="showView('timesheets')"
          >
            <span class="tabular">{{ allPending.data.timesheet_count }}</span> pending
          </button>
        </p>
      </div>
      <AsyncState
        section="approvals-all-pending"
        :resource="allPending"
        :empty="!allPending.data?.count"
        :empty-title="overdueOnly ? 'Nothing overdue' : `Nothing pending in ${session.employee?.company || 'your company'}`"
        empty-body="Leave, attendance, change requests and requests waiting on someone appear here, grouped by who owes the decision."
        :skeleton-rows="3"
      >
        <p
          v-if="allPending.data.total_is_capped"
          class="mb-3 text-sm text-ink-amber-3"
        >
          Showing the oldest 500. Narrow by approver.
        </p>
        <div class="space-y-6">
          <section
            v-for="group in allPending.data.groups"
            :key="group.key"
            data-testid="overdue-group"
          >
            <h2 class="label mb-2">
              {{ group.approver_name }}
              <span
                v-if="group.is_you"
                class="text-ink-gray-5"
              >(You)</span>
              <span class="tabular text-ink-gray-5">{{ group.count }}</span>
              <span
                v-if="group.reason && group.reason !== 'none'"
                class="ml-1 text-ink-amber-3"
              >{{ APPROVER_REASON_TEXT[group.reason] }}</span>
            </h2>
            <ul
              class="space-y-2"
              :class="group.items.length > OVERDUE_VISIBLE_ROWS ? 'scroll-queue max-h-[19.5rem]' : ''"
              :tabindex="group.items.length > OVERDUE_VISIBLE_ROWS ? 0 : undefined"
              :aria-label="group.items.length > OVERDUE_VISIBLE_ROWS ? `${group.approver_name}, scrollable` : undefined"
            >
              <li
                v-for="item in group.items"
                :key="item.route_kind + item.name"
                data-testid="overdue-row"
                :data-overdue-name="item.name"
              >
                <router-link
                  :to="`/approvals/${item.route_kind}/${item.name}`"
                  class="surface-card elev-1 flex min-h-14 min-w-0 items-center gap-3 px-3 hover:bg-surface-gray-2"
                >
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-sm font-medium text-ink-gray-9">{{ item.employee_name }} · {{ item.title }}</span>
                    <span class="block truncate text-xs text-ink-gray-5">
                      {{ item.kind }}
                      <template v-if="item.overdue"> · Overdue</template>
                    </span>
                  </span>
                  <span class="tabular shrink-0 text-right text-sm text-ink-gray-9">
                    {{ item.since_unknown ? 'Since unknown' : `${item.age_days} days` }}
                    <span class="block text-xs text-ink-gray-5">limit {{ item.threshold_days }}</span>
                  </span>
                </router-link>
              </li>
            </ul>
          </section>
        </div>
      </AsyncState>
    </section>

    <p
      v-if="view === 'queue'"
      class="mb-4 text-sm text-ink-gray-5"
    >
      <template v-if="hasHrWork">
        Oldest first. Your own team, plus anything marked HR.
      </template>
      <template v-else>
        Oldest first. You only see people who report to you.
      </template>
    </p>

    <!-- Kind chips: Leave / Attendance in Leave & attendance tab -->
    <SegmentedChips
      v-if="view === 'queue' && (kindChips.length > 1 || kindFilter)"
      class="mb-4"
      label="Filter approvals by kind"
      data-testid="approval-kind-chips"
      :options="kindOptions"
      :model-value="kindFilter"
      @update:model-value="filterQueue"
    />

    <div
      v-if="view === 'queue' || view === 'timesheets'"
      class="lg:flex lg:items-start lg:gap-6"
    >
      <!-- The queue. Plan 2026-10-15-002 U5/U6: the Approvals tab in lanes
           (My team, Decided by me, HR, IT); the Timesheets tab as Waiting on
           you and Waiting on HR, with HR's oversight list below. -->
      <div
        v-show="!name || isDesktop || openInQueue"
        class="min-w-0 lg:flex-1"
        data-testid="approvals-queue"
      >
        <AsyncState
          section="approvals-queue"
          :resource="queue"
          :empty="!tabRows.length && !(view === 'queue' && decided.length)"
          :empty-title="view === 'timesheets' ? 'No timesheets waiting on you' : 'Nothing waiting on you'"
          :empty-body="
            view === 'timesheets'
              ? 'Weeks your team sends for approval appear here.'
              : 'Leave, attendance, change requests and requests waiting on you appear here.'
          "
          :skeleton-rows="3"
        >
          <!-- R2: with every pending lane empty, the empty copy sits above
               the Decided disclosure. -->
          <p
            v-if="!tabRows.length"
            class="surface-inset mb-6 p-4 text-sm text-ink-gray-7"
          >
            Nothing waiting on you.
          </p>
          <section
            v-for="lane in lanes"
            :key="lane.key"
            class="mb-8"
            :data-testid="`lane-${lane.key}`"
          >
            <!-- KTD8: the last 14 days of this caller's own decisions,
                 collapsed. Directly after My team, or first without it. -->
            <template v-if="lane.key === 'decided'">
              <button
                type="button"
                class="flex min-h-11 w-full cursor-pointer items-center gap-2 text-left"
                :aria-expanded="decidedOpen"
                aria-controls="approvals-decided-list"
                data-testid="decided-toggle"
                @click="decidedOpen = !decidedOpen"
              >
                <Icon
                  name="chevronRight"
                  class="shrink-0 text-ink-gray-5 transition-transform duration-200"
                  :class="decidedOpen ? 'rotate-90' : ''"
                />
                <span class="label">{{ lane.title }}</span>
                <span class="tabular text-sm text-ink-gray-5">{{ decided.length }}</span>
              </button>
              <ul
                v-show="decidedOpen"
                id="approvals-decided-list"
                class="mt-2 space-y-2"
              >
                <li
                  v-for="row in decided"
                  :key="row.id"
                  class="surface-card elev-1 flex items-center gap-3 p-3"
                >
                  <Avatar
                    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs font-bold text-ink-gray-7"
                    :photo-url="row.photo_url"
                    :initials="row.initials"
                    :size="32"
                  />
                  <p class="min-w-0 flex-1 truncate text-sm text-ink-gray-7">
                    {{ row.employee_name }} · {{ row.label }}
                    <template v-if="row.from_date">
                      · {{ formatDateRange(row.from_date, row.to_date) }}
                    </template>
                  </p>
                  <StatusBadge
                    :kind="row.kind"
                    :status="row.status"
                    :docstatus="row.docstatus"
                  />
                </li>
              </ul>
            </template>
            <template v-else>
              <h2 class="mb-3 text-base font-semibold text-ink-gray-9">
                {{ lane.title }}
                <span class="tabular text-sm font-normal text-ink-gray-5">{{ lane.rows.length }}</span>
              </h2>
              <!-- Plan 2026-10-04-003 U7 (R13, R19): inside a lane, oldest waiting
               first -- change requests (never batchable), the rows the
               server flagged, and everything else. -->
              <section
                v-for="section in lane.sections"
                :key="section.key"
                class="mb-6"
                :data-testid="`queue-section-${section.key}`"
              >
                <h3 class="label mb-2">
                  {{ section.title }}
                  <span class="tabular text-ink-gray-5">{{ section.rows.length }}</span>
                </h3>
                <p
                  v-if="section.note"
                  class="mb-2 text-sm text-ink-gray-5"
                >
                  {{ section.note }}
                </p>

                <!-- R14: one header per person, their oldest item leading. -->
                <div
                  v-for="group in groupByPerson(section.rows)"
                  :key="group.employee"
                  class="mb-4"
                  :data-testid="`person-group-${section.key}`"
                  :data-person="group.employee_name"
                >
                  <div class="mb-1.5 flex items-center gap-2 px-1">
                    <Avatar
                      class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-[10px] font-bold text-ink-gray-7"
                      :photo-url="group.photo_url"
                      :initials="group.initials"
                      :size="24"
                    />
                    <span class="min-w-0 truncate text-sm font-medium text-ink-gray-9">
                      {{ group.employee_name }}
                    </span>
                    <span class="tabular text-xs text-ink-gray-5">
                      {{ group.rows.length }} {{ group.rows.length === 1 ? 'item' : 'items' }} · waiting
                      {{ ageLabel(group.oldest) }}
                    </span>
                  </div>

                  <ul class="space-y-2">
                    <li
                      v-for="row in group.rows"
                      :key="row.id"
                      class="surface-card elev-1"
                      :class="row.name === name ? 'ring-2 ring-field' : ''"
                      data-testid="approval-row"
                      :data-approval-kind="row.kind"
                      :data-approval-name="row.name"
                    >
                      <div class="flex items-stretch">
                        <!-- R17: selection exists only in Looks normal, only for
                         the two kinds the batch takes, and only while the
                         server still calls the row clean. -->
                        <button
                          v-if="section.key === 'normal' && canBulkSelect"
                          type="button"
                          class="flex w-11 shrink-0 cursor-pointer items-center justify-center border-r border-outline-gray-2"
                          :class="selectable(row) ? '' : 'cursor-default opacity-0'"
                          :aria-label="isSelected(row) ? `Unselect ${rowSummary(row)}` : `Select ${rowSummary(row)}`"
                          :aria-pressed="isSelected(row)"
                          :data-testid="`bulk-check-${row.kind}`"
                          :tabindex="selectable(row) ? 0 : -1"
                          @click.stop="toggleBulk(row)"
                        >
                          <span
                            class="flex h-5 w-5 items-center justify-center rounded border"
                            :class="isSelected(row) ? 'border-heading bg-heading text-white' : 'border-outline-gray-3'"
                            aria-hidden="true"
                          >
                            <Icon
                              v-if="isSelected(row)"
                              name="check"
                              class="h-3 w-3"
                            />
                          </span>
                        </button>

                        <button
                          type="button"
                          class="flex w-full min-w-0 cursor-pointer items-center gap-3 p-3 text-left"
                          :aria-expanded="row.name === name"
                          @click="row.name === name ? closeDetail() : open(row)"
                        >
                          <Avatar
                            class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                            :photo-url="row.photo_url"
                            :initials="row.initials"
                            :size="40"
                          />

                          <span class="min-w-0 flex-1">
                            <span class="flex min-w-0 items-center gap-2">
                              <KindTag :row="row" />
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
                              v-if="row.kind === 'timesheet' && row.expected_hours != null"
                              class="tabular block text-xs text-ink-gray-5"
                            >
                              {{ Number(row.total_hours).toFixed(1).replace(/\.0$/, '') }} of
                              {{ Number(row.expected_hours).toFixed(1).replace(/\.0$/, '') }} h expected
                            </span>
                            <span
                              v-if="row.kind === 'leave' && row.balance_after != null"
                              class="block text-xs text-ink-gray-5"
                            >
                              Balance after:
                              <span class="tabular">{{ Number(row.balance_after).toFixed(1).replace(/\.0$/, '') }}</span>
                              <template v-if="row.overlap_count">
                                · {{ row.overlap_count }}
                                {{ row.overlap_count === 1 ? 'other person is' : 'other people are' }} off then
                              </template>
                            </span>
                            <!-- R16: the flags in words, never colour alone. -->
                            <span
                              v-if="row.needs_look"
                              class="mt-1 flex flex-wrap gap-1"
                              data-testid="flag-chips"
                            >
                              <span
                                v-for="flag in flagLabels(row)"
                                :key="flag"
                                class="rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
                              >
                                {{ flag }}
                              </span>
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
                      </div>

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
                            <!-- R13: the change request's own words are the
                             evidence; the approved week's hours sit beside
                             them. -->
                            <div
                              v-if="selected.kind === 'change'"
                              class="surface-inset p-3 text-sm"
                            >
                              <p class="font-medium text-ink-gray-9">
                                What they asked to change
                              </p>
                              <p class="mt-1 text-ink-gray-7">
                                {{ selected.comment }}
                              </p>
                              <p class="tabular mt-2 text-xs text-ink-gray-5">
                                The approved week carries
                                {{ Number(selected.total_hours).toFixed(1).replace(/\.0$/, '') }} h ·
                                {{ formatDateRange(selected.week_start, selected.week_end) }}
                              </p>
                            </div>

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
                              <!-- Plan 2026-10-15-003 R6: Need info from Open is a pick-up. -->
                              <p
                                v-if="reasonFor === 'Need info' && selected.kind === 'request' && selected.status === 'Open'"
                                class="mt-1 text-sm text-white"
                                data-testid="need-info-assigns"
                              >
                                This also assigns the request to you.
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
                                class="mt-1 text-sm font-medium text-ink-red-4"
                                role="alert"
                              >
                                {{ reasonError }}
                              </p>
                              <!-- Plan 2026-10-05-001 U3: closing the reason puts the other
                               outcomes (Accept among them) back. -->
                              <Button
                                class="mt-2"
                                variant="subtle"
                                data-testid="reason-cancel"
                                @click="closeReason"
                              >
                                Cancel
                              </Button>
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
                              <Button
                                class="mt-2"
                                variant="subtle"
                                data-testid="note-cancel"
                                @click="closeNote"
                              >
                                Cancel
                              </Button>
                            </div>

                            <!-- Plan 2026-10-15-002 R7: HR User reads, never decides. -->
                            <p
                              v-if="selected.read_only"
                              class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
                              data-testid="view-only-note"
                            >
                              View only: you can't approve timesheets.
                            </p>
                            <DecisionActions
                              class="mt-3"
                              :kind="selected.kind"
                              :actions="actions"
                              :disabled="acting === selected.name"
                              :loading="acting === selected.name"
                              :armed="reasonFor || (noteOpen ? 'Send to HR' : (confirmAccept ? 'Accept' : ''))"
                              :approve-label="approveLabel"
                              @act="decide"
                            />
                          </div>
                        </AsyncState>
                      </div>
                    </li>
                  </ul>
                </div>
              </section>
            </template>
          </section>

          <p
            v-if="overflow"
            class="mt-3 text-sm text-ink-gray-5"
          >
            Showing the <span class="tabular">{{ pending.length }}</span> oldest.
            <span class="tabular">{{ overflow }}</span> more are waiting.
          </p>
        </AsyncState>

        <!-- R6/R9: HR's view of every pending timesheet in their company,
             by approver, with Send reminder. -->
        <TimesheetOversight
          v-if="view === 'timesheets' && session.canOverseeTimesheets"
          class="mt-8"
        />
      </div>

      <!-- Desktop: the full evidence beside the queue. Approve is not on
           screen at all until it has loaded, which is the whole of P2-AE6 --
           the decision is only available once the thing being decided is
           visible. -->
      <aside
        v-if="name && (isDesktop || !openInQueue)"
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
              <Avatar
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                :photo-url="selected.photo_url"
                :initials="selected.initials"
                :size="40"
              />
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
                  <template v-else-if="selected.kind === 'change'">
                    Change request ·
                    {{ formatDateRange(selected.week_start, selected.week_end) }}
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

            <!-- R13: the change request's evidence is the employee's words
                 plus the approved week's hours it proposes to undo. -->
            <div
              v-else-if="selected.kind === 'change'"
              class="mt-4"
            >
              <div class="surface-inset p-3 text-sm">
                <p class="font-medium text-ink-gray-9">
                  What they asked to change
                </p>
                <p class="mt-1 text-ink-gray-7">
                  {{ selected.comment }}
                </p>
              </div>
              <dl class="mt-3 grid grid-cols-2 gap-4">
                <div>
                  <dt class="label">
                    Approved week
                  </dt>
                  <dd class="mt-0.5 text-sm text-ink-gray-9">
                    {{ formatDateRange(selected.week_start, selected.week_end) }}
                  </dd>
                </div>
                <div>
                  <dt class="label">
                    Hours on it
                  </dt>
                  <dd class="tabular mt-0.5 text-sm text-ink-gray-9">
                    {{ Number(selected.total_hours).toFixed(1).replace(/\.0$/, '') }} h
                  </dd>
                </div>
              </dl>
              <p
                v-if="selected.decision_note"
                class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
              >
                <span class="font-medium text-ink-gray-9">Decision:</span>
                {{ selected.decision_note }}
              </p>
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
            >
              <p class="text-sm text-ink-gray-6">
                {{ selected.category }}
              </p>
              <CorrectionReview
                v-if="selected.correction"
                :key="selected.name"
                :request="selected.name"
                :correction="selected.correction"
                :attachments="selected.attachments"
                :can-reveal="actions.length > 0"
              />
              <RequestThread
                :entries="selected.thread || []"
                :can-comment="!!selected.can_comment"
                :closed="!!selected.closed"
                :request-name="selected.name"
                :employee-name="selected.employee_name || ''"
                mode="handler"
                class="mt-3"
                @posted="detail.fetch()"
              />

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
                  class="mt-1 text-sm text-ink-red-4"
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
              <!-- Plan 2026-10-15-003 R6: Need info from Open is a pick-up. -->
              <p
                v-if="reasonFor === 'Need info' && selected.kind === 'request' && selected.status === 'Open'"
                class="mt-1 text-sm text-white"
                data-testid="need-info-assigns"
              >
                This also assigns the request to you.
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
                class="mt-1 text-sm font-medium text-ink-red-4"
                role="alert"
              >
                {{ reasonError }}
              </p>
              <!-- Plan 2026-10-05-001 U3: closing the reason puts the other
                   outcomes (Accept among them) back. -->
              <Button
                class="mt-2"
                variant="subtle"
                data-testid="reason-cancel"
                @click="closeReason"
              >
                Cancel
              </Button>
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
              <Button
                class="mt-2"
                variant="subtle"
                data-testid="note-cancel"
                @click="closeNote"
              >
                Cancel
              </Button>
            </div>

            <!-- P4-R1. Exactly the outcomes the server allows, in one order
                 on every kind: the decision, the recoverable no, the final no,
                 the hand-over. An outcome missing from `actions` is not
                 rendered rather than rendered disabled -- a greyed-out Reject
                 on a timesheet would still teach a manager that a week can be
                 rejected, which it cannot (P4-KTD2). -->
            <!-- Plan 2026-10-15-002 R7: HR User reads, never decides. -->
            <p
              v-if="selected.read_only"
              class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
              data-testid="view-only-note"
            >
              View only: you can't approve timesheets.
            </p>
            <div class="mt-4 flex flex-wrap items-center justify-end gap-2">
              <DecisionActions
                :kind="selected.kind"
                :actions="actions"
                :disabled="acting === selected.name"
                :loading="acting === selected.name"
                :armed="reasonFor || (noteOpen ? 'Send to HR' : (confirmAccept ? 'Accept' : ''))"
                :approve-label="approveLabel"
                @act="decide"
              />
            </div>
          </article>
        </AsyncState>
      </aside>
    </div>

    <!-- R17: the sticky bar names what is about to happen, in the same words
         the confirm uses. It appears only while something is selected. -->
    <div
      v-if="bulkSelected.length && canBulkSelect"
      class="action-bar flex items-center gap-3"
      data-testid="bulk-bar"
    >
      <p class="min-w-0 flex-1 truncate text-sm font-medium text-ink-gray-9">
        {{ bulkBarLabel }}
      </p>
      <Button
        variant="subtle"
        @click="bulkSelected = []"
      >
        Clear
      </Button>
      <Button
        variant="solid"
        theme="green"
        data-testid="bulk-approve"
        @click="confirmBulk = true"
      >
        Approve together
      </Button>
    </div>

    <!-- R17: the confirm states the count, the people and the total before
         deciding -- the one guard the plan keeps between a manager and a
         rubber stamp. -->
    <Dialog
      v-model="confirmBulk"
      :options="{ title: 'Approve these together?' }"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          {{ bulkBarLabel }} for
          {{ bulkPeople.join(', ') }}.
        </p>
        <p class="mt-2 text-sm text-ink-gray-6">
          Only rows that still look normal to the server will be approved; anything that changed
          stays in the queue.
        </p>
      </template>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button
            variant="subtle"
            @click="confirmBulk = false"
          >
            Back
          </Button>
          <Button
            variant="solid"
            theme="green"
            :loading="bulkRunning"
            data-testid="bulk-confirm"
            @click="runBulk"
          >
            Approve {{ bulkSelected.length }}
          </Button>
        </div>
      </template>
    </Dialog>

    <!-- Plan 2026-10-05-001 U3: the change request's Accept, confirmed.
         Errors stay inside the dialog so the manager reads them where they
         clicked; the confirm stays disabled while the decision is in flight. -->
    <Dialog
      :model-value="confirmAccept"
      :options="{ title: 'Accept this change request?' }"
      @update:model-value="(value) => !value && closeAcceptConfirm()"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          This cancels the approved week and reopens it for
          {{ selected?.employee_name || 'the employee' }} to edit.
        </p>
        <p
          v-if="actionError"
          class="surface-alert mt-3 p-3 text-sm"
          role="alert"
          data-testid="accept-confirm-error"
        >
          {{ actionError }}
        </p>
      </template>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button
            variant="subtle"
            :disabled="!!acting"
            data-testid="accept-cancel"
            @click="closeAcceptConfirm"
          >
            Cancel
          </Button>
          <Button
            variant="solid"
            theme="green"
            :loading="!!acting"
            :disabled="!!acting"
            data-testid="accept-confirm"
            @click="decide('Accept', { confirmed: true })"
          >
            Accept and reopen
          </Button>
        </div>
      </template>
    </Dialog>

    <!-- R18: the result names every refused item, with a way back to it. -->
    <Dialog
      :model-value="!!bulkResult"
      :options="{ title: 'Bulk approval' }"
      @update:model-value="(value) => !value && dismissBulkResult()"
    >
      <template #body-content>
        <ul class="space-y-2">
          <li
            v-for="result in bulkResult || []"
            :key="result.name"
            class="surface-inset flex items-center justify-between gap-3 p-2 text-sm"
            :data-testid="`bulk-result-${result.ok ? 'ok' : 'refused'}`"
          >
            <span class="min-w-0 flex-1 truncate">
              <template v-if="result.ok">
                Approved
              </template>
              <template v-else>
                {{ result.message }}
              </template>
            </span>
            <button
              v-if="!result.ok"
              class="shrink-0 cursor-pointer text-sm font-medium text-ink-blue-link underline underline-offset-2"
              type="button"
              @click="dismissBulkResult(); open({ name: result.name, kind: (pending.find((row) => row.name === result.name) || {}).kind || 'timesheet' })"
            >
              Open it
            </button>
          </li>
        </ul>
      </template>
      <template #actions>
        <Button
          variant="solid"
          @click="dismissBulkResult"
        >
          Done
        </Button>
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
/* Same scroll affordance as Home's queues (NeedsYou.vue): five 3.5rem rows
   plus gaps, an always-visible gutter and a bottom rule. */
.scroll-queue {
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding-right: 0.25rem;
  border-bottom: 1px solid var(--outline-gray-2);
}
</style>
