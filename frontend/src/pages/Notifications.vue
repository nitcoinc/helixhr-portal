<script setup>
import { computed, nextTick, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createResource, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import { keepaliveRequest } from '@/lib/api'
import { formatDateTime, today } from '@/lib/dates'
import { bucketGroups } from '@/lib/notificationGroups'
import { notify } from '@/lib/notify'
import { currentUnread, setUnread, unreadCount } from '@/lib/unread'

const route = useRoute()
const router = useRouter()

// Bounded by count (P2-R22). The Unread view is bounded higher: it is the
// list somebody works through, and every row in it is one they have not
// seen, so a 60-unread backlog shows as 60 rather than as the 50 newest.
const LIMIT = 50
const UNREAD_LIMIT = 200

// `?unread=1` is the Unread view, so it survives a reload and can be linked.
const unreadView = computed(() => route.query.unread === '1')
const viewOptions = computed(() => [
  { value: false, label: 'All' },
  { value: true, label: 'Unread', count: currentUnread() },
])

function showView(unread) {
  router.replace({ query: { ...route.query, unread: unread ? '1' : undefined } })
}

// Plan 2026-10-17-001 U10. `helixhr.api.get_my_notifications`, not Frappe's
// `get_notification_logs`: that one takes only a count, so "Unread" could
// only be a filter over the newest 50, and it is declared with a 60s HTTP
// cache (applied to GETs) that would hand a reload the pre-mark-read answer.
// This read filters on the server and is never cached, so a reload after a
// mark-read is the truth.
const logs = createResource({
  url: 'helixhr.api.get_my_notifications',
  makeParams: () => ({
    unread_only: unreadView.value ? 1 : 0,
    limit: unreadView.value ? UNREAD_LIMIT : LIMIT,
  }),
  auto: true,
  // The same response carries the caller's unread total, so the badge
  // agrees with the list it sits next to.
  onSuccess: (data) => setUnread(data?.unread ?? currentUnread()),
})

// Only while this page is the route: leaving it from the Unread view drops
// the query too, and that is not a reason to fetch the All view.
watch(unreadView, () => {
  if (route.name === 'Notifications') logs.reload()
})

const rows = computed(() => {
  const all = logs.data?.logs || []
  // A row read on this screen leaves the Unread view at once ("Mark all
  // read" empties it); the All view keeps it, now in the read style.
  return unreadView.value ? all.filter((row) => !row.read) : all
})
const limit = computed(() => (unreadView.value ? UNREAD_LIMIT : LIMIT))
const atLimit = computed(() => (logs.data?.logs || []).length >= limit.value)

// Today / Yesterday / This week / Older, one row per record (ED-04, EP-07).
const buckets = computed(() => bucketGroups(rows.value, today()))

// "+N earlier", per record, open or not.
const expanded = reactive({})

const ROUTE_FOR_DOCTYPE = {
  'Leave Application': { detail: 'LeaveDetail', list: 'Leave' },
  'HR Request': { detail: 'RequestDetail', list: 'Requests' },
  // A week is addressed by its Monday (`/timesheet/:weekStart`) and a
  // Notification Log carries the Timesheet's *id*. P2-U6 added the one
  // indexed read that turns one into the other, so a timesheet notification
  // now opens the week it is about rather than the list containing it.
  // Past weeks stays the fallback for a record that no longer resolves.
  Timesheet: { detail: 'TimesheetWeek', list: 'TimesheetHistory' },
  // P3-U6 / P3-R17. Every state change writes one of these, so the row has
  // to open the request it is about -- the sheet over the Attendance page,
  // which is where the days it names live.
  'Attendance Request': { detail: 'AttendanceRequestDetail', list: 'Attendance' },
  // Plan 2026-10-04-001 U13: a background export's Ready/Failed notice opens
  // Reports, whose export menu holds "My exports".
  'HelixHR Report Export': { list: 'Reports' },
}

const ICON_FOR_DOCTYPE = {
  'Leave Application': 'leave',
  Timesheet: 'timesheet',
  'HR Request': 'requests',
  'Attendance Request': 'attendance',
}

const timesheetWeek = createResource({ url: 'helixhr.api.get_timesheet_week_start' })
const requestView = createResource({ url: 'helixhr.api.get_request_view' })

async function routeFor(row) {
  const route = ROUTE_FOR_DOCTYPE[row.document_type]
  if (!route) return null
  if (route.detail && row.document_name) {
    if (row.document_type === 'Timesheet') {
      // One indexed, session-scoped read, and only when a timesheet row is
      // actually opened -- the list itself pays nothing for it.
      const weekStart = await timesheetWeek
        .submit({ name: row.document_name })
        .catch(() => null)
      return weekStart ? { name: route.detail, params: { weekStart } } : { name: route.list }
    }
    if (row.document_type === 'HR Request') {
      const view = await requestView
        .submit({ name: row.document_name })
        .catch(() => null)
      if (view === 'handler') {
        return { name: 'RequestDetail', params: { name: row.document_name }, query: { tab: 'work' } }
      }
      if (view === 'employee') {
        return { name: 'RequestDetail', params: { name: row.document_name } }
      }
      return { name: 'Requests' }
    }
    return { name: route.detail, params: { name: row.document_name } }
  }
  return { name: route.list }
}

// "Today, 16:02" / "Yesterday, 10:42" / "14 Aug, 09:30", in the user's own
// calendar (lib/dates owns that conversion). Under the Today and Yesterday
// headings the day word is already said, so only the time is left.
function when(row, bucketKey) {
  const text = formatDateTime(row.creation)
  return bucketKey === 'today' || bucketKey === 'yesterday'
    ? text.replace(/^(Today|Yesterday), /, '')
    : text
}

// The body of a notification as one quoted line. Frappe stores it as rich
// text, so the tags come off and the entities come back before it is printed
// as a sentence. `textarea.innerHTML` decodes without ever building elements.
const decoder = typeof document === 'undefined' ? null : document.createElement('textarea')
function quote(row) {
  const body = row.description || row.email_content
  if (!body || !decoder) return ''
  decoder.innerHTML = String(body).replace(/<[^>]*>/g, ' ')
  return decoder.value.replace(/\s+/g, ' ').trim()
}

const markAllRead = createResource({
  url: 'frappe.desk.doctype.notification_log.notification_log.mark_all_as_read',
  method: 'POST',
})

/**
 * Mark a whole record's logs read, then open the record (P2-R13, U10).
 *
 * One call clears every log the caller holds about the record --
 * `mark_record_notifications_read` from U6 -- so "+5 earlier" does not leave
 * five unread rows behind it. The row and the badge move now, by the group's
 * own unread count, and settle to the server's total when it answers.
 * `keepalive`, because the very next thing is a route change and often a
 * refresh after it.
 */
async function openGroup(group) {
  const { latest } = group
  const index = buckets.value.flatMap((bucket) => bucket.groups).indexOf(group)
  const members = [latest, ...group.earlier]
  const unread = members.filter((row) => !row.read).length
  if (unread) {
    members.forEach((row) => {
      row.read = 1
    })
    setUnread(currentUnread() - unread)
    const write =
      latest.document_type && latest.document_name
        ? keepaliveRequest('helixhr.api.mark_record_notifications_read', {
            reference_doctype: latest.document_type,
            reference_name: latest.document_name,
          })
        : keepaliveRequest('frappe.desk.doctype.notification_log.notification_log.mark_as_read', {
            docname: latest.name,
          })
    write
      .then((result) => {
        if (typeof result?.unread === 'number') setUnread(result.unread)
      })
      .catch(() => unreadCount.reload())
  }
  const to = await routeFor(latest)
  if (to) router.push(to)
  // A record with no page to open just leaves the Unread view; focus goes to
  // the row that took its place, or to the filter once the list is empty
  // (KTD16).
  else if (unread && unreadView.value) focusRowAt(index)
}

async function focusRowAt(index) {
  await nextTick()
  const left = document.querySelectorAll('[data-testid="notification-row"]')
  const target =
    left[Math.min(index, left.length - 1)] ||
    document.querySelector('[aria-label="Show notifications"] button')
  target?.focus()
}

async function markAll() {
  try {
    await markAllRead.submit()
  } catch {
    notify.error('Couldn’t mark your notifications read. Try again.')
    return
  }
  for (const row of logs.data?.logs || []) row.read = 1
  setUnread(0)
}
</script>

<template>
  <div>
    <PageHeader title="Notifications">
      <template #actions>
        <Button
          variant="ghost"
          :loading="markAllRead.loading"
          @click="markAll"
        >
          Mark all read
        </Button>
      </template>
    </PageHeader>

    <SegmentedChips
      class="mb-4"
      label="Show notifications"
      :options="viewOptions"
      :model-value="unreadView"
      @update:model-value="showView"
    />

    <AsyncState
      section="notifications"
      :resource="logs"
      :empty="rows.length === 0"
      :empty-title="unreadView ? 'Nothing unread' : 'You’re all caught up'"
      :empty-body="
        unreadView
          ? 'You have read everything. Choose All to look back at earlier notifications.'
          : 'Decisions on your leave, timesheets and requests land here.'
      "
      skeleton="row"
      :skeleton-rows="4"
    >
      <!-- Today / Yesterday / This week / Older, with `.label` -- the only
           grouping device in this system. A run of cards under a small word,
           never a second surface. -->
      <section
        v-for="bucket in buckets"
        :key="bucket.key"
        class="mb-5"
        :aria-label="bucket.label"
      >
        <h2 class="label mb-2">
          {{ bucket.label }}
        </h2>
        <ul class="space-y-2">
          <li
            v-for="group in bucket.groups"
            :key="group.key"
            class="surface-card elev-1"
          >
            <!-- One row per record: the newest log, and the whole row opens
                 the record and clears every log about it. -->
            <button
              class="flex w-full cursor-pointer items-start gap-3 p-3 text-left"
              data-testid="notification-row"
              :data-read="group.unread ? '0' : '1'"
              @click="openGroup(group)"
            >
              <!-- An unread row carries a *filled field-green* icon tile and a
                   read one a grey tile. Tone plus weight, never hue alone: the
                   subject is also semibold while unread, and the tile's state
                   is captioned for a screen reader. -->
              <span
                class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                :class="group.unread ? 'bg-field text-signal' : 'bg-surface-gray-2 text-ink-gray-6'"
              >
                <Icon
                  :name="ICON_FOR_DOCTYPE[group.latest.document_type] || 'notifications'"
                  size="h-4 w-4"
                />
                <span class="sr-only">{{ group.unread ? 'Unread' : 'Read' }}</span>
              </span>
              <span class="min-w-0 flex-1">
                <span
                  class="block text-sm text-ink-gray-9"
                  :class="group.unread ? 'font-semibold' : ''"
                >
                  {{ group.latest.subject }}
                </span>
                <span
                  v-if="quote(group.latest)"
                  class="mt-0.5 block truncate text-sm text-ink-gray-6"
                >
                  “{{ quote(group.latest) }}”
                </span>
                <span class="tabular mt-1 block text-xs text-ink-gray-5">
                  {{ when(group.latest, bucket.key) }}
                </span>
              </span>
              <Icon
                name="chevronRight"
                size="h-4 w-4"
                class="mt-2 shrink-0 text-ink-gray-4"
              />
            </button>

            <template v-if="group.earlier.length">
              <!-- Outside the row's button: two nested controls would be
                   neither valid markup nor reachable. -->
              <button
                type="button"
                class="-mt-2 ml-12 inline-flex min-h-11 cursor-pointer items-center px-3 text-xs font-medium text-ink-gray-6 hover:text-ink-gray-9"
                data-testid="notification-earlier-toggle"
                :aria-expanded="expanded[group.key] ? 'true' : 'false'"
                :aria-controls="`notification-earlier-${group.latest.name}`"
                @click="expanded[group.key] = !expanded[group.key]"
              >
                {{ expanded[group.key] ? 'Hide earlier' : `+${group.earlier.length} earlier` }}
              </button>
              <ul
                v-show="expanded[group.key]"
                :id="`notification-earlier-${group.latest.name}`"
                class="ml-12 space-y-2 px-3 pb-3"
                data-testid="notification-earlier"
              >
                <li
                  v-for="row in group.earlier"
                  :key="row.name"
                  class="text-sm"
                >
                  <span
                    class="block text-ink-gray-8"
                    :class="row.read ? '' : 'font-semibold'"
                  >
                    {{ row.subject }}
                  </span>
                  <span class="tabular block text-xs text-ink-gray-5">
                    {{ formatDateTime(row.creation) }}
                  </span>
                </li>
              </ul>
            </template>
          </li>
        </ul>
      </section>

      <p
        v-if="atLimit"
        class="pb-2 text-center text-sm text-ink-gray-5"
      >
        <template v-if="unreadView">
          Showing your {{ UNREAD_LIMIT }} newest unread. Mark all read to clear the rest.
        </template>
        <template v-else>
          Showing your {{ LIMIT }} most recent.
        </template>
      </p>
    </AsyncState>
  </div>
</template>
