<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import RequestForm from '@/components/RequestForm.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import SearchField from '@/components/SearchField.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import RequestThread from '@/components/requests/RequestThread.vue'
import Icon from '@/components/Icon.vue'
import { attachToRequest, keepaliveRequest } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/dates'
import { currentUnread, setUnread, unreadCount } from '@/lib/unread'
import { useIsDesktop } from '@/lib/useIsDesktop'
import { CORRECTABLE_FIELDS, spokenValue } from '@/lib/profileCorrection'
import { fileSize, myRequestGroups } from '@/lib/requests'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'

// Plan 2026-10-17-001 U8 (R21, KTD14). The employee's workspace -- their own
// requests, the one they opened, and the new-request sheet -- moved out of
// `pages/Requests.vue` with its behaviour unchanged. The page's "New
// request" button calls the exposed `newRequest`.
const props = defineProps({
  name: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

// P2-R22. A bounded first page with an explicit Show more, and one
// session-scoped read where the page used to send its own
// `frappe.client.get_list` with `limit_page_length: 0`.
const pageLimit = ref(20)
// U6 / R13. The category chip is in the URL query so a link reproduces the
// view. `type`, not `category`: `?category=` already prefills the new-request
// form (Profile's correction link), and one key cannot mean both.
const categoryFilter = computed(() => route.query.type || '')
// U8 / ED-19: a subject search, asked of the server -- the list is paged,
// so filtering the rows already here would search one page.
const query = ref('')
const search = ref('')
const requests = createResource({
  url: 'helixhr.api.get_my_requests',
  makeParams: () => ({
    limit: pageLimit.value,
    category: categoryFilter.value || undefined,
    q: search.value || undefined,
  }),
  auto: true,
})
const debounced = useDebouncedSearch((value) => {
  search.value = value.trim()
  pageLimit.value = 20
  requests.reload()
})
watch(query, debounced.search)
watch(categoryFilter, () => {
  pageLimit.value = 20
  requests.reload()
})
const categoryChips = computed(() => requests.data?.counts?.categories || [])
const categoryOptions = computed(() => [
  { value: '', label: 'All' },
  ...categoryChips.value.map((chip) => ({ value: chip.name, label: chip.name, count: chip.count })),
])

function filterByCategory(category) {
  router.push({ name: route.name, params: route.params, query: { ...route.query, type: category || undefined } })
}

const rows = computed(() => requests.data?.requests || [])
const total = computed(() => requests.data?.total || 0)
const moreCount = computed(() => Math.max(0, total.value - rows.value.length))

function showMore() {
  pageLimit.value = Math.min(100, pageLimit.value + 20)
  requests.reload()
}

// The search shows once there is something to search (every request the
// employee has, whatever the chip or the search says).
const allCount = computed(() => categoryChips.value.reduce((sum, chip) => sum + chip.count, 0))

// Needs you / Open / Closed. Plan 2026-10-17-001 KTD12 (R19): "Needs you" is
// what the employee owes -- the request waits on them, or holds an unread
// message from the team -- the same rule Home applies. An unread status
// change (Done, Declined) is an update: it keeps its unread dot in its own
// Open or Closed group (ED-01, EP-04). The read state is still Frappe's own
// Notification Log (P2-KTD6); a message you have read stops needing you.
const groups = computed(() => myRequestGroups(rows.value))

/** The row's second line: when it was sent, and the most recent thing that
 * has happened to it since. Only steps the record actually knows are
 * printed -- a request made before the portal started stamping them simply
 * says "Sent …". */
function meta(row) {
  const parts = [`Sent ${formatDate(row.creation)}`]
  if (row.closed_on) parts.push(`closed ${formatDate(row.closed_on)}`)
  else if (row.picked_up_on) {
    // Plan 2026-10-04-002 R4: the picker is named; a team-resolved request
    // without a resolvable picker falls back to the team label.
    parts.push(`picked up ${row.picked_up_by_name ? `by ${row.picked_up_by_name} ` : ''}${formatDate(row.picked_up_on)}`)
  }
  if (row.attachments) {
    parts.push(`${row.attachments} attachment${row.attachments === 1 ? '' : 's'}`)
  }
  return parts.join(' · ')
}

// --- the selected record ------------------------------------------------

// The list is bounded, so `/requests/<name>` has to be answerable on its own:
// a request reached from a notification or a bookmark is not necessarily on
// the page the list returned.
const detail = createResource({
  url: 'helixhr.api.get_my_request',
  makeParams: () => ({ name: props.name }),
  onSuccess: (data) => clearReadObligation(data),
})

const selected = computed(() => (props.name ? detail.data : null))

watch(
  () => props.name,
  (name) => {
    if (name) detail.fetch()
  },
  { immediate: true },
)

function reloadRequest() {
  detail.fetch()
  requests.reload()
}

function closeDetail() {
  // Back to the list the detail was opened from (R9), keeping its filter.
  router.push({ name: 'Requests', query: categoryFilter.value ? { type: categoryFilter.value } : {} })
}

// --- clearing the obligation --------------------------------------------

const justMarkedRead = ref(false)

/**
 * P2-U8 step 5 / P2-R13. Opening the request *is* reading the reply, so the
 * notification that sent you here stops asking. The shell's badge moves in
 * the same interaction rather than at the next poll, and the list row leaves
 * "Needs you" without a second round trip to find that out.
 */
async function clearReadObligation(data) {
  justMarkedRead.value = false
  const pending = data?.unread_notifications || []
  if (!pending.length) return
  justMarkedRead.value = true
  setUnread(currentUnread() - pending.length)
  const row = rows.value.find((entry) => entry.name === data.name)
  if (row) {
    row.unread = false
    row.unread_reply = false
  }
  try {
    const result = await keepaliveRequest('helixhr.api.mark_my_request_read', { name: data.name })
    setUnread(result.unread)
  } catch {
    unreadCount.reload()
  }
}

// --- the request that was sent, and the file that wasn't ----------------

// A create that committed while its upload failed leaves one thing behind
// that no server can hold for us: the bytes. They stay here, keyed by the
// request they belong to, until the employee retries or leaves the page.
const pendingUpload = ref(null)
const uploadError = ref('')
const retrying = ref(false)

// Set when the create was answered from an earlier attempt's operation key
// (P2-AE7). The request on screen is the one that went through, and whatever
// was edited between the two presses of Send is not on it -- which the page
// has to say, because "we reused your earlier request" and "we made the
// request you just typed" otherwise render identically.
const reusedExisting = ref('')

const failedUpload = computed(() =>
  pendingUpload.value && pendingUpload.value.name === props.name ? pendingUpload.value : null,
)

async function retryUpload() {
  const pending = failedUpload.value
  if (!pending || retrying.value) return
  retrying.value = true
  uploadError.value = ''
  try {
    await attachToRequest(pending.file, { name: pending.name })
    pendingUpload.value = null
    detail.fetch()
    requests.reload()
  } catch (error) {
    uploadError.value = error?.messages?.[0] || 'The file still didn’t upload.'
  } finally {
    retrying.value = false
  }
}


// --- asking ------------------------------------------------------------

const showForm = ref(!!route.query.subject || !!route.query.category)

function newRequest() {
  showForm.value = true
}

/**
 * The sheet is finished either way: the request exists. When its file
 * failed, the page keeps the bytes and opens the request so the truth --
 * "sent, the file didn't" -- is said on the record it is about, next to a
 * Retry upload that targets that same request (P2-R18, P2-AE7).
 */
function onCreated({ name, created, uploadError: failure, pendingFile }) {
  showForm.value = false
  reusedExisting.value = created ? '' : name
  uploadError.value = failure || ''
  pendingUpload.value = failure && pendingFile ? { name, file: pendingFile } : null
  requests.reload()
  router.push({ name: 'RequestDetail', params: { name } })
}

// --- one URL, two shapes ------------------------------------------------

const isDesktop = useIsDesktop()

// The timeline. Three steps, and only the ones that happened are drawn --
// a step with no date is a step the record cannot vouch for.
const timeline = computed(() => {
  const request = selected.value
  if (!request) return []
  const list = [{ key: 'sent', label: 'Sent', at: request.creation }]
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

const latestQuestion = computed(() => {
  const q = (selected.value?.thread || []).slice().reverse().find((e) => e.kind === 'question')
  return q ? q.message : ''
})

defineExpose({ newRequest })
</script>

<template>
  <div>
    <div class="lg:flex lg:items-start lg:gap-6">
      <div
        v-show="!name || isDesktop"
        class="min-w-0 lg:flex-1"
      >
        <!-- U6 / R13. Category chips, counted over your own requests only;
           an inactive category you used before still has one. -->
        <SegmentedChips
          v-if="categoryChips.length > 1 || categoryFilter"
          class="mb-4"
          label="Filter requests by category"
          data-testid="request-category-chips"
          :options="categoryOptions"
          :model-value="categoryFilter"
          @update:model-value="filterByCategory"
        />
        <SearchField
          v-if="allCount || query"
          v-model="query"
          class="mb-4 sm:max-w-sm"
          label="Search your requests"
          placeholder="Part of a subject"
          maxlength="60"
          data-testid="request-search"
        />
        <AsyncState
          section="requests-list"
          :resource="requests"
          :empty="rows.length === 0"
          :empty-title="search ? 'No requests match' : 'No requests yet'"
          :empty-body="
            search
              ? 'Try another word from the subject, or clear the search.'
              : 'Ask HR for a letter, a payroll correction, or anything else you need.'
          "
          :skeleton-rows="3"
        >
          <template #empty-action>
            <!-- Not the header's wording: with an empty list both are on
               screen at once, and two controls with the same accessible
               name is a duplicate rather than an affordance. -->
            <Button
              v-if="!search"
              variant="solid"
              theme="blue"
              @click="newRequest"
            >
              Send your first request
            </Button>
          </template>

          <section
            v-for="group in groups"
            :key="group.key"
            class="mb-6 last:mb-0"
            :aria-label="group.label"
          >
            <h2 class="label mb-2">
              {{ group.label }}
            </h2>
            <ul class="space-y-2">
              <li
                v-for="row in group.rows"
                :key="row.name"
                class="surface-card elev-1 relative p-4"
                :class="row.name === name ? 'ring-2 ring-field' : ''"
                data-testid="request-row"
                :data-unread="row.unread ? '1' : '0'"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <p class="label flex items-center gap-2">
                      <!-- Tone plus a caption, never hue alone: the dot is
                         named for a screen reader. An unread update that
                         asks nothing of you keeps its own group (KTD12). -->
                      <span
                        v-if="row.unread"
                        class="inline-block h-2 w-2 shrink-0 rounded-full bg-field"
                        data-testid="unread-dot"
                      ><span class="sr-only">Unread</span></span>
                      {{ row.category }}
                    </p>
                    <!-- One link per row, stretched over the whole card. Two
                       nested interactive elements would be the alternative,
                       and that is neither valid markup nor navigable. -->
                    <!-- A subject wraps, so this link stays inline: the 44px
                       target is the stretched pseudo-element over the whole
                       card. `-my-2 inline-flex min-h-11` is for single-line
                       links only -- on two lines its negative margin pulled
                       the meta line up into the title (EP-01). -->
                    <router-link
                      class="break-words font-medium text-ink-gray-9 after:absolute after:inset-0 after:content-['']"
                      :class="row.unread ? 'font-semibold' : ''"
                      :to="{ name: 'RequestDetail', params: { name: row.name }, query: categoryFilter ? { type: categoryFilter } : {} }"
                    >
                      {{ row.subject }}
                    </router-link>
                    <p class="mt-0.5 text-sm text-ink-gray-5">
                      {{ meta(row) }}
                    </p>
                  </div>
                  <div class="flex shrink-0 items-center gap-2">
                    <StatusBadge
                      kind="request"
                      :status="row.status"
                      :team="row.handled_by_team"
                    />
                    <Icon
                      name="chevronRight"
                      size="h-4 w-4"
                      class="text-ink-gray-4"
                    />
                  </div>
                </div>

                <!-- The handling team's reply, attributed and quoted rather
                   than prefixed with a bare label, so a reply reads as
                   somebody having answered (R5: "IT" for an IT-routed
                   request, not a fixed "HR"). -->
                <div
                  v-if="row.hr_note"
                  class="surface-inset mt-3 flex gap-3 p-3"
                >
                  <span
                    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-field text-xs font-bold text-signal"
                    aria-hidden="true"
                  >{{ row.handled_by_team || 'HR' }}</span>
                  <div class="min-w-0">
                    <p class="text-sm font-medium text-ink-gray-9">
                      {{ row.handled_by_team || 'HR' }}
                      <template v-if="row.replied_on">
                        <span class="font-normal text-ink-gray-5">·
                          {{ formatDate(row.replied_on) }}</span>
                      </template>
                    </p>
                    <p class="mt-0.5 text-sm text-ink-gray-7">
                      {{ row.hr_note }}
                    </p>
                  </div>
                </div>
              </li>
            </ul>
          </section>

          <div
            v-if="moreCount"
            class="mt-4 text-center"
          >
            <Button
              variant="ghost"
              :loading="requests.loading"
              @click="showMore"
            >
              Show {{ moreCount }} more
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
          :resource="detail"
          :empty="!detail.data"
          empty-title="That request isn't here"
          empty-body="It may have been removed."
          skeleton="block"
          skeleton-height="h-64"
        >
          <template #error-title>
            We couldn't load this request
          </template>

          <article
            v-if="selected"
            class="surface-card elev-1 p-4"
            aria-label="Request"
          >
            <!-- Plan 2026-10-16-001 R11: Needs your reply banner for employee -->
            <div
              v-if="selected.status === 'Waiting on Employee'"
              class="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-4"
              data-testid="needs-reply-banner"
            >
              <div class="flex items-start gap-3">
                <span class="rounded-full bg-amber-200 p-1 text-amber-800">
                  <Icon
                    name="notifications"
                    size="h-4 w-4"
                  />
                </span>
                <div class="min-w-0 flex-1">
                  <h3 class="text-sm font-semibold text-amber-900">
                    Needs your reply
                  </h3>
                  <p
                    v-if="latestQuestion"
                    class="mt-1 text-sm font-medium italic text-amber-800"
                    data-testid="highlighted-question"
                  >
                    “{{ latestQuestion }}”
                  </p>
                  <p class="mt-1 text-xs text-amber-700">
                    Reply below to return this request to {{ selected.picked_up_by_name || selected.handled_by_team || 'the team' }}.
                  </p>
                </div>
              </div>
            </div>

            <!-- The idempotent retry, told truthfully. The record is the one
                 the first attempt made; a correction typed before the second
                 press of Send is not on it, and no later Send can apply it
                 (P2-AE7). -->
            <p
              v-if="reusedExisting === selected.name"
              class="surface-alert mb-3 p-3 text-sm"
              data-testid="request-reused"
              role="status"
            >
              Your earlier request went through — this is it. Anything you
              changed since was not applied. Ask a follow-up if you need to
              correct it.
            </p>

            <p class="label">
              {{ selected.category }} · {{ selected.name }}
            </p>
            <div class="mt-1 flex flex-wrap items-start justify-between gap-2">
              <h2 class="type-section font-heading text-ink-gray-9">
                {{ selected.subject }}
              </h2>
              <StatusBadge
                kind="request"
                :status="selected.status"
                :team="selected.handled_by_team"
              />
            </div>

            <section class="mt-4">
              <h3 class="label">
                You wrote
              </h3>
              <p
                v-if="selected.details"
                class="mt-1 whitespace-pre-line text-sm text-ink-gray-7"
              >
                {{ selected.details }}
              </p>
              <p
                v-else
                class="mt-1 text-sm text-ink-gray-5"
              >
                You sent this with no extra details.
              </p>
              <!-- Plan 2026-10-02-001 U14: a structured correction's new value,
                   masked and read-only -- the full value never comes back. -->
              <p
                v-if="selected.correction_field"
                class="mt-2 text-sm text-ink-gray-7"
                data-testid="request-correction"
              >
                New {{ CORRECTABLE_FIELDS[selected.correction_field] || selected.correction_field }}:
                <span
                  class="tabular text-ink-gray-9"
                  :aria-label="spokenValue(selected.correction_proposed_masked || '')"
                >{{ selected.correction_proposed_masked }}</span>
              </p>

              <ul
                v-if="selected.attachments.length"
                class="mt-3 flex flex-wrap gap-2"
              >
                <li
                  v-for="attachment in selected.attachments"
                  :key="attachment.name"
                >
                  <a
                    class="inline-flex min-h-11 items-center gap-2 rounded-full border border-outline-gray-2 px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
                    :href="attachment.file_url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Icon
                      name="requests"
                      size="h-4 w-4"
                      class="text-ink-gray-5"
                    />
                    {{ attachment.file_name }}
                    <span
                      v-if="fileSize(attachment.file_size)"
                      class="tabular text-ink-gray-5"
                    >{{ fileSize(attachment.file_size) }}</span>
                  </a>
                </li>
              </ul>

              <!-- The truthful partial failure. The request is on screen, so
                   nothing here says it failed: what failed was the file, and
                   Retry upload sends it to *this* request (P2-AE7). -->
              <div
                v-if="failedUpload"
                class="surface-alert mt-3 p-3"
                data-testid="upload-failed"
                role="alert"
              >
                <p class="text-sm font-medium">
                  {{ failedUpload.file.name }} didn’t upload
                </p>
                <p class="mt-0.5 text-sm">
                  Your request was sent; only the file failed.
                </p>
                <p
                  v-if="uploadError"
                  class="mt-0.5 text-sm"
                >
                  {{ uploadError }}
                </p>
                <Button
                  class="mt-2"
                  variant="outline"
                  :loading="retrying"
                  @click="retryUpload"
                >
                  Retry upload
                </Button>
              </div>
            </section>

            <section
              v-if="selected.hr_note || selected.hr_attachments.length"
              class="mt-4 border-t border-outline-gray-1 pt-4"
            >
              <h3 class="label">
                {{ selected.handled_by_team || 'HR' }} replied
              </h3>
              <div class="mt-2 flex gap-3">
                <span
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-field text-xs font-bold text-signal"
                  aria-hidden="true"
                >{{ selected.handled_by_team || 'HR' }}</span>
                <div class="min-w-0 flex-1">
                  <p class="text-sm font-medium text-ink-gray-9">
                    {{ selected.handled_by_team || 'HR' }}
                    <template v-if="selected.replied_on">
                      <span class="font-normal text-ink-gray-5">·
                        {{ formatDateTime(selected.replied_on) }}</span>
                    </template>
                  </p>
                  <p
                    v-if="selected.hr_note"
                    class="mt-0.5 whitespace-pre-line text-sm text-ink-gray-7"
                  >
                    {{ selected.hr_note }}
                  </p>
                  <ul
                    v-if="selected.hr_attachments.length"
                    class="mt-2 flex flex-wrap gap-2"
                  >
                    <li
                      v-for="attachment in selected.hr_attachments"
                      :key="attachment.name"
                    >
                      <a
                        class="inline-flex min-h-11 items-center gap-2 rounded-full border border-outline-gray-2 px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
                        :href="attachment.file_url"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <Icon
                          name="requests"
                          size="h-4 w-4"
                          class="text-ink-gray-5"
                        />
                        {{ attachment.file_name }}
                      </a>
                    </li>
                  </ul>
                  <p
                    v-if="justMarkedRead"
                    class="mt-2 flex items-center gap-1.5 text-sm text-ink-gray-5"
                    data-testid="marked-as-read"
                  >
                    <Icon
                      name="approvals"
                      size="h-4 w-4"
                      class="text-ink-green-3"
                    />
                    Marked as read just now
                  </p>
                </div>
              </div>
            </section>

            <!-- P5-R10 / Plan 2026-10-15-003 KTD8: shared thread component. -->
            <RequestThread
              class="mt-4 border-t border-outline-gray-1 pt-4"
              :entries="selected.thread || []"
              :can-comment="!!selected.can_comment"
              :closed="!!selected.closed"
              :request-name="selected.name"
              :handler-name="selected.picked_up_by_name || selected.handled_by_team"
              :employee-name="selected.employee_name || ''"
              :expected-modified="selected.status === 'Waiting on Employee' ? selected.modified : null"
              :milestones="timeline"
              mode="employee"
              @posted="reloadRequest"
              @new-request="newRequest"
            />

            <div class="mt-4 border-t border-outline-gray-1 pt-4">
              <!-- A follow-up is a new request, carrying the subject it is
                   about. HR Request has no threading and inventing one here
                   would be a second conversation model (P2-U8 scope). -->
              <Button
                variant="subtle"
                @click="newRequest"
              >
                Ask a follow-up
              </Button>
            </div>
          </article>
        </AsyncState>
      </aside>
    </div>

    <Dialog
      v-model="showForm"
      :options="{ title: 'New request' }"
    >
      <template #body-content>
        <RequestForm
          :initial-category="route.query.category"
          :initial-subject="route.query.subject"
          @created="onCreated"
          @cancel="showForm = false"
        />
      </template>
    </Dialog>
  </div>
</template>
