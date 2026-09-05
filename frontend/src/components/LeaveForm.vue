<script setup>
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'
import { toPlainLeaveError } from '@/lib/errorMap'
import { today } from '@/lib/dates'

// P2-U5. The ask sheet. Three things changed from the Phase 1 form and each
// one closes a real defect:
//
//   * The document is built on the server (`helixhr.api.apply_for_leave`),
//     not here and posted through `frappe.client.insert`. Employee, approver
//     and half-day date are session/record facts, so none of them is a
//     parameter a caller can get wrong or forge (P2-R27).
//   * The day count comes back from HRMS's own `get_number_of_leave_days`
//     before the request is sent, so the employee sees the number that will
//     actually be stored, holidays already deducted (P2-U5 scenario 2).
//   * There is no separate half-day date field. It was a third date input
//     that had to be kept in step with the first one by a watcher, and when
//     the watcher and the user disagreed the *watcher* lost -- the request
//     went in with a half-day date from two edits ago (scenario 3).
const props = defineProps({
  /** Values to start from, for "Edit and resend" on a sent-back request. */
  initial: { type: Object, default: null },
})
const emit = defineEmits(['applied', 'cancel'])

const context = createResource({
  url: 'helixhr.api.get_leave_form_context',
  auto: true,
  onSuccess: (data) => {
    if (!leaveType.value && data?.types?.length) leaveType.value = data.types[0].leave_type
    if (!fromDate.value && data?.today) {
      fromDate.value = data.today
      toDate.value = data.today
    }
  },
})

const startingDate = props.initial?.from_date || ''
const leaveType = ref(props.initial?.leave_type || '')
const fromDate = ref(startingDate || today())
const toDate = ref(props.initial?.to_date || startingDate || today())
const halfDay = ref(!!props.initial?.half_day)
const description = ref(props.initial?.description || '')
const error = ref('')

const types = computed(() => context.data?.types || [])
const approver = computed(() => context.data?.approver || '')
const approverName = computed(() => context.data?.approver_name || '')
const approverFirstName = computed(() => (approverName.value || '').split(/\s+/)[0] || '')

/** Initials for the approver chip, the same device the canvas uses on the
 * Approvals queue. Two letters at most; one is fine for a single-word name. */
const approverInitials = computed(() =>
  (approverName.value || '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join(''),
)

function balanceLabel(type) {
  if (type.left === null || type.left === undefined) return ''
  return `${type.left} left`
}

/** The first word of the type, which is what fits on a chip at 360px.
 * "Casual Leave" and "Sick Leave" both end in the same word. */
function chipLabel(type) {
  return type.leave_type.replace(/\s+Leave$/i, '')
}

// A half day is one day. Forcing To to follow From here keeps the *visible*
// form honest; the server does the same thing again on insert, so the record
// is correct even if this never runs.
watch([halfDay, fromDate], () => {
  if (halfDay.value) toDate.value = fromDate.value
  else if (toDate.value < fromDate.value) toDate.value = fromDate.value
})

// --- what the server says this request actually is ----------------------

const preview = createResource({
  url: 'helixhr.api.get_leave_day_count',
  makeParams: () => ({
    leave_type: leaveType.value,
    from_date: fromDate.value,
    to_date: halfDay.value ? fromDate.value : toDate.value,
    half_day: halfDay.value ? 1 : 0,
  }),
})

const previewError = computed(() =>
  preview.error ? toPlainLeaveError(preview.error) : '',
)

// Debounced, because a date input fires while the user is still typing the
// year. 250ms is below the 300ms the design system allows for a state
// transition, so the block never feels like it is lagging the form.
let previewTimer = null
function refreshPreview() {
  clearTimeout(previewTimer)
  if (!leaveType.value || !fromDate.value || !toDate.value) return
  previewTimer = setTimeout(() => preview.fetch(), 250)
}
watch([leaveType, fromDate, toDate, halfDay], refreshPreview, { immediate: true })
onMounted(refreshPreview)
onUnmounted(() => clearTimeout(previewTimer))

// --- sending -------------------------------------------------------------

const apply = createResource({
  url: 'helixhr.api.apply_for_leave',
  method: 'POST',
})

const missingApprover = computed(() => !!context.data && !approver.value)

/** Send is off until the request is describable: a type, both dates, and
 * somebody to send it to. A missing approver is the one that matters --
 * without this the form created a draft that HR Settings then refused, and
 * the employee was left with a half-made record and a validation error
 * (P2-U5 scenario 4). */
const canSubmit = computed(
  () =>
    !!leaveType.value &&
    !!fromDate.value &&
    !!toDate.value &&
    !missingApprover.value &&
    !apply.loading,
)

const sendLabel = computed(() =>
  approverFirstName.value ? `Send to ${approverFirstName.value}` : 'Send',
)

async function submit() {
  error.value = ''
  if (!canSubmit.value) return
  try {
    const result = await apply.submit({
      leave_type: leaveType.value,
      from_date: fromDate.value,
      // Sent for completeness; the server derives it again from `half_day`
      // rather than trusting it.
      to_date: halfDay.value ? fromDate.value : toDate.value,
      half_day: halfDay.value ? 1 : 0,
      description: description.value,
    })
    emit('applied', result)
  } catch (e) {
    // The browser never overrides a server refusal (P2-U5 scenario 2): it
    // translates the sentence and shows it.
    error.value = toPlainLeaveError(e)
  }
}
</script>

<template>
  <form
    class="space-y-4"
    @submit.prevent="submit"
  >
    <fieldset>
      <legend class="label mb-2">
        Type
      </legend>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="type in types"
          :key="type.leave_type"
          type="button"
          class="min-h-11 cursor-pointer rounded-full border px-4 text-sm transition-colors duration-200"
          :class="
            leaveType === type.leave_type
              ? 'border-field bg-field text-white'
              : 'border-outline-gray-2 bg-surface-white text-ink-gray-8 hover:bg-surface-gray-2'
          "
          :aria-pressed="leaveType === type.leave_type"
          @click="leaveType = type.leave_type"
        >
          <span class="font-medium">{{ chipLabel(type) }}</span>
          <span
            v-if="balanceLabel(type)"
            class="tabular ml-2"
            :class="leaveType === type.leave_type ? 'text-blue-200' : 'text-ink-gray-5'"
          >{{ balanceLabel(type) }}</span>
        </button>
      </div>
      <p
        v-if="context.data && !types.length"
        class="mt-2 text-sm text-ink-gray-5"
      >
        You have no leave types allocated yet. Ask HR.
      </p>
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

    <!-- What the server says this request is: HRMS's own day count, the
         non-working days it skipped, what is left afterwards, and who it
         goes to. Shown before Send, so nothing about the request is a
         surprise once it is sent. -->
    <div
      class="surface-inset p-3"
      data-testid="leave-preview"
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
      <template v-else-if="preview.data">
        <p class="text-ink-gray-9">
          <span class="tabular font-heading text-lg font-bold">{{
            preview.data.total_leave_days
          }}</span>
          working {{ preview.data.total_leave_days === 1 ? 'day' : 'days' }}
        </p>
        <p class="mt-0.5 text-sm text-ink-gray-6">
          <template v-if="preview.data.skipped_label">
            {{ preview.data.skipped_label }} ·
          </template>
          leaves <span class="tabular font-medium">{{ preview.data.balance_after }}</span>
          {{ leaveType }} after
        </p>
      </template>
      <p
        v-if="approverName"
        class="mt-2 flex items-center gap-2 text-sm text-ink-gray-7"
      >
        <span
          class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-xs font-bold text-ink-green-3"
          aria-hidden="true"
        >{{ approverInitials }}</span>
        Goes to {{ approverName }}
      </p>
    </div>

    <FormControl
      v-model="description"
      type="textarea"
      label="Reason (optional)"
    />

    <!-- No approver, no request. The draft that used to be created here was
         refused by HR Settings a moment later and left behind. -->
    <div
      v-if="missingApprover"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      You don't have a leave approver yet, so this can't be sent.
      <router-link
        class="underline underline-offset-2"
        :to="{
          name: 'Requests',
          query: { category: 'Other', subject: 'Please set my leave approver' },
        }"
      >
        Ask HR
      </router-link>.
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
        :loading="apply.loading"
        :disabled="!canSubmit"
        type="submit"
      >
        {{ sendLabel }}
      </Button>
      <Button
        variant="subtle"
        type="button"
        @click="emit('cancel')"
      >
        Cancel
      </Button>
    </div>
  </form>
</template>
