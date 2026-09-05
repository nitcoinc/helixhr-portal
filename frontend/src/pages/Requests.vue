<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import RequestForm from '@/components/RequestForm.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import Icon from '@/components/Icon.vue'
import { attachToRequest } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/dates'
import { currentUnread, setUnread, unreadCount } from '@/lib/unread'

// P2-U8 / KTD5. `/requests` and `/requests/:name` are the same component: the
// selected record is a route parameter, so refresh and browser Back land on
// the same request, and the phone view and the desktop panel are two shapes
// of one state rather than two screens.
const props = defineProps({
  name: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

// P2-R22. A bounded first page with an explicit Show more, and one
// session-scoped read where the page used to send its own
// `frappe.client.get_list` with `limit_page_length: 0`.
const pageLimit = ref(20)
const requests = createResource({
  url: 'helixhr.api.get_my_requests',
  makeParams: () => ({ limit: pageLimit.value }),
  auto: true,
})

const rows = computed(() => requests.data?.requests || [])
const total = computed(() => requests.data?.total || 0)
const moreCount = computed(() => Math.max(0, total.value - rows.value.length))

function showMore() {
  pageLimit.value = Math.min(100, pageLimit.value + 20)
  requests.reload()
}

// Needs you / Open / Closed. "Needs you" is not a status: it is an *unread
// notification* about this request, which is the same read state the shell
// badge and Notifications use (P2-KTD6). A reply you have already read stops
// needing you even though the request is still Done.
const CLOSED = ['Done', 'Rejected']
const groups = computed(() =>
  [
    {
      key: 'needs-you',
      label: 'Needs you',
      rows: rows.value.filter((row) => row.unread),
    },
    {
      key: 'open',
      label: 'Open',
      rows: rows.value.filter((row) => !row.unread && !CLOSED.includes(row.status)),
    },
    {
      key: 'closed',
      label: 'Closed',
      rows: rows.value.filter((row) => !row.unread && CLOSED.includes(row.status)),
    },
  ].filter((group) => group.rows.length),
)

/** The row's second line: when it was sent, and the most recent thing that
 * has happened to it since. Only steps the record actually knows are
 * printed -- a request made before the portal started stamping them simply
 * says "Sent …". */
function meta(row) {
  const parts = [`Sent ${formatDate(row.creation)}`]
  if (row.closed_on) parts.push(`closed ${formatDate(row.closed_on)}`)
  else if (row.picked_up_on) parts.push(`picked up ${formatDate(row.picked_up_on)}`)
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

watch(
  () => props.name,
  (name) => {
    if (name) detail.fetch()
  },
  { immediate: true },
)

const selected = computed(() => (props.name ? detail.data : null))

function closeDetail() {
  router.push({ name: 'Requests' })
}

// --- clearing the obligation --------------------------------------------

const markRead = createResource({ url: 'helixhr.api.mark_my_request_read', method: 'POST' })
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
  if (row) row.unread = false
  try {
    const result = await markRead.submit({ name: data.name })
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

/** Bytes as a person reads them. Only ever used next to a file name. */
function fileSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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
function onCreated({ name, uploadError: failure, pendingFile }) {
  showForm.value = false
  uploadError.value = failure || ''
  pendingUpload.value = failure && pendingFile ? { name, file: pendingFile } : null
  requests.reload()
  router.push({ name: 'RequestDetail', params: { name } })
}

// --- one URL, two shapes ------------------------------------------------

// 1024px is where the shell drops the phone tab bar for the side nav, so it
// is also where there is room for the list and the record side by side. The
// URL is the same at both widths (KTD5).
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

// The timeline. Three steps, and only the ones that happened are drawn --
// a step with no date is a step the record cannot vouch for.
const timeline = computed(() => {
  const request = selected.value
  if (!request) return []
  return [
    { key: 'sent', label: 'Sent', at: request.creation },
    { key: 'picked-up', label: 'Picked up by HR', at: request.picked_up_on },
    { key: 'replied', label: 'Replied', at: request.replied_on },
  ].filter((step) => step.at)
})
</script>

<template>
  <div>
    <PageHeader title="Requests">
      <template #actions>
        <Button
          variant="solid"
          theme="blue"
          @click="newRequest"
        >
          New request
        </Button>
      </template>
    </PageHeader>

    <div class="lg:flex lg:items-start lg:gap-6">
      <!-- The list. On a phone a selected record takes the whole width
           (P2-R6's full-height treatment); at lg: both columns are on screen
           and the list stays put while the panel changes. -->
      <div
        v-show="!name || isDesktop"
        class="min-w-0 lg:flex-1"
      >
        <AsyncState
          section="requests-list"
          :resource="requests"
          :empty="rows.length === 0"
          empty-title="No requests yet"
          empty-body="Ask HR for a letter, a payroll correction, or anything else you need."
          :skeleton-rows="3"
        >
          <template #empty-action>
            <!-- Not the header's wording: with an empty list both are on
                 screen at once, and two controls with the same accessible
                 name is a duplicate rather than an affordance. -->
            <Button
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
                      <!-- Tone plus a caption, never hue alone: the row is
                           also in the "Needs you" group and the dot is
                           named for a screen reader. -->
                      <span
                        v-if="row.unread"
                        class="inline-block h-2 w-2 shrink-0 rounded-full bg-field"
                      ><span class="sr-only">Unread</span></span>
                      {{ row.category }}
                    </p>
                    <!-- One link per row, stretched over the whole card. Two
                         nested interactive elements would be the alternative,
                         and that is neither valid markup nor navigable. -->
                    <!-- P2-U9: `-my-2 min-h-11` for the same reason as the
                         Leave row -- the stretched pseudo-element makes the
                         card tappable, but an automated target-size check
                         reads the link's own 24px box. The negative margin
                         keeps the list's density unchanged. -->
                    <router-link
                      class="-my-2 inline-flex min-h-11 items-center font-medium text-ink-gray-9 after:absolute after:inset-0 after:content-['']"
                      :class="row.unread ? 'font-semibold' : ''"
                      :to="{ name: 'RequestDetail', params: { name: row.name } }"
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
                    />
                    <Icon
                      name="chevronRight"
                      size="h-4 w-4"
                      class="text-ink-gray-4"
                    />
                  </div>
                </div>

                <!-- HR's reply, attributed and quoted rather than prefixed
                     with a bare "HR:", so a reply reads as somebody having
                     answered. -->
                <div
                  v-if="row.hr_note"
                  class="surface-inset mt-3 flex gap-3 p-3"
                >
                  <span
                    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-field text-xs font-bold text-signal"
                    aria-hidden="true"
                  >HR</span>
                  <div class="min-w-0">
                    <p class="text-sm font-medium text-ink-gray-9">
                      HR
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
              />
            </div>

            <!-- Sent -> Picked up -> Replied. A step the record has no date
                 for is not drawn: an undated dot would claim something
                 happened without being able to say when. -->
            <ol
              class="surface-inset mt-4 space-y-2 p-3"
              data-testid="request-timeline"
            >
              <li
                v-for="step in timeline"
                :key="step.key"
                class="flex items-baseline justify-between gap-3 text-sm"
              >
                <span class="flex items-center gap-2 text-ink-gray-9">
                  <span
                    class="inline-block h-2 w-2 shrink-0 rounded-full bg-field"
                    aria-hidden="true"
                  />
                  {{ step.label }}
                </span>
                <span class="tabular shrink-0 text-ink-gray-5">{{ formatDateTime(step.at) }}</span>
              </li>
            </ol>

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
                HR replied
              </h3>
              <div class="mt-2 flex gap-3">
                <span
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-field text-xs font-bold text-signal"
                  aria-hidden="true"
                >HR</span>
                <div class="min-w-0 flex-1">
                  <p class="text-sm font-medium text-ink-gray-9">
                    HR
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
