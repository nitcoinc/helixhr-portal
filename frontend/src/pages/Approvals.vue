<script setup>
import Avatar from '@/components/Avatar.vue'
import { ref, computed, watch, nextTick, onMounted, onUnmounted, useId } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import TabBar from '@/components/TabBar.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import AsyncState from '@/components/AsyncState.vue'
import SearchField from '@/components/SearchField.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import ApprovalEvidence from '@/components/approvals/ApprovalEvidence.vue'
import KindTag from '@/components/KindTag.vue'
import { KIND_TAGS, getReasonCopy } from '@/lib/workKinds'
import TimesheetOversight from '@/components/approvals/TimesheetOversight.vue'
import {
  APPROVER_REASON_TEXT,
  BULK_MAX_ITEMS,
  approveAllBatch,
  approveAllLabel,
  batchItem,
  batchLabel,
  decidedForTab,
  groupByPerson,
  hrLine,
  isBatchable,
  isWithManager,
  laneSections,
  laneSubsections,
  readShortcuts,
  selectionState,
  showsHrChip,
  writeShortcuts,
} from '@/lib/approvalLanes'
import { session } from '@/lib/session'
import { decrementApprovals, setApprovalsCount } from '@/lib/approvalsCount'
import { formatDate, formatDateRange } from '@/lib/dates'
import { focusNear } from '@/lib/focus'
import { trimNumber } from '@/lib/hours'
import { toPlainLeaveError } from '@/lib/errorMap'
import { notify } from '@/lib/notify'
import { useIsDesktop } from '@/lib/useIsDesktop'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'
import { returnedNotice } from '@/lib/rosterRequests'

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
// Approvals search: what the field holds, and the term the queue was last
// asked for -- trimmed, and empty under the server's 2-character minimum
// (`_request_search_pattern`), so a shorter one asks the same as none.
const query = ref('')
const search = ref('')
const searchHintId = useId()
const queue = createResource({
  url: 'helixhr.api.get_my_approvals',
  makeParams: () => ({
    kind: kindFilter.value || undefined,
    q: search.value || undefined,
  }),
})
watch(kindFilter, () => reloadQueue())

const debounced = useDebouncedSearch((value) => {
  const next = value.trim().length >= 2 ? value.trim() : ''
  if (next === search.value) return
  search.value = next
  return reloadQueue()
})
watch(query, debounced.search)
// What is on screen, from the answer itself: a slow reply never relabels it.
const searching = computed(() => Boolean(queue.data?.searched))

// Plan 2026-10-17-001 KTD8: every load is the shell's badge, which is never
// polled. `total` counts the chip's kind only, so a filtered view sums the
// per-kind counts, which always cover the whole queue. Its
// `total_is_capped` describes the filtered page, not the queue, so a
// filtered load keeps the badge's current floor flag.
// A searched load is not the queue either, and its counts are not read here:
// only an unsearched load may move the badge.
watch(
  () => queue.data,
  (data) => {
    if (!data || data.searched) return
    if (kindFilter.value) {
      setApprovalsCount((data.counts?.kinds || []).reduce((sum, kind) => sum + (kind.count || 0), 0))
    } else {
      setApprovalsCount(data.total, data.total_is_capped)
    }
  },
)

// Plan 2026-10-16-001 R16: in Leave & attendance tab, chips filter leave / attendance.
// Plan 2026-10-31-001 U10 (R14): roster requests are HR's, in this tab too.
const QUEUE_KINDS = ['leave', 'attendance', 'roster']
const kindChips = computed(() =>
  (queue.data?.counts?.kinds || []).filter(
    (chip) => QUEUE_KINDS.includes(chip.name) && (chip.count || chip.name === kindFilter.value),
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
  return QUEUE_KINDS.reduce((sum, name) => sum + (kinds.find((k) => k.name === name)?.count || 0), 0)
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
// Bulk selection belongs to one tab's rows and clears when the tab changes,
// as do the reasons a batch left on its refused rows.
watch(view, () => {
  bulkSelected.value = []
  refusedReasons.value = {}
})
// A new search shows other rows; a selection of rows it hid must not ride
// along into a batch, and an open confirm (which re-reads its lane every
// render) must not grow to the unsearched lane when the reload lands.
watch(search, () => {
  bulkSelected.value = []
  if (!bulkRunning.value) confirmSource.value = null
})

const pending = computed(() => queue.data?.pending || [])
const decided = computed(() => queue.data?.decided || [])
const decidedRows = computed(() => decidedForTab(decided.value, view.value, searching.value))
// Plan 2026-10-31-001 KTD15: what is waiting on this caller. The "With
// managers" rows ride in `pending` but are counted nowhere -- not in `total`,
// the header pill or the overflow line.
const owedPending = computed(() => pending.value.filter((row) => !isWithManager(row)))
const overflow = computed(() => Math.max(0, (queue.data?.total || 0) - owedPending.value.length))

// P4-R11. The queue's own sentence used to promise "you only see people who
// report to you", which is still true for a line manager and false for an HR
// Manager, whose queue is their reports *plus* everything handed to HR. The
// rows themselves are the honest signal, so the sentence follows them rather
// than asking the server a second question about roles.
// Plan 2026-10-31-001 R23: the "With managers" rows are not HR's work, so
// they never turn this sentence on.
const hasHrWork = computed(() => owedPending.value.some((row) => row.for_hr))

// MH-09: an HR User approves nothing, so an empty "waiting on you" card would
// only push the work they do have -- oversight -- below the fold.
const oversightOnly = computed(() => !session.canApprove && session.canOverseeTimesheets)

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
// Plan 2026-10-31-001 U10: a roster request's Approve writes several people's
// shifts at once, so it takes the same confirm.
const confirmAccept = ref(false)
const confirmAction = computed(() => (selected.value?.kind === 'roster' ? 'Approve' : 'Accept'))
function needsConfirm(action) {
  return action === confirmAction.value
}
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

// What the reason field is called, per outcome: the one kind map's copy
// (KTD6). The page reads only its "missing" sentence; ApprovalEvidence draws
// the rest.
const reasonCopy = computed(() => getReasonCopy(reasonFor.value, selected.value?.kind))

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

  if (needsConfirm(action) && !confirmed) {
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
  // KTD16: read before the row goes, so the row after it takes focus.
  const plan = focusPlan([item.name])
  const fromAllPending = view.value === 'all-pending'
  const nextOversight = fromAllPending ? allPendingNeighbour(item.name) : null
  try {
    const outcome = await act.submit({
      doctype: item.doctype,
      name: item.name,
      action,
      comment,
      expected_modified: item.modified,
      expected_state: item.state,
    })
    clearDecisionSurfaces()
    // Plan 2026-10-31-001 R16: an Approve the re-check turned into a return
    // applied nothing. It leaves the queue like any decision, but is never
    // reported as a success.
    if (outcome?.returned) notify.error(returnedNotice(item.manager_name || item.employee_name, outcome.reasons))
    // KTD8: the badge moves now; the reload below then answers with the
    // queue's own total. An All pending item is often not in the caller's
    // own queue, so only a queued one moves the badge.
    if (!fromAllPending || openInQueue.value) decrementApprovals(1)
    closeDetail()
    if (fromAllPending) {
      Promise.resolve(allPending.reload())
        .catch(() => {})
        .then(() => focusAllPendingRow(nextOversight))
      reloadQueue()
    } else {
      reloadQueue().then(() => restoreFocus(plan))
    }
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
    reloadQueue()
    // The row may be gone or decided elsewhere; All pending re-reads too.
    if (fromAllPending) Promise.resolve(allPending.reload()).catch(() => {})
    if (props.name) detail.fetch()
  } finally {
    acting.value = ''
  }
}

// --- presentation --------------------------------------------------------

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
    amount: (row) => (row.total_hours != null ? `${trimNumber(row.total_hours)} h` : ''),
    approve: () => 'Accept',
    quote: (item) => item.comment,
  },
  // Plan 2026-10-31-001 U10: a manager's roster request -- how many changes
  // for how many people, the manager's note as the quote.
  roster: {
    summary: (row) =>
      `Roster change · ${plural(row.people_count, 'person', 'people')} · ${formatDateRange(row.from_date, row.to_date)}`,
    amount: (row) => (row.change_count != null ? plural(row.change_count, 'change', 'changes') : ''),
    approve: (item) => `Approve ${plural(item.change_count, 'change', 'changes')}`,
    quote: (item) => item.note,
  },
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`
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

/** Everything ApprovalEvidence draws, the same for the phone's inline block
 * and the desktop panel (U7, R21). */
const evidence = computed(() => ({
  selected: selected.value,
  actions: actions.value,
  busy: !!selected.value && acting.value === selected.value.name,
  armed: reasonFor.value || (noteOpen.value ? 'Send to HR' : confirmAccept.value ? confirmAction.value : ''),
  approveLabel: approveLabel.value,
  quote: quote.value || '',
  hrLine: hrLine(selected.value),
  actionError: actionError.value,
  reasonFor: reasonFor.value,
  reasonError: reasonError.value,
  noteOpen: noteOpen.value,
}))

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
const leaveAttendanceRows = computed(() => pending.value.filter((row) => QUEUE_KINDS.includes(row.kind)))
const tabRows = computed(() =>
  view.value === 'timesheets' ? timesheetRows.value : leaveAttendanceRows.value,
)
// Plan 2026-10-31-001 R23: what is actually waiting on this caller. With only
// "With managers" rows, the queue still says nothing is waiting on you.
const owedTabRows = computed(() => tabRows.value.filter((row) => !isWithManager(row)))
// Collapsed by default; a link straight to one of its rows opens it.
const withManagersOpen = ref(false)
watch(
  () => !!props.name && pending.value.some((row) => row.name === props.name && isWithManager(row)),
  (inside) => {
    if (inside) withManagersOpen.value = true
  },
  { immediate: true },
)
const lanes = computed(() => {
  if (view.value !== 'timesheets') return laneSections(leaveAttendanceRows.value, decidedRows.value.length)
  return [
    { key: 'yours', title: 'Waiting on you', rows: timesheetRows.value.filter((row) => row.lane !== 'hr') },
    { key: 'hr-stage', title: 'Waiting on HR', rows: timesheetRows.value.filter((row) => row.lane === 'hr') },
  ]
    .filter((lane) => lane.rows.length)
    .map((lane) => ({ ...lane, sections: laneSubsections(lane.rows) }))
    .concat(decidedRows.value.length ? [{ key: 'decided', title: 'Decided by me', rows: [], sections: [] }] : [])
})
// A row opened from the oversight list is not in this caller's own queue;
// its detail then takes the column the queue would have used on a phone.
const decidedOpen = ref(false)
// A search's answers are the point, so its Decided matches open themselves;
// clearing it folds the receipt back to its normal, collapsed view.
watch(searching, (on) => {
  decidedOpen.value = on
})
// A "With managers" row folded away is not on screen, so its detail takes the
// column too.
const openInQueue = computed(
  () =>
    !!props.name &&
    pending.value.some((row) => row.name === props.name && (!isWithManager(row) || withManagersOpen.value)),
)

// R20: selection exists from 640px up. A phone queue is one column of
// single decisions; the checkboxes would only crowd it. Approve all (MH-04)
// is the phone's batch, and it never reads the checkboxes.
const canBulkSelect = ref(window.matchMedia('(min-width: 640px)').matches)
let bulkMediaQuery = null
function bulkMediaChanged(event) {
  canBulkSelect.value = event.matches
  if (!event.matches) {
    bulkSelected.value = []
    // A confirm over a selection that just went away has nothing left to
    // say. An Approve-all confirm keeps its batch: it came from the lane.
    if (confirmSource.value?.type === 'selection') confirmSource.value = null
  }
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
  return canBulkSelect.value && isBatchable(row)
}

// Each entry is `batchItem(row)` as it was when it was ticked, so the token
// the manager saw is the one the server checks.
const bulkSelected = ref([])
const selectedNames = computed(() => bulkSelected.value.map((item) => item.name))

function isSelected(row) {
  return selectedNames.value.includes(row.name)
}

function toggleBulk(row) {
  if (!selectable(row)) return
  if (isSelected(row)) {
    bulkSelected.value = bulkSelected.value.filter((item) => item.name !== row.name)
  } else if (bulkSelected.value.length < BULK_MAX_ITEMS) {
    bulkSelected.value = [...bulkSelected.value, batchItem(row)]
  }
}

/** MH-06 / HR-12: the tri-state select-all on Looks normal and on each
 * person in it. Ticking adds what is missing, up to the server's cap;
 * unticking a full set clears it. */
const ARIA_CHECKED = { all: 'true', some: 'mixed', none: 'false' }

function selectAllState(rows) {
  return selectionState(rows, selectedNames.value)
}

function selectableCount(rows) {
  return rows.filter(isBatchable).length
}

function toggleAll(rows) {
  const targets = rows.filter(selectable)
  if (selectAllState(rows) === 'all') {
    const drop = targets.map((row) => row.name)
    bulkSelected.value = bulkSelected.value.filter((item) => !drop.includes(item.name))
    return
  }
  const next = [...bulkSelected.value]
  for (const row of targets) {
    if (next.length >= BULK_MAX_ITEMS) break
    if (!next.some((item) => item.name === row.name)) next.push(batchItem(row))
  }
  bulkSelected.value = next
}

/** Queue rows by record name, in the order asked, skipping any that left. */
function rowsByName(names) {
  return names.map((name) => pending.value.find((row) => row.name === name)).filter(Boolean)
}

const bulkBarLabel = computed(() => batchLabel(rowsByName(selectedNames.value)))

/** The Looks normal rows of one lane, as the queue holds them now. */
function laneNormalRows(laneKey) {
  const lane = lanes.value.find((candidate) => candidate.key === laneKey)
  return lane?.sections.find((section) => section.key === 'normal')?.rows || []
}

/** MH-04: the batch Approve all would send for one lane. */
function laneBatch(laneKey) {
  return approveAllBatch(laneNormalRows(laneKey))
}

// --- the one bulk confirm (R17, KTD6) ---------------------------------------
//
// Three ways in: the checkbox bar ('selection'), a lane's Approve all
// ('lane'), and the keyboard's `a` on one row ('row'). The confirm keeps only
// *where* its batch comes from and re-derives the rows from the queue every
// render, so a reload underneath an open confirm changes the sentence rather
// than leaving a stale count -- and a phone rotating to 700px keeps it.
const confirmSource = ref(null)
const bulkRunning = ref(false)
const confirmBulk = computed({
  get: () => !!confirmSource.value,
  set: (open) => {
    if (!open && !bulkRunning.value) confirmSource.value = null
  },
})

function openConfirm(source) {
  if (!bulkRunning.value) confirmSource.value = source
}

const confirmBatch = computed(() => {
  const source = confirmSource.value
  if (!source) return { rows: [], eligible: 0 }
  if (source.type === 'lane') return laneBatch(source.lane)
  const names = source.type === 'row' ? [source.name] : selectedNames.value
  const rows = rowsByName(names).filter(isBatchable)
  return { rows, eligible: rows.length }
})

const confirmLabel = computed(() => batchLabel(confirmBatch.value.rows))

/** The people the confirm names (R17): count, people and total before
 * deciding. */
const confirmPeople = computed(() => [...new Set(confirmBatch.value.rows.map((row) => row.employee_name))])

const bulkResult = ref(null)
// A refused row that is still pending keeps its reason on the row until the
// next batch (R18).
const refusedReasons = ref({})
const bulkApprove = createResource({
  url: 'helixhr.api.approve_clean_items',
  method: 'POST',
})

/** "2 approved. 1 was already decided. 1 wasn't approved and stays in the
 * queue." -- the persistent half of a partial result (R18). */
const bulkSummary = computed(() => {
  const result = bulkResult.value
  if (!result) return ''
  const parts = [`${result.approved} approved.`]
  const decidedCount = result.decided.length
  if (decidedCount) parts.push(`${decidedCount} ${decidedCount === 1 ? 'was' : 'were'} already decided.`)
  const stayed = result.stayed.length
  if (stayed) parts.push(`${stayed} ${stayed === 1 ? "wasn't approved and stays" : "weren't approved and stay"} in the queue.`)
  return parts.join(' ')
})

async function runBulk() {
  const source = confirmSource.value
  const rows = confirmBatch.value.rows
  if (bulkRunning.value || !source || !rows.length) return
  const fromSelection = source.type === 'selection'
  const items = rows.map(
    (row) => (fromSelection && bulkSelected.value.find((item) => item.name === row.name)) || batchItem(row),
  )
  const labels = Object.fromEntries(rows.map((row) => [row.name, `${row.employee_name} · ${rowSummary(row)}`]))
  const kinds = Object.fromEntries(rows.map((row) => [row.name, row.kind]))
  const plan = focusPlan(rows.map((row) => row.name))
  bulkRunning.value = true
  refusedReasons.value = {}
  try {
    const answer = await bulkApprove.submit({ items: JSON.stringify(items) })
    const results = (Array.isArray(answer) ? answer : answer?.message || []).filter(Boolean)
    const approvedNames = results.filter((result) => result.ok).map((result) => result.name)
    decrementApprovals(approvedNames.length)
    bulkSelected.value = fromSelection
      ? []
      : bulkSelected.value.filter((item) => !approvedNames.includes(item.name))
    confirmSource.value = null
    await reloadQueue()
    const refused = results.filter((result) => !result.ok)
    if (!refused.length) {
      // KTD6: the confirm was the guard; the toast only reports.
      notify.success(`${approvedNames.length} approved`)
      restoreFocus(plan)
      return
    }
    // R18: a partial result is never only a success toast. A refused row
    // still pending stays, with its reason; one that left the queue was
    // decided by somebody else, and the summary names it.
    const stillPending = new Set(pending.value.map((row) => row.name))
    const stayed = refused.filter((result) => stillPending.has(result.name))
    refusedReasons.value = Object.fromEntries(stayed.map((result) => [result.name, result.message]))
    bulkResult.value = {
      approved: approvedNames.length,
      stayed: stayed.map((result) => ({ ...result, label: labels[result.name], kind: kinds[result.name] })),
      decided: refused
        .filter((result) => !stillPending.has(result.name))
        .map((result) => ({ name: result.name, label: labels[result.name] })),
      plan,
    }
  } catch (error) {
    confirmSource.value = null
    // The call may have failed after some rows were approved, so the queue
    // (and through its watcher, the badge) is read again before saying so.
    await reloadQueue()
    notify.error(toPlainLeaveError(error) || 'Could not finish the bulk approval.', {
      detail: 'Some items may already be approved; the list was refreshed.',
    })
  } finally {
    bulkRunning.value = false
  }
}

function dismissBulkResult() {
  const plan = bulkResult.value?.plan
  bulkResult.value = null
  restoreFocus(plan)
}

function openRefused(result) {
  bulkResult.value = null
  open({ name: result.name, kind: result.kind || 'timesheet' })
}

/** The queue's reload, as a promise that never rejects: AsyncState already
 * shows a failed load. One load at a time: asked again mid-flight, it runs
 * once more when the current one settles, so the newest search, chip or
 * decision is always the last answer -- a slow search can never land over
 * the cleared one (or the badge's own read). */
let queueInflight = null
let queueAgain = false
/** Plan 2026-10-19-001 U9: the All pending row after (else before) `name`,
 * read before the decided row leaves the list. */
function allPendingNeighbour(name) {
  // Only rows that open a panel can take focus; the decided row is one.
  const rows = [...document.querySelectorAll('[data-overdue-name]')]
    .filter((row) => row.dataset.overdueName === name || row.querySelector('[data-decidable]'))
    .map((row) => row.dataset.overdueName)
  const index = rows.indexOf(name)
  return index < 0 ? null : rows[index + 1] || rows[index - 1] || null
}

/** KTD16 for All pending: focus the neighbour's link, else the tab's first
 * control, never `body`. */
function focusAllPendingRow(name) {
  nextTick(() => {
    const target =
      (name && document.querySelector(`[data-overdue-name="${CSS.escape(name)}"] a`)) ||
      document.querySelector('[data-testid="overdue-tab"] a, [data-testid="overdue-tab"] button')
    target?.focus()
  })
}

function reloadQueue() {
  if (queueInflight) {
    queueAgain = true
    return queueInflight
  }
  queueInflight = Promise.resolve(queue.reload())
    .catch(() => {})
    .then(() => {
      queueInflight = null
      if (!queueAgain) return undefined
      queueAgain = false
      return reloadQueue()
    })
  return queueInflight
}
reloadQueue()

// --- focus after a row leaves (KTD16) --------------------------------------

const queueList = ref(null)

function rowToggles(scope) {
  return scope ? [...scope.querySelectorAll('[data-row-toggle]')] : []
}

/** Where focus should go once `names` leave: the same position in their
 * lane, so the row after them takes it. Read before the rows go. */
function focusPlan(names) {
  const row = queueList.value?.querySelector(`[data-approval-name="${CSS.escape(names[0] || '')}"]`)
  const lane = row?.closest('[data-lane]')
  if (!lane) return null
  return { lane: lane.dataset.lane, index: rowToggles(lane).indexOf(row.querySelector('[data-row-toggle]')), names }
}

/** The next row in the lane, else the lane heading, else the first lane
 * heading left, else the page's main region. A toast never takes focus. */
function restoreFocus(plan) {
  if (!plan) return
  nextTick(() =>
    requestAnimationFrame(() => {
      const list = queueList.value
      const lane = list?.querySelector(`[data-lane="${CSS.escape(plan.lane)}"]`)
      const rows = rowToggles(lane).filter(
        (toggle) => !plan.names.includes(toggle.closest('[data-approval-name]')?.dataset.approvalName),
      )
      focusNear(
        rows,
        plan.index,
        lane?.querySelector('[data-lane-heading]'),
        list?.querySelector('[data-lane-heading]'),
        document.querySelector('main[tabindex]'),
      )
    }),
  )
}

// --- keyboard triage (KTD13, MH-06) ----------------------------------------
//
// Off by default, per user and per device. When on, keys act only while
// focus is inside the queue list (the handler sits on it) and never while
// someone is typing. `a` opens the bulk confirm; nothing is approved
// without it.
const shortcutsOn = ref(readShortcuts(session.user))
const shortcutsOpen = ref(false)
const firstPendingLane = computed(
  () => lanes.value.find((lane) => lane.key !== 'decided' && lane.key !== 'with-managers')?.key,
)

function toggleShortcuts() {
  shortcutsOn.value = !shortcutsOn.value
  writeShortcuts(session.user, shortcutsOn.value)
}

const TYPING = 'input, textarea, select, [contenteditable]:not([contenteditable="false"])'

function onQueueKey(event) {
  if (!shortcutsOn.value || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return
  const target = event.target
  if (!(target instanceof Element) || target.closest(TYPING) || target.isContentEditable) return
  if (!['j', 'k', 'x', 'a', 'Enter'].includes(event.key)) return
  const item = target.closest('[data-approval-name]')
  const row = item ? pending.value.find((candidate) => candidate.name === item.dataset.approvalName) : null
  if (event.key === 'j' || event.key === 'k') {
    const toggles = rowToggles(queueList.value)
    const index = item ? toggles.indexOf(item.querySelector('[data-row-toggle]')) : -1
    const next = index === -1 ? 0 : index + (event.key === 'j' ? 1 : -1)
    focusNear(toggles, next)
  } else if (event.key === 'x') {
    if (!row) return
    toggleBulk(row)
  } else if (event.key === 'Enter') {
    // Only on the row itself: Enter on a button inside the open detail is
    // that button's own.
    if (!row || !target.hasAttribute('data-row-toggle')) return
    if (row.name !== props.name) open(row)
  } else if (bulkSelected.value.length) {
    openConfirm({ type: 'selection' })
  } else if (isBatchable(row)) {
    openConfirm({ type: 'row', name: row.name })
  } else {
    return
  }
  event.preventDefault()
}
</script>

<template>
  <div>
    <PageHeader title="Approvals">
      <template #actions>
        <span
          v-if="owedPending.length && !searching"
          class="rounded-full bg-surface-amber-1 px-3 py-1 text-sm font-medium text-ink-amber-3"
        >
          <span class="tabular">{{ owedPending.length }}</span> waiting
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

    <p
      v-if="view === 'queue'"
      class="mb-4 text-sm text-ink-gray-5"
    >
      <template v-if="hasHrWork">
        Oldest first. Your own team, plus HR's work.
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

    <!-- Approvals search: everything waiting on you, and everything you have
         ever decided, by person, leave type or record. Outside the queue's
         AsyncState so a reload never takes the field (or its focus) away. -->
    <template v-if="view === 'queue' || (view === 'timesheets' && !oversightOnly)">
      <SearchField
        v-model="query"
        class="mb-4 sm:max-w-sm"
        label="Search approvals"
        placeholder="Person, leave type or record"
        maxlength="60"
        data-testid="approvals-search"
        :aria-describedby="searchHintId"
      />
      <!-- Always rendered: a live region inserted with its text already in
           it is often not announced. Only the text comes and goes. -->
      <p
        :id="searchHintId"
        class="text-sm text-ink-gray-6"
        :class="searching ? '-mt-2 mb-4' : ''"
        role="status"
      >
        <span
          v-if="searching"
          data-testid="approvals-search-hint"
        >Searching what's waiting on you and everything you've decided, including older decisions.</span>
      </p>
    </template>

    <div
      v-if="view === 'queue' || view === 'timesheets' || view === 'all-pending'"
      class="lg:flex lg:items-start lg:gap-6"
    >
      <!-- R8: every pending non-timesheet item in HR's company, by approver.
           Overdue only reproduces the old Overdue tab; `?view=overdue` lands
           here with it on. -->
      <section
        v-if="view === 'all-pending'"
        v-show="!name || isDesktop"
        class="min-w-0 lg:flex-1"
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
                  <!-- Plan 2026-10-19-001 U9 (R27): the row opens the decision
                       panel beside the list, where HR decides it like any
                       other; the caller's own item is said, not linked. -->
                  <component
                    :is="item.can_decide ? 'router-link' : 'div'"
                    :data-decidable="item.can_decide ? '' : undefined"
                    :to="
                      item.can_decide
                        ? {
                          name: 'ApprovalDetail',
                          params: { kind: item.route_kind, name: item.name },
                          query: { ...route.query, view: 'all-pending', overdue: overdueOnly ? '1' : undefined },
                        }
                        : undefined
                    "
                    class="surface-card elev-1 flex min-h-14 min-w-0 items-center gap-3 px-3"
                    :class="[
                      item.can_decide ? 'hover:bg-surface-gray-2' : '',
                      item.name === name ? 'ring-2 ring-outline-gray-4' : '',
                    ]"
                    :aria-current="item.name === name ? 'true' : undefined"
                  >
                    <span class="min-w-0 flex-1">
                      <span class="block truncate text-sm font-medium text-ink-gray-9">{{ item.employee_name }} · {{ item.title }}</span>
                      <span class="block truncate text-xs text-ink-gray-5">
                        {{ item.kind }}
                        <template v-if="item.overdue"> · Overdue</template>
                        <template v-if="item.is_own"> · your own request, so you can't decide it</template>
                      </span>
                    </span>
                    <span class="tabular shrink-0 text-right text-sm text-ink-gray-9">
                      {{ item.since_unknown ? 'Since unknown' : `${item.age_days} days` }}
                      <span class="block text-xs text-ink-gray-5">limit {{ item.threshold_days }}</span>
                    </span>
                  </component>
                </li>
              </ul>
            </section>
          </div>
        </AsyncState>
      </section>

      <!-- The queue. Plan 2026-10-15-002 U5/U6: the Approvals tab in lanes
           (My team, Decided by me, HR, IT); the Timesheets tab as Waiting on
           you and Waiting on HR, with HR's oversight list below. -->
      <div
        v-if="view !== 'all-pending'"
        v-show="!name || isDesktop || openInQueue"
        class="min-w-0 lg:flex-1"
        data-testid="approvals-queue"
      >
        <!-- MH-09: oversight-only (HR User) has no personal queue to show, so
             the page leads with All pending timesheets instead of an empty
             "waiting on you" card. -->
        <AsyncState
          v-if="!(view === 'timesheets' && oversightOnly)"
          section="approvals-queue"
          :resource="queue"
          :loading="queue.loading && !queue.data"
          :empty="!tabRows.length && !decidedRows.length"
          :empty-title="
            searching
              ? `No approvals match “${search}”`
              : view === 'timesheets' ? 'No timesheets waiting on you' : 'Nothing waiting on you'
          "
          :empty-body="
            searching
              ? 'Nothing waiting on you and nothing you have decided matches. Try a name, a leave type or a record number.'
              : view === 'timesheets'
                ? 'Weeks your team sends for approval appear here.'
                : 'Leave, attendance, change requests and requests waiting on you appear here.'
          "
          :skeleton-rows="3"
        >
          <!-- R2: with every pending lane empty, the empty copy sits above
               the Decided disclosure. -->
          <p
            v-if="!owedTabRows.length"
            class="surface-inset mb-6 p-4 text-sm text-ink-gray-7"
          >
            <template v-if="searching">
              Nothing waiting on you matches “{{ search }}”.
            </template>
            <template v-else>
              Nothing waiting on you.
            </template>
          </p>
          <!-- KTD13: keyboard triage listens here and nowhere else, so a key
               typed outside the list is just a key. -->
          <div
            ref="queueList"
            class="focus:outline-none"
            tabindex="-1"
            data-testid="approvals-list"
            @keydown="onQueueKey"
          >
            <section
              v-for="lane in lanes"
              :key="lane.key"
              class="mb-8"
              :data-testid="`lane-${lane.key}`"
              :data-lane="lane.key"
            >
              <!-- KTD8: the last 14 days of this caller's own decisions,
                   collapsed. Directly after My team, or first without it.
                   Searched: every matching decision, of any age, open. -->
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
                  <span class="text-sm text-ink-gray-5">
                    {{ searching ? 'all time' : 'last 14 days' }} ·
                    <span class="tabular">{{ decidedRows.length }}{{ searching && queue.data?.decided_is_capped ? '+' : '' }}</span>
                  </span>
                </button>
                <ul
                  v-show="decidedOpen"
                  id="approvals-decided-list"
                  class="mt-2 space-y-2"
                >
                  <li
                    v-for="row in decidedRows"
                    :key="row.id"
                    class="surface-card elev-1 flex items-center gap-3 p-3"
                    data-testid="decided-row"
                    :data-decided-name="row.name"
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
                      <span
                        v-if="searching && row.decided_on"
                        class="block text-xs text-ink-gray-5"
                      >Decided {{ formatDate(row.decided_on) }}</span>
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
                <!-- Plan 2026-10-31-001 R23: manager-stage leave HR may decide
                     but is not waiting on. Quieter than HR's own work and
                     collapsed; counted nowhere. -->
                <button
                  v-if="lane.key === 'with-managers'"
                  type="button"
                  class="mb-2 flex min-h-11 w-full cursor-pointer items-center gap-2 text-left"
                  :aria-expanded="withManagersOpen"
                  aria-controls="approvals-with-managers"
                  data-testid="with-managers-toggle"
                  data-lane-heading
                  @click="withManagersOpen = !withManagersOpen"
                >
                  <Icon
                    name="chevronRight"
                    class="shrink-0 text-ink-gray-5 transition-transform duration-200"
                    :class="withManagersOpen ? 'rotate-90' : ''"
                  />
                  <span class="label">{{ lane.title }}</span>
                  <span class="tabular text-sm text-ink-gray-5">({{ lane.rows.length }})</span>
                </button>
                <div
                  v-else
                  class="relative mb-3 flex flex-wrap items-center gap-2"
                >
                  <!-- KTD16: focus lands here when the lane's last row leaves. -->
                  <h2
                    class="text-base font-semibold text-ink-gray-9 focus:outline-none"
                    tabindex="-1"
                    data-lane-heading
                  >
                    {{ lane.title }}
                    <span class="tabular text-sm font-normal text-ink-gray-5">{{ lane.rows.length }}</span>
                  </h2>
                  <!-- KTD13 / MH-06: the opt-in, beside the first lane only. -->
                  <template v-if="canBulkSelect && lane.key === firstPendingLane">
                    <button
                      type="button"
                      class="ml-auto inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded px-2 text-sm text-ink-gray-6 hover:bg-surface-gray-2"
                      :aria-expanded="shortcutsOpen"
                      aria-controls="approvals-shortcuts"
                      data-testid="shortcuts-hint"
                      @click="shortcutsOpen = !shortcutsOpen"
                    >
                      Keyboard shortcuts
                      <span class="text-ink-gray-5">{{ shortcutsOn ? 'on' : 'off' }}</span>
                    </button>
                    <div
                      v-if="shortcutsOpen"
                      id="approvals-shortcuts"
                      class="surface-card elev-2 absolute right-0 top-full z-10 mt-1 w-72 p-3 text-sm"
                      role="group"
                      aria-label="Keyboard shortcuts"
                      @keydown.esc.stop="shortcutsOpen = false"
                    >
                      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-ink-gray-7">
                        <dt class="tabular font-medium text-ink-gray-9">
                          j / k
                        </dt>
                        <dd>Next / previous row</dd>
                        <dt class="tabular font-medium text-ink-gray-9">
                          x
                        </dt>
                        <dd>Select the row</dd>
                        <dt class="tabular font-medium text-ink-gray-9">
                          Enter
                        </dt>
                        <dd>Open it</dd>
                        <dt class="tabular font-medium text-ink-gray-9">
                          a
                        </dt>
                        <dd>Approve, after the confirm</dd>
                      </dl>
                      <p class="mt-2 text-xs text-ink-gray-5">
                        Keys work only while focus is in the list.
                      </p>
                      <button
                        type="button"
                        role="switch"
                        class="mt-2 flex min-h-11 w-full cursor-pointer items-center justify-between gap-3 text-left font-medium text-ink-gray-9"
                        :aria-checked="shortcutsOn ? 'true' : 'false'"
                        data-testid="shortcuts-toggle"
                        @click="toggleShortcuts"
                      >
                        Use keyboard shortcuts
                        <span
                          class="flex h-6 w-10 shrink-0 items-center rounded-full p-0.5 transition-colors duration-200"
                          :class="shortcutsOn ? 'justify-end bg-heading' : 'justify-start bg-surface-gray-3'"
                          aria-hidden="true"
                        >
                          <span class="h-5 w-5 rounded-full bg-white" />
                        </span>
                      </button>
                    </div>
                  </template>
                </div>
                <!-- R23: the With managers rows are not rendered while it is
                     collapsed, so keyboard triage never walks hidden rows. -->
                <div :id="lane.key === 'with-managers' ? 'approvals-with-managers' : undefined">
                  <template v-if="lane.key !== 'with-managers' || withManagersOpen">
                    <p
                      v-if="lane.key === 'with-managers'"
                      class="mb-3 text-sm text-ink-gray-5"
                    >
                      Still with their manager, so not counted as yours. Open one to decide it for them.
                    </p>
                    <!-- Plan 2026-10-04-003 U7 (R13, R19): inside a lane, oldest waiting
                 first -- change requests (never batchable), the rows the
                 server flagged, and everything else. -->
                    <section
                      v-for="section in lane.sections"
                      :key="section.key"
                      class="mb-6"
                      :data-testid="`queue-section-${section.key}`"
                    >
                      <div
                        v-if="section.title"
                        class="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1"
                      >
                        <!-- MH-06 / HR-12: select the whole of Looks normal. -->
                        <button
                          v-if="section.key === 'normal' && canBulkSelect && selectableCount(section.rows) > 1"
                          type="button"
                          role="checkbox"
                          class="-my-2 flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center"
                          :aria-checked="ARIA_CHECKED[selectAllState(section.rows)]"
                          :aria-label="`Select all ${selectableCount(section.rows)} that look normal`"
                          data-testid="select-all-normal"
                          @click="toggleAll(section.rows)"
                        >
                          <span
                            class="flex h-5 w-5 items-center justify-center rounded border"
                            :class="selectAllState(section.rows) === 'none' ? 'border-outline-gray-3' : 'border-heading bg-heading text-white'"
                            aria-hidden="true"
                          >
                            <Icon
                              v-if="selectAllState(section.rows) === 'all'"
                              name="check"
                              class="h-3 w-3"
                            />
                            <span
                              v-else-if="selectAllState(section.rows) === 'some'"
                              class="h-0.5 w-2.5 rounded bg-white"
                            />
                          </span>
                        </button>
                        <h3 class="label">
                          {{ section.title }}
                          <span class="tabular text-ink-gray-5">{{ section.rows.length }}</span>
                        </h3>
                        <!-- MH-04: the clean lane in two taps, at every width. The
                         batch is the lane's own eligible rows, never the
                         checkboxes, and it always goes through the confirm. -->
                        <Button
                          v-if="section.key === 'normal' && approveAllLabel(laneBatch(lane.key))"
                          class="ml-auto"
                          variant="subtle"
                          theme="green"
                          data-testid="approve-all"
                          @click="openConfirm({ type: 'lane', lane: lane.key })"
                        >
                          {{ approveAllLabel(laneBatch(lane.key)) }}
                        </Button>
                      </div>
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
                          <!-- HR-12: one person's clean rows together. -->
                          <button
                            v-if="section.key === 'normal' && canBulkSelect && selectableCount(group.rows) > 1"
                            type="button"
                            role="checkbox"
                            class="-my-2 -ml-1 flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center"
                            :aria-checked="ARIA_CHECKED[selectAllState(group.rows)]"
                            :aria-label="`Select all of ${group.employee_name}'s ${selectableCount(group.rows)}`"
                            data-testid="select-all-person"
                            @click="toggleAll(group.rows)"
                          >
                            <span
                              class="flex h-5 w-5 items-center justify-center rounded border"
                              :class="selectAllState(group.rows) === 'none' ? 'border-outline-gray-3' : 'border-heading bg-heading text-white'"
                              aria-hidden="true"
                            >
                              <Icon
                                v-if="selectAllState(group.rows) === 'all'"
                                name="check"
                                class="h-3 w-3"
                              />
                              <span
                                v-else-if="selectAllState(group.rows) === 'some'"
                                class="h-0.5 w-2.5 rounded bg-white"
                              />
                            </span>
                          </button>
                          <Avatar
                            class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-[10px] font-bold text-ink-gray-7"
                            :photo-url="group.photo_url"
                            :initials="group.initials"
                            :size="24"
                          />
                          <span class="min-w-0 truncate text-sm font-medium text-ink-gray-9">
                            {{ group.employee_name }}
                          </span>
                          <!-- MH-07: with one row, the row already says how long. -->
                          <span class="tabular text-xs text-ink-gray-5">
                            {{ group.rows.length }} {{ group.rows.length === 1 ? 'item' : 'items' }}
                            <template v-if="group.rows.length > 1 && ageLabel(group.oldest)">
                              · waiting {{ ageLabel(group.oldest) }}
                            </template>
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
                                :class="selectable(row) ? '' : 'cursor-default'"
                                :aria-label="isSelected(row) ? `Unselect ${rowSummary(row)}` : `Select ${rowSummary(row)}`"
                                :aria-pressed="isSelected(row)"
                                :data-testid="`bulk-check-${row.kind}`"
                                :tabindex="selectable(row) ? 0 : -1"
                                @click.stop="toggleBulk(row)"
                              >
                                <!-- A row the batch can't take keeps the column and its
                                 rule, so every avatar in the lane lines up; only
                                 the box is hidden. -->
                                <span
                                  class="flex h-5 w-5 items-center justify-center rounded border"
                                  :class="[
                                    isSelected(row) ? 'border-heading bg-heading text-white' : 'border-outline-gray-3',
                                    selectable(row) ? '' : 'invisible',
                                  ]"
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
                                data-row-toggle
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
                                      v-if="showsHrChip(row)"
                                      class="shrink-0 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-bold text-ink-gray-7"
                                      data-testid="hr-chip"
                                    >HR</span>
                                  </span>
                                  <!-- MH-02: on a phone the dates are why the row is
                                   read, so the summary wraps there rather than
                                   cutting them off; wider screens have room. -->
                                  <span class="block break-words text-sm text-ink-gray-6 sm:truncate">
                                    {{ rowSummary(row) }}
                                  </span>
                                  <span
                                    v-if="row.kind === 'timesheet' && row.expected_hours != null"
                                    class="tabular block text-xs text-ink-gray-5"
                                  >
                                    {{ trimNumber(row.total_hours) }} of
                                    {{ trimNumber(row.expected_hours) }} h expected
                                  </span>
                                  <span
                                    v-if="row.kind === 'leave' && row.balance_after != null"
                                    class="block text-xs text-ink-gray-5"
                                  >
                                    Balance after:
                                    <span class="tabular">{{ trimNumber(row.balance_after) }}</span>
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
                                    data-testid="hr-line"
                                  >
                                    {{ hrLine(row) }}
                                  </span>
                                  <!-- Plan 2026-10-18-001 R10: this approver's own row,
                                   which their account cannot change yet. -->
                                  <span
                                    v-if="row.blocked_reason"
                                    class="block text-xs font-medium text-ink-amber-3"
                                    data-testid="row-blocked-reason"
                                  >
                                    View only: {{ row.blocked_reason }}
                                  </span>
                                  <!-- R18: a batch refused this row and it is still
                                   pending; the reason stays with it. -->
                                  <span
                                    v-if="refusedReasons[row.name]"
                                    class="block text-xs font-medium text-ink-red-4"
                                    data-testid="bulk-refused-reason"
                                  >
                                    Not approved: {{ refusedReasons[row.name] }}
                                  </span>
                                </span>

                                <span class="shrink-0 text-right">
                                  <span
                                    class="tabular block font-medium text-ink-gray-9"
                                    :data-testid="row.kind === 'request' ? 'request-claim' : null"
                                  >{{ rowAmount(row) }}</span>
                                  <!-- MH-07: the amount above is the request's; this is
                                   how long it has waited, and says so. -->
                                  <span
                                    v-if="ageLabel(row)"
                                    class="tabular block text-xs text-ink-gray-5"
                                  >waiting {{ ageLabel(row) }}</span>
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
                                <ApprovalEvidence
                                  v-if="selected"
                                  v-bind="evidence"
                                  v-model:reason="reason"
                                  v-model:note="note"
                                  layout="inline"
                                  @act="decide"
                                  @close-reason="closeReason"
                                  @close-note="closeNote"
                                />
                              </AsyncState>
                            </div>
                          </li>
                        </ul>
                      </div>
                    </section>
                  </template>
                </div>
              </template>
            </section>
          </div>

          <p
            v-if="overflow"
            class="mt-3 text-sm text-ink-gray-5"
          >
            Showing the <span class="tabular">{{ owedPending.length }}</span> oldest.
            <span class="tabular">{{ overflow }}</span> more are waiting.
          </p>
        </AsyncState>

        <!-- R6/R9: HR's view of every pending timesheet in their company,
             by approver, with Send reminder. -->
        <TimesheetOversight
          v-if="view === 'timesheets' && session.canOverseeTimesheets"
          :class="oversightOnly ? '' : 'mt-8'"
        />
      </div>

      <!-- Desktop: the full evidence beside the queue. Approve is not on
           screen at all until it has loaded, which is the whole of P2-AE6 --
           the decision is only available once the thing being decided is
           visible. -->
      <aside
        v-if="name && (isDesktop || view === 'all-pending' || !openInQueue)"
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

          <ApprovalEvidence
            v-if="selected"
            v-bind="evidence"
            v-model:reason="reason"
            v-model:note="note"
            layout="panel"
            @act="decide"
            @close-reason="closeReason"
            @close-note="closeNote"
          />
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
        @click="openConfirm({ type: 'selection' })"
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
        <p
          v-if="confirmBatch.rows.length"
          class="text-sm text-ink-gray-7"
          data-testid="bulk-confirm-label"
        >
          {{ confirmLabel }} for
          {{ confirmPeople.join(', ') }}.
        </p>
        <p
          v-else
          class="text-sm text-ink-gray-7"
        >
          Nothing here can be approved together any more.
        </p>
        <!-- MH-04: past the server's cap the batch is the oldest 60, and
             the rest wait for the next run. -->
        <p
          v-if="confirmBatch.eligible > confirmBatch.rows.length"
          class="mt-2 text-sm text-ink-gray-7"
        >
          The oldest <span class="tabular">{{ confirmBatch.rows.length }}</span> of
          <span class="tabular">{{ confirmBatch.eligible }}</span>; the other
          <span class="tabular">{{ confirmBatch.eligible - confirmBatch.rows.length }}</span> stay in the queue.
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
            :disabled="bulkRunning"
            @click="confirmBulk = false"
          >
            Back
          </Button>
          <Button
            variant="solid"
            theme="green"
            :loading="bulkRunning"
            :disabled="!confirmBatch.rows.length"
            data-testid="bulk-confirm"
            @click="runBulk"
          >
            Approve {{ confirmBatch.rows.length }}
          </Button>
        </div>
      </template>
    </Dialog>

    <!-- Plan 2026-10-05-001 U3: the change request's Accept, confirmed.
         Errors stay inside the dialog so the manager reads them where they
         clicked; the confirm stays disabled while the decision is in flight. -->
    <!-- Plan 2026-10-31-001 U10: the same confirm guards a roster request's
         Approve, which writes several people's shifts in one go. -->
    <Dialog
      :model-value="confirmAccept"
      :options="{ title: confirmAction === 'Approve' ? 'Approve these shift changes?' : 'Accept this change request?' }"
      @update:model-value="(value) => !value && closeAcceptConfirm()"
    >
      <template #body-content>
        <template v-if="confirmAction === 'Approve'">
          <p class="text-sm text-ink-gray-7">
            {{ approveLabel }} for
            {{ (selected?.employees || []).map((person) => person.employee_name).join(', ') }}.
            The new shifts are written now, in your name.
          </p>
          <p class="mt-2 text-sm text-ink-gray-6">
            If anyone's shifts have moved since {{ selected?.manager_name || 'the manager' }} sent this,
            nothing is applied and it goes back to them with the reason.
          </p>
          <p
            v-if="selected?.conflicts?.length"
            class="surface-alert mt-3 p-3 text-sm"
            data-testid="roster-confirm-conflicts"
          >
            Approving will send this back:
            {{ selected.conflicts.map((conflict) => conflict.message).join(' ') }}
          </p>
        </template>
        <p
          v-else
          class="text-sm text-ink-gray-7"
        >
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
            @click="decide(confirmAction, { confirmed: true })"
          >
            {{ confirmAction === 'Approve' ? approveLabel : 'Accept and reopen' }}
          </Button>
        </div>
      </template>
    </Dialog>

    <!-- R18: a partial result stays until dismissed. It says how many went
         through, names what somebody else already decided, and gives a way
         back to each row that is still waiting. A full success is a toast
         instead. -->
    <Dialog
      :model-value="!!bulkResult"
      :options="{ title: 'Bulk approval' }"
      @update:model-value="(value) => !value && dismissBulkResult()"
    >
      <template #body-content>
        <p
          class="text-sm text-ink-gray-7"
          data-testid="bulk-result-summary"
        >
          {{ bulkSummary }}
        </p>
        <ul
          v-if="bulkResult?.decided.length"
          class="mt-3 space-y-2"
        >
          <li
            v-for="result in bulkResult.decided"
            :key="result.name"
            class="surface-inset p-2 text-sm"
            data-testid="bulk-result-decided"
          >
            <span class="block truncate">{{ result.label || result.name }}</span>
            <span class="block text-xs text-ink-gray-5">Already decided by someone else</span>
          </li>
        </ul>
        <ul
          v-if="bulkResult?.stayed.length"
          class="mt-3 space-y-2"
        >
          <li
            v-for="result in bulkResult.stayed"
            :key="result.name"
            class="surface-inset flex items-center justify-between gap-3 p-2 text-sm"
            data-testid="bulk-result-refused"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate">{{ result.label || result.name }}</span>
              <span class="block text-xs text-ink-gray-6">{{ result.message }}</span>
            </span>
            <button
              class="min-h-11 shrink-0 cursor-pointer text-sm font-medium text-ink-blue-link underline underline-offset-2"
              type="button"
              @click="openRefused(result)"
            >
              Open it
            </button>
          </li>
        </ul>
      </template>
      <template #actions>
        <Button
          variant="solid"
          theme="blue"
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
