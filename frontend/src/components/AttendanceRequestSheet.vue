<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { createResource, Dialog, FormControl, Button } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StepStrip from '@/components/StepStrip.vue'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { HOLIDAY_LIST_UNKNOWN } from '@/lib/holidays'
import { formatDate, formatDateRange, today } from '@/lib/dates'

// P3-U6 step 2 / P3-R12, P3-R13, P3-R17. "Fix a day": the sheet that raises
// an Attendance Request, and the sheet that shows one afterwards.
//
// Two modes, one surface, because they are the same object at two moments and
// a second screen for the second moment would be a second place for the copy
// to drift:
//
//   * `name` empty  -- the ask. Reason, dates, half day, explanation, and the
//     server's own preview of what sending it would do.
//   * `name` set    -- one request, its two steps, and whatever the employee
//     can still do to it. Reached from the Requests column, from Home's
//     "Needs you", and from `/attendance/requests/:name` directly.
//
// Nothing about the request is derived here. The days that would be marked,
// the days that would be skipped and *why*, and whether it can be sent at
// all are all `get_attendance_request_preview`'s answer (P3-KTD14): the
// browser cannot see the holiday list, the approved leave or the attendance
// rows those decisions rest on, and guessing at them is how a screen ends up
// promising something the server then refuses.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** The day the ask starts from -- the day sheet's date, or today. */
  date: { type: String, default: '' },
  /** An existing request to show instead of a new one. */
  name: { type: String, default: '' },
  /** From `get_my_attendance_requests`: the two reasons HRMS offers and the
   * manager who would get this. Passed in so the sheet costs no extra read. */
  reasons: { type: Array, default: () => [] },
  approverName: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

// --- the ask -------------------------------------------------------------

const reason = ref('')
const fromDate = ref('')
const toDate = ref('')
const halfDay = ref(false)
const explanation = ref('')
const error = ref('')
const sending = ref(false)

const reasonChoices = computed(() => (props.reasons.length ? props.reasons : ['Work From Home', 'On Duty']))

function reset() {
  const start = props.date || today()
  reason.value = reasonChoices.value[0]
  fromDate.value = start
  toDate.value = start
  halfDay.value = false
  explanation.value = ''
  error.value = ''
}

// A half day is one day, and the To field follows From. The server derives
// `half_day_date` from `from_date` for a one-day range rather than trusting
// the form, so the record is right even if this never runs -- this only
// keeps the *visible* form honest (the same rule as LeaveForm's).
watch([halfDay, fromDate], () => {
  if (halfDay.value) toDate.value = fromDate.value
  else if (toDate.value < fromDate.value) toDate.value = fromDate.value
})

// --- what the server says this request would do --------------------------

const preview = createResource({
  url: 'helixhr.api.get_attendance_request_preview',
  makeParams: () => ({
    from_date: fromDate.value,
    to_date: halfDay.value ? fromDate.value : toDate.value,
    half_day: halfDay.value ? 1 : 0,
    reason: reason.value,
  }),
})

// Debounced, because a date input fires while the year is still being typed.
// 250ms is under the 300ms the design system allows for a state transition,
// so the line never feels like it is lagging the form.
let previewTimer = null
function refreshPreview() {
  clearTimeout(previewTimer)
  if (props.name || !reason.value || !fromDate.value || !toDate.value) return
  previewTimer = setTimeout(() => preview.fetch(), 250)
}
watch([reason, fromDate, toDate, halfDay], refreshPreview)
onUnmounted(() => clearTimeout(previewTimer))

// A network failure carries no `messages`, so `toPlainMessage` returns '' and
// the line disappeared -- the same sentence the leave paths fall back to is
// said instead, because "no explanation" is the one thing this must not be.
const previewError = computed(() =>
  preview.error
    ? toPlainMessage(preview.error.messages?.[0] ?? preview.error.message) || FALLBACK_ERROR
    : '',
)

/** "2 days would be marked as Work From Home", in the server's numbers. */
const markLine = computed(() => {
  const data = preview.data
  if (!data?.known) return ''
  if (!data.mark) return 'None of those days would change.'
  const days = `${data.mark} day${data.mark === 1 ? '' : 's'}`
  const replacing = data.replaces_absent
    ? ` (${data.replaces_absent} currently marked absent)`
    : ''
  return `${days} would be marked as ${reason.value}${replacing}.`
})

/** Why the other days are not in that number. Named individually, because
 * "3 skipped" without the reason is what sends somebody to HR. */
const skippedLine = computed(() => {
  const skipped = preview.data?.skipped
  if (!skipped) return ''
  const parts = []
  if (skipped.holiday) parts.push(`${skipped.holiday} holiday${skipped.holiday === 1 ? '' : 's'}`)
  if (skipped.weekly_off)
    parts.push(`${skipped.weekly_off} weekly off${skipped.weekly_off === 1 ? '' : 's'}`)
  if (skipped.on_leave)
    parts.push(`${skipped.on_leave} day${skipped.on_leave === 1 ? '' : 's'} you were on leave`)
  return parts.length ? `Skipped: ${parts.join(', ')}.` : ''
})

/** P3-KTD14. A day that already carries real attendance is refused, not
 * previewed away: HRMS rewrites such a row in place when HR confirms and
 * cancels it outright afterwards, so a request over one can erase attendance
 * rather than revert it. The HR Request is the honest pointer. */
const overwriteLine = computed(() => {
  const overwrite = preview.data?.overwrite
  if (!overwrite) return ''
  return `${overwrite} of those days already ${overwrite === 1 ? 'has' : 'have'} attendance, so this can't fix ${overwrite === 1 ? 'it' : 'them'}.`
})

const unknownDays = computed(() => !!preview.data && !preview.data.known)

const missingApprover = computed(() => !props.approverName)

const canSend = computed(
  () =>
    !props.name &&
    !!reason.value &&
    !!fromDate.value &&
    !missingApprover.value &&
    !!preview.data?.can_send &&
    !sending.value,
)

const sendLabel = computed(() =>
  props.approverName ? `Send to ${props.approverName.split(/\s+/)[0]}` : 'Send',
)

const create = createResource({ url: 'helixhr.api.create_my_attendance_request', method: 'POST' })
// One resource for the one endpoint, used by both callers: a brand-new
// request's Send (below) and a stored Draft's Send action (P3-U9).
const send = createResource({ url: 'helixhr.api.send_my_attendance_request', method: 'POST' })

/**
 * Create the draft, then send it -- the two methods P3-U5 exposes.
 *
 * Not one call, because `send_my_attendance_request` re-derives the preview
 * against the stored record and refuses the same things the sheet showed. If
 * the send is the half that fails, the draft stays: it appears in the
 * Requests column with a Send action, rather than vanishing along with the
 * explanation the employee just typed.
 */
function submit() {
  if (!canSend.value) return
  return run(async () => {
    const draft = await create.submit({
      from_date: fromDate.value,
      to_date: halfDay.value ? fromDate.value : toDate.value,
      reason: reason.value,
      half_day: halfDay.value ? 1 : 0,
      explanation: explanation.value,
    })
    await send.submit({ name: draft.name, expected_modified: draft.modified })
  })
}

// --- one existing request ------------------------------------------------

const request = createResource({
  url: 'helixhr.api.get_my_attendance_request',
  makeParams: () => ({ name: props.name }),
})

const shown = computed(() => (props.name ? request.data : null))

const withdraw = createResource({ url: 'helixhr.api.withdraw_my_attendance_request', method: 'POST' })

/** The half every action on this sheet shares: one in-flight flag, one
 * plain-sentence error, close on success, and -- when a stored request is on
 * screen -- reload it on failure, because a refusal usually means it moved
 * (P3-U9). */
async function run(action) {
  error.value = ''
  sending.value = true
  try {
    await action()
    emit('changed')
    open.value = false
  } catch (e) {
    error.value = toPlainMessage(e?.messages?.[0] ?? e?.message) || FALLBACK_ERROR
    if (props.name) request.fetch()
  } finally {
    sending.value = false
  }
}

function act(resource, params) {
  return run(() => resource.submit(params))
}

// Reopening is a fresh ask, and a stale half-typed explanation must never
// follow the employee onto another day. Declared here, below the two
// resources and `refreshPreview`, because `immediate: true` runs during setup
// and a `const` declared further down is still in its temporal dead zone
// then -- which is silently swallowed by Vue as a warning and leaves the
// sheet with no preview at all.
watch(
  () => [props.modelValue, props.name, props.date].join('/'),
  () => {
    if (!props.modelValue) return
    if (props.name) {
      error.value = ''
      request.fetch()
    } else {
      reset()
      refreshPreview()
    }
  },
  { immediate: true },
)

const title = computed(() => {
  if (!props.name) return props.date ? `Fix ${formatDate(props.date)}` : 'Fix a day'
  return 'Your attendance request'
})
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title, size: 'sm' }"
  >
    <template #body-content>
      <!-- ------------------------------------------------ one request --- -->
      <AsyncState
        v-if="name"
        section="attendance-request"
        :resource="request"
        :empty="!request.data"
        empty-title="That request isn't here any more"
        empty-body="It may have been withdrawn, or HR may have removed it."
        skeleton="block"
        skeleton-height="h-40"
      >
        <template #error-title>
          We couldn't load this request
        </template>

        <div
          v-if="shown"
          class="space-y-3"
          data-testid="attendance-request-detail"
        >
          <StepStrip
            :state="shown.workflow_state"
            :docstatus="shown.docstatus"
            :approver="shown.approver_name"
          />

          <dl class="text-sm">
            <div class="flex justify-between gap-3">
              <dt class="text-ink-gray-6">
                {{ shown.reason }}
              </dt>
              <dd class="text-ink-gray-9">
                {{ formatDateRange(shown.from_date, shown.to_date) }}
              </dd>
            </div>
            <div
              v-if="shown.half_day"
              class="mt-1 flex justify-between gap-3"
            >
              <dt class="text-ink-gray-6">
                Half day
              </dt>
              <dd class="text-ink-gray-9">
                {{ formatDate(shown.half_day_date) }}
              </dd>
            </div>
            <div class="mt-1 flex justify-between gap-3">
              <dt class="text-ink-gray-6">
                Status
              </dt>
              <dd>
                <StatusBadge
                  kind="attendance"
                  :status="shown.workflow_state"
                  :docstatus="shown.docstatus"
                  :approver="shown.approver_name"
                />
              </dd>
            </div>
          </dl>

          <p
            v-if="shown.explanation"
            class="surface-inset p-3 text-sm text-ink-gray-7"
          >
            “{{ shown.explanation }}”
          </p>

          <!-- The reason it came back, on the request itself. Not having to
               open something else to find out what to change is the whole
               point of carrying it here (P3-R17). -->
          <p
            v-if="shown.reason_sent_back"
            class="surface-alert p-3 text-sm"
            data-testid="attendance-request-sent-back"
          >
            Sent back: {{ shown.reason_sent_back }}
          </p>

          <p
            v-if="error"
            class="text-sm text-ink-red-4"
            role="alert"
          >
            {{ error }}
          </p>

          <div class="flex flex-wrap gap-2 border-t border-outline-gray-1 pt-3">
            <!-- A draft that was created but never sent -- the second half of
                 a send that failed. It is still the employee's to send. -->
            <Button
              v-if="shown.workflow_state === 'Draft'"
              variant="solid"
              theme="blue"
              :loading="sending"
              :disabled="sending || missingApprover"
              @click="act(send, { name: shown.name, expected_modified: shown.modified })"
            >
              {{ sendLabel }}
            </Button>
            <Button
              v-if="shown.can_withdraw"
              variant="outline"
              theme="red"
              :disabled="sending"
              @click="act(withdraw, { name: shown.name })"
            >
              Withdraw
            </Button>
            <Button
              variant="subtle"
              @click="open = false"
            >
              Close
            </Button>
          </div>

          <!-- Withdraw is the only edit the portal has: the request's own
               fields are frozen outside Draft and there is no portal method
               that changes them, so "change it" means withdraw this one and
               ask again with the reason above still on screen (P3-R17a). -->
          <p
            v-if="shown.can_withdraw && shown.reason_sent_back"
            class="text-xs text-ink-gray-5"
          >
            To change the days, withdraw this one and ask again.
          </p>
          <p
            v-else-if="!shown.can_withdraw && shown.docstatus === 0"
            class="text-xs text-ink-gray-5"
          >
            This one is with HR now. Ask HR to sort it out.
          </p>
        </div>
      </AsyncState>

      <!-- ------------------------------------------------------ the ask --- -->
      <form
        v-else
        class="space-y-4"
        @submit.prevent="submit"
      >
        <fieldset>
          <legend class="label mb-2">
            What happened
          </legend>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="choice in reasonChoices"
              :key="choice"
              type="button"
              class="min-h-11 cursor-pointer rounded-full border px-4 text-sm font-medium transition-colors duration-200"
              :class="
                reason === choice
                  ? 'border-field bg-field text-white'
                  : 'border-outline-gray-2 bg-surface-white text-ink-gray-8 hover:bg-surface-gray-2'
              "
              :aria-pressed="reason === choice"
              @click="reason = choice"
            >
              {{ choice }}
            </button>
          </div>
        </fieldset>

        <div class="grid grid-cols-2 gap-3">
          <FormControl
            v-model="fromDate"
            type="date"
            label="From"
            required
          />
          <FormControl
            v-model="toDate"
            type="date"
            label="To"
            :disabled="halfDay"
            required
          />
        </div>

        <label class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-7">
          <input
            v-model="halfDay"
            type="checkbox"
            class="h-4 w-4"
          >
          Half day
          <span class="text-ink-gray-5">(the From date)</span>
        </label>

        <!-- The server's own answer, before Send: how many days would be
             marked, which ones would not and why, and the one refusal the
             employee has to act on rather than retry (P3-R13). -->
        <div
          class="surface-inset p-3"
          data-testid="attendance-request-preview"
        >
          <p
            v-if="preview.loading"
            class="text-sm text-ink-gray-5"
          >
            Working out the days…
          </p>
          <p
            v-else-if="previewError"
            class="text-sm text-ink-red-4"
            role="alert"
          >
            {{ previewError }}
          </p>
          <p
            v-else-if="unknownDays"
            class="text-sm text-ink-gray-7"
          >
            {{ HOLIDAY_LIST_UNKNOWN }}
          </p>
          <template v-else-if="preview.data">
            <!-- P3-R24: counts are numbers, so they line up (`.tabular`). -->
            <p class="tabular text-sm text-ink-gray-9">
              {{ markLine }}
            </p>
            <p
              v-if="skippedLine"
              class="tabular mt-0.5 text-sm text-ink-gray-6"
            >
              {{ skippedLine }}
            </p>
            <p
              v-if="overwriteLine"
              class="tabular mt-1 text-sm text-ink-red-4"
            >
              {{ overwriteLine }}
              <router-link
                class="underline underline-offset-2"
                :to="{
                  name: 'Requests',
                  query: {
                    category: 'Other',
                    subject: `Attendance problem on ${formatDate(fromDate)}`,
                  },
                }"
              >
                Ask HR about those days
              </router-link>.
            </p>
          </template>
          <p
            v-if="approverName"
            class="mt-2 text-sm text-ink-gray-7"
          >
            Goes to {{ approverName }} first, then HR.
          </p>
        </div>

        <FormControl
          v-model="explanation"
          type="textarea"
          label="Anything your manager should know (optional)"
        />

        <!-- No manager, no request: the same refusal, and the same sentence,
             as the timesheet's (P3-R14). -->
        <div
          v-if="missingApprover"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          You don't have a manager set up to approve this yet.
          <router-link
            class="underline underline-offset-2"
            :to="{ name: 'Requests', query: { category: 'Other', subject: 'Please set my manager' } }"
          >
            Ask HR
          </router-link>
          to set one.
        </div>

        <p
          v-if="error"
          class="text-sm text-ink-red-4"
          role="alert"
        >
          {{ error }}
        </p>

        <div
          class="sticky bottom-0 -mx-1 flex items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3"
        >
          <Button
            variant="solid"
            theme="blue"
            type="submit"
            :loading="sending"
            :disabled="!canSend"
          >
            {{ sendLabel }}
          </Button>
          <Button
            variant="subtle"
            type="button"
            @click="open = false"
          >
            Cancel
          </Button>
        </div>
      </form>
    </template>
  </Dialog>
</template>
