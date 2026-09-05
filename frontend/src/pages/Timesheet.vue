<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import WeekGrid from '@/components/WeekGrid.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import {
  addCalendarDays,
  formatDate,
  formatDateRange,
  mondayOf,
  today,
  weekDates,
} from '@/lib/dates'

// P2-U6 / P2-R12 / P2-AE5. `/timesheet` and `/timesheet/:weekStart` are the
// same component: the week is a route parameter, so refresh and browser Back
// land on the same week, Home's sent-back row opens *that* week, and a Past
// weeks row opens the week it is about rather than the current one.
const props = defineProps({
  weekStart: { type: String, default: '' },
})

const router = useRouter()

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const FULL_WEEK_HOURS = 40
const FULL_DAY_HOURS = 8

// P2-AE3, and the defect this page carried until now: the week was computed
// with `new Date('YYYY-MM-DD')` and read back with `.getDay()`/`.getDate()`,
// which parses a calendar date as a UTC instant and reads it in host-local
// time -- so an employee west of Greenwich opened the *previous* week. There
// is one calendar module (lib/dates.js) and this page now uses it.
const monday = computed(() => mondayOf(props.weekStart || today()))

const week = createResource({
  url: 'helixhr.api.get_my_week',
  makeParams: () => ({ week_start: monday.value }),
  auto: true,
})

const projects = createResource({
  url: 'helixhr.api.get_my_projects',
  auto: true,
})

// --- the model ----------------------------------------------------------
//
// One model feeds both layouts (P2-U6 step 9): a *line* is a project + task
// + note, carrying an hours map keyed by calendar date. The phone renders
// the selected day's slice of it; the desktop renders it whole. Nothing is
// duplicated between the two, so nothing can drift between them.

const lines = ref([])
const selectedDate = ref(today())
const error = ref('')
const savedAt = ref(null)
const submitting = ref(false)
const confirmCopy = ref(false)

let nextLineId = 0
/** Stable client identity, so Vue keys never fall back to the array index
 * (P2-U6 step 6): removing the first row otherwise re-keys every row under
 * it and moves the focus and the typed value with it. */
function newLine(fields = {}) {
  nextLineId += 1
  return { id: `line-${nextLineId}`, project: '', task: '', note: '', hours: {}, ...fields }
}

function round(value) {
  return Math.round(value * 100) / 100
}

/** Server rows are one row per project/task/note *per day*; a line is the
 * same booking across the week. Two server rows that agree on all three and
 * fall on the same day are summed -- the portal cannot create that, but a
 * timesheet edited in Desk can. */
function linesFrom(rows, weekMonday) {
  const found = new Map()
  const inWeek = new Set(weekDates(weekMonday))
  for (const row of rows || []) {
    if (!inWeek.has(row.date)) continue
    const key = [row.project || '', row.task || '', row.note || ''].join(' | ')
    if (!found.has(key)) {
      found.set(
        key,
        newLine({ project: row.project || '', task: row.task || '', note: row.note || '' }),
      )
    }
    const line = found.get(key)
    line.hours[row.date] = round((line.hours[row.date] || 0) + Number(row.hours || 0))
  }
  return [...found.values()]
}

/** What the server is asked to store: only days that carry real hours. A
 * line added but not filled in is a state of the editor, not of the week. */
function serialize() {
  const rows = []
  for (const line of lines.value) {
    for (const [date, hours] of Object.entries(line.hours)) {
      if (!hours) continue
      rows.push({ date, project: line.project, task: line.task || '', hours, note: line.note || '' })
    }
  }
  return rows
}

let savedSnapshot = '[]'
function snapshot() {
  return JSON.stringify(serialize())
}

const isDirty = computed(() => snapshot() !== savedSnapshot)

function dayTotal(iso) {
  return round(lines.value.reduce((sum, line) => sum + (line.hours[iso] || 0), 0))
}

const days = computed(() =>
  weekDates(monday.value).map((iso, index) => ({
    iso,
    weekday: WEEKDAYS[index],
    dayOfMonth: Number(iso.split('-')[2]),
    label: formatDate(iso),
    isWeekend: index >= 5,
    total: dayTotal(iso),
  })),
)

const weekTotal = computed(() => round(days.value.reduce((sum, day) => sum + day.total, 0)))

function loadFromServer() {
  lines.value = linesFrom(week.data?.timesheet?.rows, monday.value)
  savedSnapshot = snapshot()
  error.value = ''
  if (!days.value.some((day) => day.iso === selectedDate.value)) {
    const now = today()
    selectedDate.value = days.value.some((day) => day.iso === now) ? now : monday.value
  }
}
watch(() => week.data, loadFromServer)
watch(monday, () => {
  savedAt.value = null
  week.reload()
})

function addLine(date) {
  const line = newLine()
  if (date) line.hours[date] = 0
  lines.value.push(line)
}

function removeLine({ id, date }) {
  const line = lines.value.find((row) => row.id === id)
  if (!line) return
  // Removing a row on the phone removes it from *that day*; a line still
  // booked on other days is not deleted out from under them.
  if (date) delete line.hours[date]
  if (!date || !Object.keys(line.hours).length) {
    lines.value = lines.value.filter((row) => row.id !== id)
  }
}

function setHours({ id, date, value }) {
  const line = lines.value.find((row) => row.id === id)
  if (!line) return
  line.hours[date] = Number.isFinite(value) ? Math.min(24, Math.max(0, round(value))) : 0
}

function updateLine({ id, field, value }) {
  const line = lines.value.find((row) => row.id === id)
  if (!line) return
  line[field] = value
  // A task belongs to a project; changing the project cannot leave the old
  // one's task behind, which the server would refuse anyway (P2-U6 step 5).
  if (field === 'project') line.task = ''
}

/** "Same projects as Wednesday? Copy them." Hours and the note come with it;
 * nothing about approval does. */
function copyDay(fromIso) {
  for (const line of lines.value) {
    const hours = line.hours[fromIso]
    if (hours) line.hours[selectedDate.value] = hours
  }
}

// --- copy previous week -------------------------------------------------

const previousMonday = computed(() => addCalendarDays(monday.value, -7))
const previousWeek = createResource({ url: 'helixhr.api.get_my_week' })

async function copyPreviousWeek() {
  confirmCopy.value = false
  error.value = ''
  try {
    const data = await previousWeek.submit({ week_start: previousMonday.value })
    const rows = data?.timesheet?.rows || []
    if (!rows.length) {
      error.value = 'There is nothing in last week to copy.'
      return
    }
    // Re-dated by seven calendar days, not by an offset in hours: a week
    // that crosses a daylight-saving change is still seven days long.
    lines.value = linesFrom(
      rows.map((row) => ({ ...row, date: addCalendarDays(row.date, 7) })),
      monday.value,
    )
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not read last week.'
  }
}

/** Copying over a week that already has rows is destructive, so it asks
 * first (P2-U6 scenario 3). An empty week just fills. */
function startCopyPreviousWeek() {
  if (serialize().length) confirmCopy.value = true
  else copyPreviousWeek()
}

// --- state, validation, actions -----------------------------------------

const workflowState = computed(() => week.data?.timesheet?.workflow_state)
const isReadOnly = computed(
  () =>
    !!workflowState.value &&
    workflowState.value !== 'Draft' &&
    workflowState.value !== 'Rejected',
)
const approverName = computed(() => week.data?.approver_name || '')

// The manager's reason arrives with the week itself. It used to be read here
// with `frappe.client.get_list` on Comment, which the Employee Self Service
// role cannot read: the call 403'd every time, so the page told the employee
// their week was sent back and never told them why.
const rejectionComment = computed(() => week.data?.timesheet?.rejection_comment)

/** Said before Save is pressed rather than after the server refuses, in the
 * same words the server uses (P2-U6 step 3). The server still decides. */
const issues = computed(() => {
  const found = []
  if (lines.value.some((line) => !line.project && Object.values(line.hours).some(Boolean))) {
    found.push('Every row needs a project.')
  }
  for (const day of days.value) {
    if (day.total > 24) found.push(`${day.weekday} has more than 24 hours booked.`)
  }
  if (!serialize().length) found.push('Add some hours before saving this week.')
  return found
})

const canWrite = computed(() => !isReadOnly.value && !issues.value.length && !submitting.value)

const save = createResource({ url: 'helixhr.api.save_my_week', method: 'POST' })
const submit = createResource({ url: 'helixhr.api.submit_my_week', method: 'POST' })

async function saveDraft() {
  if (!canWrite.value) return
  error.value = ''
  submitting.value = true
  try {
    await save.submit({ week_start: monday.value, rows: JSON.stringify(serialize()) })
    await week.reload()
    savedAt.value = Date.now()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not save. Please check your rows.'
  } finally {
    submitting.value = false
  }
}

/** Save and send, in one server call (P2-AE4).
 *
 * This used to be `await saveDraft()` followed by a direct
 * `frappe.model.workflow.apply_workflow` -- and `saveDraft` caught its own
 * error, so a refused save was swallowed and the *previously saved* rows
 * went to the manager. `helixhr.api.submit_my_week` does both inside one
 * transaction under one row lock, and refuses a stale `modified`, so a
 * second tap cannot produce a second transition either (P2-U6 scenario 7).
 */
async function submitWeek() {
  if (!canWrite.value) return
  error.value = ''
  submitting.value = true
  try {
    await submit.submit({
      week_start: monday.value,
      rows: JSON.stringify(serialize()),
      expected_modified: week.data?.timesheet?.modified || undefined,
    })
    await week.reload()
    savedAt.value = Date.now()
  } catch (e) {
    error.value = e?.messages?.[0] || 'Could not send this week.'
  } finally {
    submitting.value = false
  }
}

function goToWeek(iso) {
  router.push({ name: 'TimesheetWeek', params: { weekStart: iso } })
}
function prevWeek() {
  goToWeek(addCalendarDays(monday.value, -7))
}
function nextWeek() {
  goToWeek(addCalendarDays(monday.value, 7))
}
function thisWeek() {
  goToWeek(mondayOf(today()))
}

// "Saved 2 min ago" has to keep being true while the page sits open, and a
// clock that only ticks on re-render is a clock that lies.
const now = ref(Date.now())
let ticker = null
onMounted(() => {
  ticker = setInterval(() => {
    now.value = Date.now()
  }, 30000)
})
onUnmounted(() => clearInterval(ticker))

const savedLabel = computed(() => {
  if (isDirty.value) return 'Unsaved changes'
  if (!savedAt.value) return ''
  const minutes = Math.floor((now.value - savedAt.value) / 60000)
  return minutes < 1 ? 'Saved just now' : `Saved ${minutes} min ago`
})

function barHeight(day) {
  if (!day.total) return 0
  return Math.max(8, Math.min(100, (day.total / FULL_DAY_HOURS) * 100))
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Timesheet">
      <template #actions>
        <router-link
          to="/timesheet/history"
          class="-my-2 inline-flex min-h-11 cursor-pointer items-center text-sm text-ink-gray-6 underline underline-offset-2 hover:text-ink-gray-9"
        >
          Past weeks
        </router-link>
      </template>
    </PageHeader>

    <AsyncState
      section="timesheet-week"
      :resource="week"
      :empty="false"
      skeleton="field"
      skeleton-height="h-56"
    >
      <!-- The field block: the one anchored region on the page, and on a
           phone it *is* the day picker (P2-U6 step 8). It carries the week's
           identity, its status and its total, which is everything the
           employee needs to know before they touch a row. -->
      <section
        class="elev-2 overflow-hidden rounded-xl bg-field lg:hidden"
        aria-label="Week"
      >
        <div class="flex items-center justify-between px-1 py-2">
          <button
            class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-blue-100 hover:bg-white/10"
            type="button"
            aria-label="Previous week"
            @click="prevWeek"
          >
            <Icon name="chevronLeft" />
          </button>
          <div class="flex min-w-0 items-center gap-2">
            <span class="tabular truncate font-semibold text-white">
              {{ formatDateRange(monday, week.data?.week_end) }}
            </span>
            <StatusBadge
              v-if="workflowState"
              kind="timesheet"
              :status="workflowState"
            />
          </div>
          <button
            class="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-blue-100 hover:bg-white/10"
            type="button"
            aria-label="Next week"
            @click="nextWeek"
          >
            <Icon name="chevronRight" />
          </button>
        </div>

        <div
          class="grid grid-cols-7"
          role="tablist"
          aria-label="Days this week"
        >
          <button
            v-for="day in days"
            :key="day.iso"
            class="relative flex cursor-pointer flex-col items-center gap-1 border-r border-white/10 px-1 pb-2 pt-2.5 last:border-r-0 hover:bg-white/10"
            :class="day.iso === selectedDate ? 'bg-white/15' : day.isWeekend ? 'bg-field-deep/50' : ''"
            type="button"
            role="tab"
            :aria-selected="day.iso === selectedDate"
            :aria-label="`${day.weekday} ${day.dayOfMonth}, ${day.total} hours`"
            @click="selectedDate = day.iso"
          >
            <span
              v-if="day.iso === selectedDate"
              class="absolute inset-x-0 top-0 h-0.5 bg-signal"
            />
            <span
              class="text-[11px] font-medium uppercase tracking-wide"
              :class="day.isWeekend ? 'text-blue-300' : 'text-blue-200'"
            >
              {{ day.weekday }}
            </span>
            <span class="tabular text-sm font-semibold text-white">{{ day.dayOfMonth }}</span>
            <span class="mt-1 flex h-14 w-full items-end justify-center">
              <span
                class="w-1/2 max-w-8 rounded-sm"
                :class="day.total ? 'bg-signal' : 'bg-transparent'"
                :style="{ height: `${barHeight(day)}%` }"
              />
            </span>
            <span
              class="tabular text-[11px]"
              :class="day.total ? 'text-blue-100' : 'text-blue-300'"
            >
              {{ day.total ? `${day.total}h` : '0h' }}
            </span>
          </button>
        </div>

        <div class="flex items-center justify-between gap-3 bg-field-deep/60 px-4 py-2.5">
          <p class="text-sm text-blue-100">
            <span class="tabular font-semibold text-white">{{ weekTotal }}</span>
            of {{ FULL_WEEK_HOURS }} hours this week
          </p>
          <button
            class="cursor-pointer text-sm font-medium text-signal hover:underline"
            type="button"
            @click="thisWeek"
          >
            This week
          </button>
        </div>
      </section>

      <!-- Desktop keeps the same facts in the toolbar the grid needs: the
           week, its status, and the two week-level actions. -->
      <div class="hidden lg:mb-4 lg:flex lg:items-center lg:justify-between lg:gap-4">
        <div class="flex items-center gap-2">
          <button
            class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg border border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2"
            type="button"
            aria-label="Previous week"
            @click="prevWeek"
          >
            <Icon name="chevronLeft" />
          </button>
          <button
            class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg border border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2"
            type="button"
            aria-label="Next week"
            @click="nextWeek"
          >
            <Icon name="chevronRight" />
          </button>
          <h2 class="type-section ml-2 text-ink-gray-9">
            Week of {{ formatDateRange(monday, week.data?.week_end) }}
          </h2>
          <button
            class="cursor-pointer text-sm font-medium text-ink-blue-link underline underline-offset-2"
            type="button"
            @click="thisWeek"
          >
            This week
          </button>
          <StatusBadge
            v-if="workflowState"
            kind="timesheet"
            :status="workflowState"
          />
        </div>
        <Button
          v-if="!isReadOnly"
          variant="outline"
          :loading="previousWeek.loading"
          @click="startCopyPreviousWeek"
        >
          Copy last week
        </Button>
      </div>

      <div
        v-if="workflowState === 'Rejected'"
        class="surface-alert mt-4 p-3 text-sm"
        role="alert"
      >
        This week was sent back<span v-if="rejectionComment">: &ldquo;{{ rejectionComment }}&rdquo;</span>.
        Fix it up and send it again.
      </div>

      <div class="mt-4">
        <WeekGrid
          :lines="lines"
          :projects="projects.data || []"
          :days="days"
          :selected-date="selectedDate"
          :full-week-hours="FULL_WEEK_HOURS"
          :read-only="isReadOnly"
          @add-line="addLine"
          @remove-line="removeLine"
          @set-hours="setHours"
          @update-line="updateLine"
          @copy-day="copyDay"
        />
      </div>

      <!-- Copy last week is a phone action too; there it belongs under the
           day's rows rather than in a toolbar the phone does not have. -->
      <div
        v-if="!isReadOnly"
        class="mt-3 lg:hidden"
      >
        <Button
          variant="subtle"
          class="w-full"
          :loading="previousWeek.loading"
          @click="startCopyPreviousWeek"
        >
          Copy last week
        </Button>
      </div>
    </AsyncState>

    <ul
      v-if="issues.length && !isReadOnly"
      class="surface-inset mt-3 space-y-1 p-3 text-sm text-ink-gray-7"
    >
      <li
        v-for="issue in issues"
        :key="issue"
      >
        {{ issue }}
      </li>
    </ul>

    <p
      v-if="error"
      class="surface-alert mt-3 p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </p>

    <!-- `.action-bar` (index.css) sits above the phone tab bar and inside the
         safe area. This bar used to be `sticky bottom-0`, which put Submit
         *underneath* the fixed tab bar at 360px -- the primary action on the
         page was unreachable without scrolling past the end of the document
         (P2-U3 scenario 2). -->
    <div
      v-if="!isReadOnly"
      class="action-bar flex items-center gap-3"
    >
      <p class="min-w-0 flex-1 truncate text-sm text-ink-gray-6">
        <!-- The approver is named where there is room for the sentence; on
             a phone the bar carries the save state and two buttons. -->
        <span
          v-if="approverName"
          class="hidden lg:inline"
        >Goes to {{ approverName }} for approval.</span>
        <span v-if="savedLabel"> {{ savedLabel }}</span>
      </p>
      <Button
        variant="subtle"
        :loading="save.loading"
        :disabled="!canWrite"
        @click="saveDraft"
      >
        Save
      </Button>
      <Button
        variant="solid"
        theme="blue"
        :loading="submitting"
        :disabled="!canWrite"
        @click="submitWeek"
      >
        {{ workflowState === 'Rejected' ? 'Send again' : 'Submit week' }}
      </Button>
    </div>

    <Dialog
      v-model="confirmCopy"
      :options="{ title: 'Replace this week?' }"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          This week already has hours in it. Copying the week of
          {{ formatDate(previousMonday) }} replaces them.
        </p>
      </template>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button
            variant="subtle"
            @click="confirmCopy = false"
          >
            Keep what's here
          </Button>
          <Button
            variant="solid"
            theme="blue"
            @click="copyPreviousWeek"
          >
            Replace
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
