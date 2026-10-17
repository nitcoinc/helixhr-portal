<script setup>
// Plan 2026-10-17-001 U7 (R21, KTD14). The one evidence and decision surface
// for an approval, drawn two ways: `inline` under the opened row on a phone
// (the manager never loses their place in the queue, P2-U7 step 7) and
// `panel` beside the queue on a desktop. The evidence itself differs by
// layout on purpose -- a 7-day strip on a phone, the full project-by-day
// table on a desktop -- while the employee's words, the error, the reason
// and note surfaces and the action row are one copy.
//
// The page owns every decision: this component only renders the state it is
// handed and reports which button was pressed.
import { computed } from 'vue'
import { Button, FormControl } from 'frappe-ui'
import Avatar from '@/components/Avatar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import DecisionActions from '@/components/approvals/DecisionActions.vue'
import { getReasonCopy } from '@/lib/workKinds'
import { formatDate, formatDateRange } from '@/lib/dates'
import { trimNumber } from '@/lib/hours'
import { ATTENDANCE_LABEL } from '@/lib/week'

const props = defineProps({
  /** `get_approval_detail`'s answer for the open record. */
  selected: { type: Object, required: true },
  /** 'inline' (phone, under the row) or 'panel' (desktop aside). */
  layout: { type: String, default: 'panel' },
  /** The outcomes the server allows (P4-KTD6); drawn exactly. */
  actions: { type: Array, default: () => [] },
  /** This record's decision is in flight. */
  busy: { type: Boolean, default: false },
  /** The one outcome a reason, note or confirm is open for, or ''. */
  armed: { type: String, default: '' },
  approveLabel: { type: String, default: '' },
  /** The employee's own words, wherever this kind keeps them. */
  quote: { type: String, default: '' },
  /** P4-R11: who handed this to HR, and what they said. */
  hrLine: { type: String, default: '' },
  actionError: { type: String, default: '' },
  /** Which reason-bearing outcome the reason surface is armed for, or ''. */
  reasonFor: { type: String, default: '' },
  reasonError: { type: String, default: '' },
  noteOpen: { type: Boolean, default: false },
})

const reason = defineModel('reason', { type: String, default: '' })
const note = defineModel('note', { type: String, default: '' })

const emit = defineEmits(['act', 'close-reason', 'close-note'])

const panel = computed(() => props.layout === 'panel')
/** The panel breathes a little more than the phone's inline block. */
const gap = computed(() => (panel.value ? 'mt-4' : 'mt-3'))

const DAY_LETTERS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']

/** The tallest bar in the 7-day strip. Scaled against a full day, so a light
 * week reads as a light week rather than being stretched to fill the strip. */
const dayScale = computed(() => {
  const hours = (props.selected?.day_totals || []).map((day) => day.hours)
  return Math.max(8, ...hours, 0)
})

function barHeight(hours) {
  return `${Math.round((Math.min(hours, dayScale.value) / dayScale.value) * 100)}%`
}

/** P3-R16. What the calendar shows for each day a request names -- the
 * difference between a correction and a request over a day that is already
 * accounted for. */
const DAY_SHOWS = { holiday: 'holiday', weekly_off: 'weekly off' }

function requestedDayLabel(day) {
  if (day.holiday) return DAY_SHOWS[day.holiday] || day.holiday
  if (day.status) return ATTENDANCE_LABEL[day.status] || day.status
  return 'nothing recorded'
}

const firstName = computed(() => (props.selected?.employee_name || '').split(/\s+/)[0])

// What the reason field is called, per outcome: the one kind map's copy (KTD6).
const reasonCopy = computed(() => getReasonCopy(props.reasonFor))

/** The action row's props, the same on both layouts. */
const decisionProps = computed(() => ({
  kind: props.selected.kind,
  actions: props.actions,
  disabled: props.busy,
  loading: props.busy,
  armed: props.armed,
  approveLabel: props.approveLabel,
}))

/** The field's own label, naming the person the sentence is written to. */
const reasonPlaceholder = computed(() =>
  props.reasonFor === 'Send Back'
    ? `What should ${firstName.value || 'they'} change?`
    : reasonCopy.value.placeholder,
)
</script>

<template>
  <component
    :is="panel ? 'article' : 'div'"
    :class="panel ? 'surface-card elev-1 p-4' : null"
    :aria-label="panel ? 'Request detail' : undefined"
  >
    <template v-if="!panel">
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
          {{ trimNumber(selected.total_hours) }} h ·
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
    </template>
    <template v-else>
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
            v-if="hrLine"
            class="text-sm text-ink-gray-5"
          >
            {{ hrLine }}
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
              {{ trimNumber(selected.total_hours) }} h
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
    </template>

    <p
      v-if="quote"
      class="surface-inset p-3 text-sm text-ink-gray-7"
      :class="gap"
    >
      {{ firstName }}: “{{ quote }}”
    </p>

    <p
      v-if="actionError"
      class="surface-alert p-3 text-sm"
      :class="gap"
      role="alert"
    >
      {{ actionError }}
    </p>

    <!-- Send back and Reject need their reason on the same surface as the
         button, not behind a dialog: the employee reads this sentence, so it
         is written next to the evidence it is about (P2-U7 step 4).
         P4-U4: one surface for every reason-bearing outcome, armed by
         whichever button opened it and relabelled -- and emptied -- if
         another one does. -->
    <div
      v-if="reasonFor"
      class="surface-field elev-2"
      :class="panel ? 'mt-4 p-4' : 'mt-3 p-3'"
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
        @click="emit('close-reason')"
      >
        Cancel
      </Button>
    </div>

    <!-- Send to HR is a routing act, so its words are a note to a colleague
         and optional (P4-R5). -->
    <div
      v-if="noteOpen"
      :class="gap"
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
        @click="emit('close-note')"
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
    <!-- P4-R1. Exactly the outcomes the server allows, in one order on every
         kind: the decision, the recoverable no, the final no, the hand-over.
         An outcome missing from `actions` is not rendered rather than
         rendered disabled -- a greyed-out Reject on a timesheet would still
         teach a manager that a week can be rejected (P4-KTD2). -->
    <div
      v-if="panel"
      class="mt-4 flex flex-wrap items-center justify-end gap-2"
    >
      <DecisionActions
        v-bind="decisionProps"
        @act="(action) => emit('act', action)"
      />
    </div>
    <DecisionActions
      v-else
      class="mt-3"
      v-bind="decisionProps"
      @act="(action) => emit('act', action)"
    />
  </component>
</template>
