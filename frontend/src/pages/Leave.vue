<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import LeaveForm from '@/components/LeaveForm.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import { formatDate, formatDateRange, isCalendarDate, today } from '@/lib/dates'
import { toPlainLeaveError } from '@/lib/errorMap'

// P2-U5 / KTD5. `/leave` and `/leave/:name` are the same component: the
// selected record is a route parameter, so refresh and browser Back land on
// the same leave, and the phone view and the desktop panel are two shapes of
// one state rather than two screens.
const props = defineProps({
  name: { type: String, default: '' },
})

const router = useRouter()

// P2-U5 / P2-R22 / P2-R27. One session-scoped read where the page used to
// make three -- `hrms.api.get_leave_balance_map`, `hrms.api.get_leave_applications`,
// and a generic `frappe.client.get_list` against User to turn approver ids
// into names. The server also owns the lifecycle state, the manager's reason
// and whether withdrawal is legal, because all three are properties of the
// record rather than of the screen.
const pageLimit = ref(20)
const leave = createResource({
  url: 'helixhr.api.get_my_leave',
  makeParams: () => ({ limit: pageLimit.value }),
  auto: true,
})

const balances = computed(() => leave.data?.balances || [])
const applications = computed(() => leave.data?.applications || [])
const total = computed(() => leave.data?.total || 0)
const serverToday = computed(() => leave.data?.today || today())

function barWidth(entry) {
  if (!entry.allocated) return 0
  return Math.max(0, Math.min(100, (entry.left / entry.allocated) * 100))
}

// Coming up / Past, which replaces the four filter pills. The pills asked the
// employee to guess which bucket their leave was in; the only division that
// matters on this page is "still ahead of me" against "already happened", and
// a `.label` over a run of cards says it without a control at all.
const groups = computed(() =>
  [
    {
      key: 'coming-up',
      label: 'Coming up',
      rows: applications.value.filter((app) => !app.to_date || app.to_date >= serverToday.value),
    },
    {
      key: 'past',
      label: 'Past',
      rows: applications.value.filter((app) => app.to_date && app.to_date < serverToday.value),
    },
  ].filter((group) => group.rows.length),
)

const moreCount = computed(() => Math.max(0, total.value - applications.value.length))
function showMore() {
  pageLimit.value = Math.min(200, pageLimit.value + 20)
  leave.reload()
}

// The date tile (index.css, `.date-tile`). Parsed straight off the date-only
// string rather than through a Date object: `new Date('2026-09-14')` is
// midnight UTC and renders as the 13th west of Greenwich, which is exactly the
// class of bug P2-R5 and P2-AE3 exist to prevent.
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
function tile(app) {
  if (!isCalendarDate(app.from_date)) return null
  const [, month, day] = app.from_date.split('-')
  const parsed = { month: MONTHS[Number(month) - 1], day: String(Number(day)), through: '' }
  // The artboard's second line on a multi-day tile ("-16"), which is what
  // makes a three-day absence readable without reaching the meta line.
  if (isCalendarDate(app.to_date) && app.to_date !== app.from_date) {
    parsed.through = `–${Number(app.to_date.split('-')[2])}`
  }
  return parsed
}

/** The approver's *first* name, which is what the canvas puts on the badge
 * ("Waiting for Priya"). A full name turns a 92px pill into a two-line block
 * at 360px. */
function approverFirstName(app) {
  return app?.approver_name ? app.approver_name.split(/\s+/)[0] : ''
}

// The lifecycle, as one word the badge can render. "Waiting for HR" is
// deliberately not a Leave Application status: it is the P2-U1 legacy state
// (docstatus 0 with status Approved, which never consumed balance) and
// StatusBadge renders an unmapped value verbatim in resting grey, which is
// exactly right -- it must not read as "Approved" (P2-R10).
const BADGE_STATUS = {
  open: 'Open',
  sent_back: 'Rejected',
  approved: 'Approved',
  waiting_for_hr: 'Waiting for HR',
  cancelled: 'Cancelled',
}
function badgeStatus(app) {
  return BADGE_STATUS[app.state] || app.status
}

function durationLabel(app) {
  const days = app.total_leave_days
  return `${days} day${days === 1 ? '' : 's'}`
}

/** Cancelling an approved leave is HR administration, which this portal
 * deliberately does not do (P2-U5 goal). The honest path is one HR Request
 * with the record already described in it. */
function askHrToCancel(app) {
  return {
    name: 'Requests',
    query: {
      category: 'Other',
      subject: `Please cancel my ${app.leave_type} on ${formatDateRange(app.from_date, app.to_date)}`,
    },
  }
}

// --- the selected record ------------------------------------------------

// The list is bounded (P2-R22), so an old leave reached from a notification
// or a bookmark is not necessarily in it. The detail is its own read rather
// than a lookup into whatever the list happened to return.
const detail = createResource({
  url: 'helixhr.api.get_my_leave_detail',
  makeParams: () => ({ name: props.name }),
})

watch(
  () => props.name,
  (name) => {
    if (name) detail.fetch()
  },
  { immediate: true },
)

const selected = computed(() => (props.name ? detail.data : null))

function closeDetail() {
  router.push({ name: 'Leave' })
}

// One breakpoint decides the *shape*, never the identity: the URL is the same
// at both widths (KTD5). 1024px is where the shell drops the phone tab bar
// for the side nav, so it is also where there is room for two columns.
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

// --- asking, and asking again -------------------------------------------

const showForm = ref(false)
const formInitial = ref(null)

function askForLeave() {
  formInitial.value = null
  showForm.value = true
}

/** "Edit and resend": the sent-back request's own values, back in the sheet.
 * A new application, not an edit of the old one -- the rejected record is the
 * manager's decision and stays as it is until the employee withdraws it. */
function editAndResend(app) {
  formInitial.value = {
    leave_type: app.leave_type,
    from_date: app.from_date,
    to_date: app.to_date,
    half_day: app.half_day,
    description: app.description,
  }
  showForm.value = true
}

function onApplied() {
  showForm.value = false
  formInitial.value = null
  // One reload: balances and applications are one response, so a new request
  // cannot leave the field block showing last minute's numbers.
  leave.reload()
}

// --- withdrawal ---------------------------------------------------------

// P2-R27. The browser used to call `frappe.client.delete` directly. Nothing
// but the UI's decision not to draw the button stood between that call and an
// approved application.
const withdrawTarget = ref(null)
const withdrawError = ref('')
const withdraw = createResource({
  url: 'helixhr.api.withdraw_my_leave',
  method: 'POST',
})

function confirmWithdraw(app) {
  withdrawError.value = ''
  withdrawTarget.value = app
}

async function doWithdraw() {
  const app = withdrawTarget.value
  if (!app) return
  withdrawError.value = ''
  try {
    await withdraw.submit({ name: app.name })
    withdrawTarget.value = null
    if (props.name === app.name) closeDetail()
    // Once. Balances and the list are one response.
    leave.reload()
  } catch (error) {
    withdrawError.value = toPlainLeaveError(error)
  }
}
</script>

<template>
  <div>
    <PageHeader title="Leave">
      <template #actions>
        <Button
          variant="solid"
          theme="blue"
          @click="askForLeave"
        >
          Ask for leave
        </Button>
      </template>
    </PageHeader>

    <!-- The anchored region: what you have left, on the field, with a bar per
         type. It is the one place on this page the signal yellow is legal. -->
    <AsyncState
      section="leave-balances"
      class="mb-6"
      :resource="leave"
      :empty="balances.length === 0"
      empty-title="No leave allocated yet"
      empty-body="HR sets your leave allocation each year. Ask HR if you think this is wrong."
      skeleton="field"
      skeleton-height="h-36"
    >
      <section
        class="surface-field elev-2 space-y-3 p-4 lg:flex lg:space-y-0 lg:divide-x lg:divide-white/15"
        aria-label="Leave balances"
      >
        <div
          v-for="entry in balances"
          :key="entry.leave_type"
          class="min-w-0 lg:flex-1 lg:px-4 lg:first:pl-0 lg:last:pr-0"
        >
          <p class="flex items-baseline justify-between gap-3 text-sm">
            <span class="truncate text-white">{{ entry.leave_type }}</span>
            <span class="shrink-0 text-blue-200">
              <span class="tabular font-heading text-base font-bold text-white">
                {{ entry.left }}
              </span>
              of <span class="tabular">{{ entry.allocated }}</span> left
            </span>
          </p>
          <!-- The bar is a second reading of the same number, not the only
               reading of it -- the figure above it is always present, so the
               bar carries no meaning of its own (WCAG 1.4.1). -->
          <div
            class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-field-deep"
            aria-hidden="true"
          >
            <div
              class="h-full rounded-full bg-signal"
              :style="{ width: `${barWidth(entry)}%` }"
            />
          </div>
        </div>
      </section>
    </AsyncState>

    <div class="lg:flex lg:items-start lg:gap-6">
      <!-- The list. On a phone a selected record takes the whole width
           (P2-R6's full-height treatment); at lg: both columns are on
           screen and the list stays put while the panel changes. -->
      <div
        v-show="!name || isDesktop"
        class="min-w-0 lg:flex-1"
      >
        <AsyncState
          section="leave-list"
          :resource="leave"
          :empty="applications.length === 0"
          empty-title="No leave requests yet"
          empty-body="Ask for leave to get started."
          :skeleton-rows="3"
        >
          <template #empty-action>
            <!-- Deliberately not the header's wording: with an empty list both
                 buttons are on screen at once, and two controls with the same
                 name 200px apart is a duplicate, not an affordance. -->
            <Button
              variant="solid"
              theme="blue"
              @click="askForLeave"
            >
              Ask for your first leave
            </Button>
          </template>

          <div
            v-for="group in groups"
            :key="group.key"
            class="mb-6 last:mb-0"
          >
            <h2 class="label mb-2">
              {{ group.label }}
            </h2>
            <ul class="space-y-2">
              <li
                v-for="app in group.rows"
                :key="app.name"
                class="surface-card elev-1 relative flex gap-3 p-3"
                :class="app.name === name ? 'ring-2 ring-field' : ''"
              >
                <span
                  v-if="tile(app)"
                  class="date-tile mt-0.5"
                  aria-hidden="true"
                >
                  <span class="date-tile-month">{{ tile(app).month }}</span>
                  <span class="date-tile-day">{{ tile(app).day }}</span>
                  <span
                    v-if="tile(app).through"
                    class="text-xs text-ink-gray-5"
                  >{{ tile(app).through }}</span>
                </span>

                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-start justify-between gap-2">
                    <!-- One link per row, stretched over the whole card. Two
                         nested interactive elements would be the alternative,
                         and that is neither valid markup nor navigable. -->
                    <router-link
                      class="font-medium text-ink-gray-9 after:absolute after:inset-0 after:content-['']"
                      :to="{ name: 'LeaveDetail', params: { name: app.name } }"
                    >
                      {{ app.leave_type }}
                    </router-link>
                    <StatusBadge
                      kind="leave"
                      :status="badgeStatus(app)"
                      :approver="approverFirstName(app)"
                    />
                  </div>
                  <p class="mt-0.5 text-sm text-ink-gray-6">
                    {{ formatDateRange(app.from_date, app.to_date) }}
                    · <span class="tabular">{{ durationLabel(app) }}</span>
                    <template v-if="app.creation">
                      · sent {{ formatDate(app.creation) }}
                    </template>
                  </p>

                  <!-- The manager's reason, quoted where the decision is,
                       rather than a status word that sends the employee
                       looking for it (P2-R14). -->
                  <div
                    v-if="app.state === 'sent_back' && app.reason"
                    class="surface-alert mt-2 p-3 text-sm"
                  >
                    <span v-if="app.approver_name">{{ app.approver_name }}: </span>“{{ app.reason }}”
                  </div>

                  <div
                    v-if="app.state === 'sent_back'"
                    class="relative z-10 mt-2 flex flex-wrap items-center gap-2"
                  >
                    <Button
                      variant="subtle"
                      @click="editAndResend(app)"
                    >
                      Edit and resend
                    </Button>
                    <Button
                      variant="ghost"
                      @click="confirmWithdraw(app)"
                    >
                      Withdraw
                    </Button>
                  </div>
                  <div
                    v-else-if="app.can_withdraw"
                    class="relative z-10 mt-2"
                  >
                    <Button
                      variant="outline"
                      theme="red"
                      @click="confirmWithdraw(app)"
                    >
                      Withdraw
                    </Button>
                  </div>
                  <p
                    v-else-if="app.state === 'waiting_for_hr'"
                    class="mt-2 text-sm text-ink-gray-5"
                  >
                    HR is sorting this one out. There's nothing for you to do.
                  </p>
                  <p
                    v-else-if="app.state === 'approved'"
                    class="relative z-10 mt-2 text-sm text-ink-gray-5"
                  >
                    Need to cancel this?
                    <router-link
                      class="cursor-pointer text-blue-700 underline underline-offset-2"
                      :to="askHrToCancel(app)"
                    >
                      Ask HR to cancel
                    </router-link>.
                  </p>
                </div>

                <Icon
                  name="chevronRight"
                  class="mt-1 shrink-0 self-start text-ink-gray-4"
                />
              </li>
            </ul>
          </div>

          <div
            v-if="moreCount"
            class="mt-4 text-center"
          >
            <Button
              variant="ghost"
              :loading="leave.loading"
              @click="showMore"
            >
              Show {{ moreCount }} more
            </Button>
          </div>
        </AsyncState>
      </div>

      <!-- The selected leave. Written once and shaped twice: a full-width
           panel with a way back on a phone, a 384px column beside the list at
           lg:. Same URL either way (KTD5), so refresh and Back agree. -->
      <aside
        v-if="name"
        class="min-w-0 lg:w-96 lg:shrink-0"
      >
        <AsyncState
          section="leave-detail"
          :resource="detail"
          :empty="!detail.data"
          empty-title="That leave request isn't here"
          empty-body="It may have been withdrawn."
          skeleton="block"
          skeleton-height="h-64"
        >
          <template #error-title>
            We couldn't load this leave request
          </template>

          <article
            v-if="selected"
            class="surface-card elev-1 p-4"
            aria-label="Leave request"
          >
            <div class="flex flex-wrap items-start justify-between gap-2">
              <h2 class="font-heading text-lg font-bold text-ink-gray-9">
                {{ selected.leave_type }}
              </h2>
              <StatusBadge
                kind="leave"
                :status="badgeStatus(selected)"
                :approver="approverFirstName(selected)"
              />
            </div>

            <dl class="mt-4 grid grid-cols-2 gap-4">
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
                  {{ durationLabel(selected) }}
                  <span v-if="selected.half_day">(half day)</span>
                </dd>
              </div>
              <div>
                <dt class="label">
                  Sent
                </dt>
                <dd class="mt-0.5 text-sm text-ink-gray-9">
                  {{ formatDate(selected.creation) }}
                </dd>
              </div>
            </dl>

            <div
              v-if="selected.description"
              class="mt-4"
            >
              <h3 class="label">
                Reason
              </h3>
              <p class="mt-0.5 text-sm text-ink-gray-7">
                {{ selected.description }}
              </p>
            </div>

            <!-- The manager's reason again, because this is the view an
                 employee lands on from Home and from the notification
                 (P2-U5 scenario 1). -->
            <div
              v-if="selected.state === 'sent_back' && selected.reason"
              class="surface-alert mt-4 p-3 text-sm"
            >
              <span v-if="selected.approver_name">{{ selected.approver_name }}: </span>“{{
                selected.reason
              }}”
            </div>

            <p
              v-else-if="selected.state === 'open' && selected.approver_name"
              class="surface-inset mt-4 p-3 text-sm text-ink-gray-7"
            >
              Waiting on <span class="font-medium">{{ selected.approver_name }}</span>
              <template v-if="selected.creation">
                since {{ formatDate(selected.creation) }}
              </template>.
            </p>

            <div class="mt-4 border-t border-outline-gray-1 pt-4">
              <div
                v-if="selected.state === 'sent_back'"
                class="flex flex-wrap items-center gap-2"
              >
                <Button
                  variant="solid"
                  theme="blue"
                  @click="editAndResend(selected)"
                >
                  Edit and resend
                </Button>
                <Button
                  variant="subtle"
                  @click="confirmWithdraw(selected)"
                >
                  Withdraw
                </Button>
              </div>
              <div
                v-else-if="selected.can_withdraw"
                class="flex flex-wrap items-center gap-3"
              >
                <Button
                  variant="outline"
                  theme="red"
                  @click="confirmWithdraw(selected)"
                >
                  Withdraw
                </Button>
                <p
                  v-if="selected.approver_name"
                  class="text-sm text-ink-gray-5"
                >
                  You can withdraw until {{ approverFirstName(selected) }} decides.
                </p>
              </div>
              <!-- P2-U1 step 4. Unsubmitted but marked Approved: it never
                   consumed balance, HR resolves it in Desk, and there is
                   nothing here the employee can or should do. -->
              <p
                v-else-if="selected.state === 'waiting_for_hr'"
                class="text-sm text-ink-gray-5"
              >
                HR is sorting this one out. There's nothing for you to do.
              </p>
              <p
                v-else-if="selected.state === 'approved'"
                class="text-sm text-ink-gray-5"
              >
                This leave is approved and counted against your balance.
                <router-link
                  class="cursor-pointer text-blue-700 underline underline-offset-2"
                  :to="askHrToCancel(selected)"
                >
                  Ask HR to cancel
                </router-link>
                if your plans changed.
              </p>

              <Button
                class="mt-3"
                variant="ghost"
                @click="closeDetail"
              >
                Back to leave
              </Button>
            </div>
          </article>
        </AsyncState>
      </aside>
    </div>

    <Dialog
      v-model="showForm"
      :options="{ title: 'Ask for leave' }"
    >
      <template #body-content>
        <LeaveForm
          :initial="formInitial"
          @applied="onApplied"
          @cancel="showForm = false"
        />
      </template>
    </Dialog>

    <!-- P2-U5 scenario 5: withdrawal is confirmed, never a single tap. -->
    <Dialog
      :model-value="!!withdrawTarget"
      :options="{ title: 'Withdraw this leave request?', size: 'sm' }"
      @update:model-value="(open) => !open && (withdrawTarget = null)"
    >
      <template #body-content>
        <p
          v-if="withdrawTarget"
          class="text-sm text-ink-gray-7"
        >
          {{ withdrawTarget.leave_type }},
          {{ formatDateRange(withdrawTarget.from_date, withdrawTarget.to_date) }}. This removes the
          request. You can always ask again.
        </p>
        <p
          v-if="withdrawError"
          class="mt-3 text-sm text-ink-red-4"
          role="alert"
        >
          {{ withdrawError }}
        </p>
        <div class="mt-4 flex items-center gap-2">
          <Button
            variant="solid"
            theme="red"
            :loading="withdraw.loading"
            @click="doWithdraw"
          >
            Withdraw
          </Button>
          <Button
            variant="subtle"
            @click="withdrawTarget = null"
          >
            Keep it
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
