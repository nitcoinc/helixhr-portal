<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, FormControl } from 'frappe-ui'
import SegmentedChips from '@/components/SegmentedChips.vue'
import SearchField from '@/components/SearchField.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import KindTag from '@/components/KindTag.vue'
import CorrectionReview from '@/components/approvals/CorrectionReview.vue'
import RequestThread from '@/components/requests/RequestThread.vue'
import AttachmentLink from '@/components/requests/AttachmentLink.vue'
import Icon from '@/components/Icon.vue'
import { attachToRequestReply } from '@/lib/api'
import { formatDate } from '@/lib/dates'
import { focusNear } from '@/lib/focus'
import { useIsDesktop } from '@/lib/useIsDesktop'
import { CORRECTABLE_FIELDS, spokenValue } from '@/lib/profileCorrection'
import { notify } from '@/lib/notify'
import { slaOverLabel } from '@/lib/requests'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'
import { getReasonCopy } from '@/lib/workKinds'

// Plan 2026-10-17-001 U8 (R21, KTD14). The handler's workspace -- the Team
// inbox and the request being worked on -- moved out of `pages/Requests.vue`
// with its behaviour unchanged. The page keeps the header and the tab bar;
// this tells it the inbox's title and badge count.
const props = defineProps({
  name: { type: String, default: '' },
})
const emit = defineEmits(['summary'])

const route = useRoute()
const router = useRouter()

// --- Plan 2026-10-04-002 U4: "To work on" --------------------------------
//
// R6: for a holder of a routed role the page gains a second tab listing what
// is routed to them; a user with neither role gets no tab bar at all. R9: the
// tab and its filter live in the URL, so a notification link reproduces the
// view. R8: acting on a row calls the same `act_on_approval` the Approvals
// queue uses -- the buttons are drawn from the detail's own `actions` (the
// workflow's own transitions), never from a second rule.

const WORK_FILTERS = [
  { value: 'open', label: 'Open' },
  { value: 'mine', label: 'Mine' },
  { value: 'closed', label: 'Closed' },
]

const workFilter = computed(() => {
  if (route.query.state === 'waiting') return 'open'
  return WORK_FILTERS.some((filter) => filter.value === route.query.state) ? route.query.state : 'open'
})

watch(
  () => route.query.state,
  async (state) => {
    if (state === 'waiting') {
      await nextTick()
      const el = document.getElementById('waiting-group')
      if (el) el.scrollIntoView({ behavior: 'smooth' })
    }
  },
  { immediate: true },
)

function filterWork(state) {
  router.push({
    name: route.name,
    params: route.params,
    query: { tab: 'work', state: state === 'open' ? undefined : state },
  })
}

// Plan 2026-10-17-001 U8 (R13; AI-04, HR-05). Search, category and the SLA
// order are server questions -- the feed is paged, so narrowing the rows
// already here would narrow one page. They stay in this component's state:
// the list and the detail are one instance, so they survive opening a row.
// The inbox asks for most-overdue first; the API's default stays oldest
// first for every other caller.
const query = ref('')
const search = ref('')
const category = ref('')
const workLimit = ref(20)
const work = createResource({
  url: 'helixhr.api.get_request_work',
  makeParams: () => ({
    state: workFilter.value,
    limit: workLimit.value,
    start: 0,
    sort: 'sla',
    q: search.value || undefined,
    category: category.value || undefined,
  }),
})
// The server ignores a query under 2 characters (_REQUEST_SEARCH_MIN),
// so a shorter one asks the same question as none, and an unchanged one is
// not asked again.
const debounced = useDebouncedSearch((value) => {
  const next = value.trim().length >= 2 ? value.trim() : ''
  if (next === search.value) return
  search.value = next
  workLimit.value = 20
  work.fetch()
})
watch(query, debounced.search)

// Chips only when the caller's roles route more than one category here.
const categoryOptions = computed(() => [
  { value: '', label: 'All' },
  ...(work.data?.categories || []).map((name) => ({ value: name, label: name })),
])
function filterCategory(value) {
  category.value = value || ''
  workLimit.value = 20
  work.fetch()
}
watch(
  workFilter,
  () => {
    workLimit.value = 20
    work.fetch()
  },
  { immediate: true },
)
const workRows = computed(() => work.data?.work || [])
// The server names the team from the roles it read (HR or IT); both -> plain.
const inboxTitle = computed(() => (work.data?.team ? `${work.data.team} team inbox` : 'Team inbox'))
const workTotal = computed(() => work.data?.total || 0)
const workNeedsActionCount = computed(() =>
  work.data?.action_count !== undefined
    ? work.data.action_count
    : workRows.value.filter((r) => r.status === 'Open' || r.status === 'In Progress').length,
)
// The page draws the title and the tab's count; the numbers live here.
watch(
  [inboxTitle, workNeedsActionCount],
  ([title, count]) => emit('summary', { title, count }),
  { immediate: true },
)
/** R6: "Waiting on Ravi · 3 days" -- the badge names the person, this the time. */
function waitedFor(days) {
  if (!days) return 'since today'
  return days === 1 ? '1 day' : `${days} days`
}
const workMoreCount = computed(() => Math.max(0, workTotal.value - workRows.value.length))
// AI-17: a kind tag that is the same on every row says nothing.
const mixedKinds = computed(() => new Set(workRows.value.map((row) => row.routed_to_role)).size > 1)

const workGroups = computed(() => {
  if (workFilter.value === 'closed') {
    return [{ key: 'closed', title: 'Closed', rows: workRows.value }]
  }
  const newRows = workRows.value.filter((r) => r.status === 'Open')
  const inProgressRows = workRows.value.filter((r) => r.status === 'In Progress')
  const waitingRows = workRows.value.filter((r) => r.status === 'Waiting on Employee')

  const list = []
  if (newRows.length) {
    list.push({ key: 'new', title: 'New', count: newRows.length, rows: newRows })
  }
  if (inProgressRows.length) {
    list.push({ key: 'in_progress', title: 'In progress', count: inProgressRows.length, rows: inProgressRows })
  }
  if (waitingRows.length) {
    list.push({ key: 'waiting', title: 'Waiting on employee', count: waitingRows.length, rows: waitingRows })
  }
  if (!list.length && workRows.value.length) {
    list.push({ key: 'all', title: 'Requests', count: workRows.value.length, rows: workRows.value })
  }
  return list
})

function showMoreWork() {
  workLimit.value = Math.min(100, workLimit.value + 20)
  work.fetch()
}

// The feed's detail is the same decision detail the Approvals queue loads
// (`get_approval_detail`), so the evidence, the thread and the legal actions
// are the server's one answer -- R8's "not a second implementation".
const workDetail = createResource({
  url: 'helixhr.api.get_approval_detail',
  makeParams: () => ({ kind: 'request', name: props.name }),
})
const workAct = createResource({ url: 'helixhr.api.act_on_approval', method: 'POST' })
const workSelected = computed(() => (props.name ? workDetail.data : null))

const workActing = ref('')
const reasonFor = ref('') // 'Reject' while its box is open
const reasonMessage = ref('')
const reasonError = ref('')
// The server's action is Reject; the word on screen is Decline (R14).
const declineCopy = getReasonCopy('Decline')

const attachingReply = ref(false)
const attachReplyError = ref('')
const requestFileInput = ref(null)

async function attachReplyFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !workSelected.value) return
  attachingReply.value = true
  attachReplyError.value = ''
  try {
    await attachToRequestReply(file, { name: workSelected.value.name })
    workDetail.fetch()
  } catch (error) {
    attachReplyError.value = error?.messages?.[0] || 'That file did not upload.'
  } finally {
    attachingReply.value = false
  }
}

function openReason(action) {
  reasonFor.value = action
  reasonMessage.value = ''
  reasonError.value = ''
}

async function decide(action) {
  const item = workSelected.value
  if (!item || workActing.value) return
  const wantsReason = action === 'Reject'
  const message = wantsReason ? reasonMessage.value.trim() : ''
  if (wantsReason && !message) {
    reasonError.value = declineCopy.missing
    return
  }
  workActing.value = item.name
  reasonError.value = ''
  try {
    await workAct.submit({
      doctype: 'HR Request',
      name: item.name,
      action,
      comment: message || undefined,
      expected_modified: item.modified,
      expected_state: item.state,
    })
    reasonFor.value = ''
    reasonMessage.value = ''
    workDetail.fetch()
    work.fetch()
  } catch (error) {
    reasonError.value =
      error?.messages?.[0] || "That didn't go through. Reload and try again."
  } finally {
    workActing.value = ''
  }
}

function confirmReason() {
  decide(reasonFor.value)
}

const may = (action) => workSelected.value?.actions?.includes(action)

// --- Assign to me from the list (U8 / AI-04, HR-05) ---------------------
//
// The row's own `actions` (the same transitions the detail's buttons come
// from) decide whether the button is drawn, and its `token_modified` is the
// stale-write check, so a request somebody else took a moment ago is
// refused with the server's own sentence and the list refreshes.
const pickingUp = ref('')
const workList = ref(null)

async function pickUp(row) {
  if (pickingUp.value) return
  pickingUp.value = row.name
  const group = workGroups.value.find((entry) => entry.rows.includes(row))
  const index = group ? group.rows.indexOf(row) : 0
  try {
    await workAct.submit({
      doctype: 'HR Request',
      name: row.name,
      action: 'Pick up',
      expected_modified: row.token_modified,
      expected_state: row.status,
    })
    notify.success(`Assigned to you: ${row.subject}`)
    if (props.name === row.name) workDetail.fetch()
    await work.fetch()
    focusAfter(group?.key, index)
  } catch (error) {
    notify.error(error?.messages?.[0] || "That didn't go through. Reload and try again.")
    await work.fetch()
  } finally {
    pickingUp.value = ''
  }
}

/** KTD16: the row left its group, so focus moves to the row that took its
 * place (or the one before it, at the end); when the group emptied, to the
 * first group heading. */
async function focusAfter(groupKey, index) {
  await nextTick()
  const root = workList.value
  if (!root) return
  const rows = [...root.querySelectorAll(`[data-group="${groupKey}"] [data-testid="work-row"]`)]
  focusNear(
    rows.map((row) => row.querySelector('[data-testid="row-pick-up"]') || row.querySelector('a')),
    index,
    root.querySelector('[data-group] h3'),
  )
}

watch(
  () => props.name,
  (name) => {
    if (name) workDetail.fetch()
  },
  { immediate: true },
)

function closeDetail() {
  // Back to the tab the detail was opened from (R9), keeping its filter.
  router.push({
    name: 'Requests',
    query: { tab: 'work', ...(workFilter.value === 'open' ? {} : { state: workFilter.value }) },
  })
}

// --- one URL, two shapes ------------------------------------------------

const isDesktop = useIsDesktop()

const workTimeline = computed(() => {
  const request = workSelected.value
  if (!request) return []
  const list = [{ key: 'sent', label: 'Sent', at: request.creation || request.sent_on }]
  if (request.picked_up_on) {
    list.push({
      key: 'picked-up',
      label: `Assigned to ${request.picked_up_by_name || request.handled_by_team || 'team'}`,
      at: request.picked_up_on,
    })
  }
  if (request.closed_on) {
    list.push({
      key: 'closed',
      label: 'Closed',
      at: request.closed_on,
    })
  }
  return list
})
</script>

<template>
  <div class="lg:flex lg:items-start lg:gap-6">
    <!-- The list. On a phone a selected record takes the whole width
         (P2-R6's full-height treatment); at lg: both columns are on screen
         and the list stays put while the panel changes. -->
    <div
      v-show="!name || isDesktop"
      class="min-w-0 lg:flex-1"
    >
      <!-- U8 (R13): search the inbox by subject or requester. -->
      <SearchField
        v-model="query"
        class="mb-4 sm:max-w-sm"
        label="Search the inbox"
        placeholder="Subject or requester"
        maxlength="60"
        data-testid="work-search"
      />
      <!-- R7: the feed's state chips, off the same server filter the
           tab read. "Closed" is a deliberate click away, not a default. -->
      <SegmentedChips
        class="mb-4"
        label="Filter work by state"
        data-testid="work-filter-chips"
        :options="WORK_FILTERS"
        :model-value="workFilter"
        @update:model-value="filterWork"
      />
      <SegmentedChips
        v-if="categoryOptions.length > 2"
        class="mb-4"
        label="Filter work by category"
        data-testid="work-category-chips"
        :options="categoryOptions"
        :model-value="category"
        @update:model-value="filterCategory"
      />
      <!-- A refresh keeps the rows on screen: a search, Show more or an
           "Assign to me" redraws the list in place (KTD16) rather than
           swapping it for a skeleton and losing focus. -->
      <AsyncState
        section="work-list"
        :resource="work"
        :loading="work.loading && !work.data"
        :empty="workRows.length === 0"
        :empty-title="search || category ? 'No requests match' : 'Nothing to work on'"
        :empty-body="
          search || category
            ? 'Try another word, another category, or clear the search.'
            : 'Requests routed to you appear here as they arrive.'
        "
        :skeleton-rows="3"
      >
        <div
          ref="workList"
          class="space-y-6"
        >
          <section
            v-for="group in workGroups"
            :id="group.key === 'waiting' ? 'waiting-group' : undefined"
            :key="group.key"
            :data-group="group.key"
            class="space-y-2"
          >
            <div class="flex items-center justify-between">
              <h3
                class="label text-ink-gray-7"
                tabindex="-1"
              >
                {{ group.title }}
              </h3>
              <span
                v-if="group.count !== undefined"
                class="tabular text-xs text-ink-gray-5"
              >{{ group.count }}</span>
            </div>
            <ul class="space-y-2">
              <li
                v-for="row in group.rows"
                :key="row.name"
                class="surface-card elev-1 relative p-4"
                :class="row.name === name ? 'ring-2 ring-field' : ''"
                data-testid="work-row"
              >
                <!-- Below sm the badges take their own line under the
                     subject, so the title and the requester keep the full
                     width (HR-02, AI-01). -->
                <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                  <div class="min-w-0">
                    <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                      <KindTag
                        v-if="mixedKinds"
                        :row="row"
                      />
                      <!-- A person's name, not a group label (AI-19). -->
                      <p
                        class="min-w-0 break-words text-sm text-ink-gray-6"
                        data-testid="work-requester"
                      >
                        {{ row.employee_name }}
                      </p>
                    </div>
                    <router-link
                      class="break-words font-medium text-ink-gray-9 after:absolute after:inset-0 after:content-['']"
                      :class="row.name === name ? 'font-semibold' : ''"
                      :to="{ name: 'RequestDetail', params: { name: row.name }, query: { tab: 'work', ...(workFilter === 'open' ? {} : { state: workFilter }) } }"
                    >
                      {{ row.subject }}
                    </router-link>
                    <p class="mt-0.5 text-sm text-ink-gray-5">
                      {{ row.category }} · sent {{ formatDate(row.sent_on) }}
                      <template v-if="row.picked_up_by_name">
                        · picked up by {{ row.picked_up_by_name }}
                      </template>
                      <template v-if="row.waiting_since">
                        · waiting {{ waitedFor(row.waiting_days) }}
                      </template>
                    </p>
                  </div>
                  <div class="flex flex-wrap items-center gap-2 sm:shrink-0 sm:flex-nowrap sm:justify-end">
                    <span
                      v-if="row.sla_overdue"
                      class="whitespace-nowrap rounded-full bg-surface-red-2 px-2 py-0.5 text-xs font-bold text-ink-red-3"
                      data-testid="work-sla"
                    ><span class="sr-only">Past SLA: </span>{{ slaOverLabel(row.sla_over_days) }}</span>
                    <StatusBadge
                      kind="request"
                      :status="row.status"
                      audience="handler"
                      :waiting-on="row.waiting_on"
                      :assignee="row.picked_up_by_name"
                    />
                    <!-- Above the stretched link, so it is its own target. -->
                    <Button
                      v-if="row.actions?.includes('Pick up')"
                      class="relative z-10"
                      variant="subtle"
                      :loading="pickingUp === row.name"
                      :disabled="!!pickingUp"
                      data-testid="row-pick-up"
                      @click="pickUp(row)"
                    >
                      Assign to me<span class="sr-only">: {{ row.subject }}</span>
                    </Button>
                    <Icon
                      name="chevronRight"
                      size="h-4 w-4"
                      class="hidden text-ink-gray-4 sm:block"
                    />
                  </div>
                </div>
              </li>
            </ul>
          </section>
        </div>
        <div
          v-if="workMoreCount"
          class="mt-4 text-center"
        >
          <Button
            variant="ghost"
            :loading="work.loading"
            @click="showMoreWork"
          >
            Show {{ workMoreCount }} more
          </Button>
        </div>
      </AsyncState>
    </div>

    <!-- The selected request. Written once and shaped twice: a full-width
         panel with a way back on a phone, a 384px column beside the list at
         lg:. Same URL either way (KTD5). -->
    <aside
      v-if="name"
      class="min-w-0 lg:w-96 lg:shrink-0"
    >
      <div
        v-show="!isDesktop"
        class="mb-3"
      >
        <Button
          variant="ghost"
          @click="closeDetail"
        >
          <template #prefix>
            <Icon
              name="chevronLeft"
              size="h-4 w-4"
            />
          </template>
          Requests
        </Button>
      </div>

      <AsyncState
        section="request-detail"
        :resource="workDetail"
        :empty="!workDetail.data"
        empty-title="That request isn't here"
        empty-body="It may have been closed by somebody else."
        skeleton="block"
        skeleton-height="h-64"
      >
        <template #error-title>
          We couldn't load this request
        </template>

        <!-- R8: the same decision detail the Approvals queue opens -- the
             evidence, the conversation and the legal actions, with the
             server's permission check behind every button. -->
        <article
          v-if="workSelected"
          class="surface-card elev-1 p-4"
          aria-label="Request to work on"
          data-testid="work-detail"
        >
          <div class="flex items-center gap-2">
            <KindTag :row="workSelected" />
            <p class="label">
              {{ workSelected.category }} · {{ workSelected.name }}
            </p>
          </div>
          <div class="mt-1 flex flex-wrap items-start justify-between gap-2">
            <h2 class="type-section font-heading text-ink-gray-9">
              {{ workSelected.subject }}
            </h2>
            <StatusBadge
              kind="request"
              :status="workSelected.status"
              audience="handler"
              :waiting-on="workSelected.waiting_on || ''"
              :assignee="workSelected.picked_up_by_name"
            />
          </div>
          <p class="mt-1 text-sm text-ink-gray-6">
            {{ workSelected.employee_name }}
            <template v-if="workSelected.sent_on">
              · sent {{ formatDate(workSelected.sent_on) }}
            </template>
            <template v-if="workSelected.age_days != null">
              · waiting {{ workSelected.age_days }} day{{ workSelected.age_days === 1 ? '' : 's' }}
            </template>
            <template v-if="workSelected.picked_up_by_name">
              · picked up by {{ workSelected.picked_up_by_name }}
            </template>
          </p>
          <p
            v-if="workSelected.sla_overdue"
            class="mt-1 text-sm font-medium text-ink-amber-3"
            data-testid="work-sla-overdue"
          >
            Past this category's SLA
          </p>

          <!-- A structured correction's masked proposal and review -->
          <CorrectionReview
            v-if="workSelected.correction"
            :key="workSelected.name"
            :request="workSelected.name"
            :correction="workSelected.correction"
            :attachments="workSelected.attachments"
            :can-reveal="!!workSelected.actions?.length"
          />
          <p
            v-else-if="workSelected.correction_field"
            class="mt-2 text-sm text-ink-gray-7"
            data-testid="request-correction"
          >
            New {{ CORRECTABLE_FIELDS[workSelected.correction_field] || workSelected.correction_field }}:
            <span
              class="tabular text-ink-gray-9"
              :aria-label="spokenValue(workSelected.correction_proposed_masked || '')"
            >{{ workSelected.correction_proposed_masked }}</span>
          </p>

          <ul
            v-if="workSelected.attachments.length"
            class="mt-3 flex flex-wrap gap-2"
          >
            <li
              v-for="attachment in workSelected.attachments"
              :key="attachment.name"
            >
              <AttachmentLink :attachment="attachment" />
            </li>
          </ul>

          <div
            v-if="workSelected.actions?.length && !workSelected.closed"
            class="mt-3"
          >
            <Button
              variant="outline"
              :loading="attachingReply"
              :disabled="attachingReply"
              @click="requestFileInput?.click()"
            >
              Attach a file
            </Button>
            <input
              ref="requestFileInput"
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

          <!-- Plan 2026-10-15-003 KTD8: the shared thread; the handler's
               box shows only when `comment_on_request` would accept it. -->
          <RequestThread
            class="mt-4 border-t border-outline-gray-1 pt-4"
            :entries="workSelected.thread || []"
            :can-comment="!!workSelected.can_comment"
            :closed="!!workSelected.closed"
            :request-name="workSelected.name"
            :employee-name="workSelected.employee_name || ''"
            :expected-modified="workSelected.modified"
            :expected-state="workSelected.state || workSelected.status"
            :can-need-info="may('Need info')"
            :assigns-on-wait="workSelected.status === 'Open'"
            :milestones="workTimeline"
            mode="handler"
            @posted="workDetail.fetch(); work.fetch()"
            @refetch="workDetail.fetch()"
          />

          <!-- Decline refuses to submit without a reason (P5-U6). Need info
               is the message box's "Send and wait for reply" (R9). -->
          <div
            v-if="reasonFor"
            class="surface-inset mt-4 p-3"
            data-testid="work-reason"
          >
            <FormControl
              v-model="reasonMessage"
              type="textarea"
              :label="declineCopy.heading"
              :placeholder="declineCopy.placeholder"
            />
            <p
              v-if="reasonError"
              class="mt-1 text-sm font-medium text-ink-red-4"
              role="alert"
            >
              {{ reasonError }}
            </p>
            <div class="mt-2 flex gap-2">
              <Button
                variant="solid"
                :theme="reasonFor === 'Reject' ? 'red' : 'green'"
                :loading="workActing === workSelected.name"
                data-testid="work-reason-confirm"
                @click="confirmReason"
              >
                Decline
              </Button>
              <Button
                variant="ghost"
                @click="reasonFor = ''"
              >
                Cancel
              </Button>
            </div>
          </div>

          <div
            v-if="workSelected.actions?.length"
            class="mt-4 flex flex-wrap items-center gap-2 border-t border-outline-gray-1 pt-4"
            data-testid="decision-actions"
          >
            <Button
              v-if="may('Pick up')"
              variant="solid"
              theme="green"
              :loading="workActing === workSelected.name"
              :disabled="workActing === workSelected.name"
              data-testid="pick-up"
              @click="decide('Pick up')"
            >
              Assign to me
            </Button>
            <Button
              v-if="may('Done')"
              variant="solid"
              theme="green"
              :loading="workActing === workSelected.name"
              :disabled="workActing === workSelected.name"
              data-testid="done"
              @click="decide('Done')"
            >
              Mark done
            </Button>
            <Button
              v-if="may('Reject')"
              variant="outline"
              class="decision-tone decision-tone--final"
              :disabled="workActing === workSelected.name"
              data-testid="reject"
              @click="openReason('Reject')"
            >
              Decline
            </Button>
          </div>
        </article>
      </AsyncState>
    </aside>
  </div>
</template>
