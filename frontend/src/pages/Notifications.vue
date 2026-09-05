<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { formatDateTime } from '@/lib/dates'
import { currentUnread, setUnread, unreadCount } from '@/lib/unread'

const router = useRouter()

// Bounded by count, which is the only bound this endpoint offers (P2-R22).
const LIMIT = 50

const logs = createResource({
  url: 'frappe.desk.doctype.notification_log.notification_log.get_notification_logs',
  params: { limit: LIMIT },
  auto: true,
})

const rows = computed(() => logs.data?.notification_logs || [])

// The exact record, by route name (P2-U2's convention, P2-R12). A
// notification that opens a list is a notification that makes you search for
// the thing it just told you about.
const ROUTE_FOR_DOCTYPE = {
  'Leave Application': { detail: 'LeaveDetail', list: 'Leave' },
  'HR Request': { detail: 'RequestDetail', list: 'Requests' },
  // A week is addressed by its Monday (`/timesheet/:weekStart`) and a
  // Notification Log carries the Timesheet's *id*. P2-U6 added the one
  // indexed read that turns one into the other, so a timesheet notification
  // now opens the week it is about rather than the list containing it.
  // Past weeks stays the fallback for a record that no longer resolves.
  Timesheet: { detail: 'TimesheetWeek', list: 'TimesheetHistory' },
}

const ICON_FOR_DOCTYPE = {
  'Leave Application': 'leave',
  Timesheet: 'timesheet',
  'HR Request': 'requests',
}

const timesheetWeek = createResource({ url: 'helixhr.api.get_timesheet_week_start' })

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
    return { name: route.detail, params: { name: row.document_name } }
  }
  return { name: route.list }
}

// "Today, 16:02" / "Yesterday, 10:42" / "14 Aug, 09:30", in the user's own
// calendar. The Today group is read off the same string rather than a second
// timezone conversion here: lib/dates owns that conversion, and a page-local
// copy of it is exactly the drift P2-AE3 is about.
function when(row) {
  return formatDateTime(row.creation)
}

function isToday(row) {
  return when(row).startsWith('Today')
}

const todayRows = computed(() => rows.value.filter(isToday))
const earlierRows = computed(() => rows.value.filter((row) => !isToday(row)))

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

const markRead = createResource({
  url: 'frappe.desk.doctype.notification_log.notification_log.mark_as_read',
  method: 'POST',
})

const markAllRead = createResource({
  url: 'frappe.desk.doctype.notification_log.notification_log.mark_all_as_read',
  method: 'POST',
})

/**
 * Open the record this notification is about, and mark this one row read on
 * the way (P2-R13). The row and the shell's badge both move now, in the same
 * interaction, rather than at the next poll -- and the list is not reloaded
 * to find that out: `get_notification_logs` is served with a 60s HTTP cache,
 * so a reload here would hand back the pre-read answer.
 */
async function openLog(row) {
  if (!row.read) {
    row.read = 1
    setUnread(currentUnread() - 1)
    markRead.submit({ docname: row.name }).catch(() => unreadCount.reload())
  }
  const to = await routeFor(row)
  if (to) router.push(to)
}

async function markAll() {
  await markAllRead.submit()
  rows.value.forEach((row) => {
    row.read = 1
  })
  setUnread(0)
}
</script>

<template>
  <div>
    <PageHeader title="Notifications">
      <template #actions>
        <Button
          variant="subtle"
          :loading="markAllRead.loading"
          @click="markAll"
        >
          Mark all read
        </Button>
      </template>
    </PageHeader>

    <AsyncState
      section="notifications"
      :resource="logs"
      :empty="rows.length === 0"
      empty-title="You're all caught up"
      empty-body="Decisions on your leave, timesheets and requests land here."
      skeleton="row"
      :skeleton-rows="4"
    >
      <!-- Today / Earlier, with `.label` -- the only grouping device in this
           system. A run of cards under a small word, never a second surface. -->
      <template
        v-for="group in [
          { key: 'today', label: 'Today', rows: todayRows },
          { key: 'earlier', label: 'Earlier', rows: earlierRows },
        ]"
        :key="group.key"
      >
        <section
          v-if="group.rows.length"
          class="mb-5"
          :aria-label="group.label"
        >
          <h2 class="label mb-2">
            {{ group.label }}
          </h2>
          <ul class="space-y-2">
            <li
              v-for="row in group.rows"
              :key="row.name"
            >
              <button
                class="surface-card elev-1 flex w-full cursor-pointer items-start gap-3 p-3 text-left"
                data-testid="notification-row"
                :data-read="row.read ? '1' : '0'"
                @click="openLog(row)"
              >
                <!-- An unread row carries a *filled field-green* icon tile and a
                     read one a grey tile. Tone plus weight, never hue alone: the
                     subject is also semibold while unread, and the tile's state
                     is captioned for a screen reader. -->
                <span
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                  :class="row.read ? 'bg-surface-gray-2 text-ink-gray-6' : 'bg-field text-signal'"
                >
                  <Icon
                    :name="ICON_FOR_DOCTYPE[row.document_type] || 'notifications'"
                    size="h-4 w-4"
                  />
                  <span class="sr-only">{{ row.read ? 'Read' : 'Unread' }}</span>
                </span>
                <span class="min-w-0 flex-1">
                  <span
                    class="block text-sm text-ink-gray-9"
                    :class="row.read ? '' : 'font-semibold'"
                  >
                    {{ row.subject }}
                  </span>
                  <span
                    v-if="quote(row)"
                    class="mt-0.5 block truncate text-sm text-ink-gray-6"
                  >
                    “{{ quote(row) }}”
                  </span>
                  <span class="tabular mt-1 block text-xs text-ink-gray-5">
                    {{ group.key === 'today' ? when(row).replace(/^Today, /, '') : when(row) }}
                  </span>
                </span>
                <Icon
                  name="chevronRight"
                  size="h-4 w-4"
                  class="mt-2 shrink-0 text-ink-gray-4"
                />
              </button>
            </li>
          </ul>
        </section>
      </template>

      <p class="pb-2 text-center text-sm text-ink-gray-5">
        Showing your {{ LIMIT }} most recent.
      </p>
    </AsyncState>
  </div>
</template>
