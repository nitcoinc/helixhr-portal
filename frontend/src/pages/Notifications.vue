<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { formatDateTime } from '@/lib/dates'

const router = useRouter()

const logs = createResource({
  url: 'frappe.desk.doctype.notification_log.notification_log.get_notification_logs',
  params: { limit: 50 },
  auto: true,
})

const rows = computed(() => logs.data?.notification_logs || [])

const ROUTE_FOR_DOCTYPE = {
  'Leave Application': '/leave',
  Timesheet: '/timesheet',
  'HR Request': '/requests',
}

const ICON_FOR_DOCTYPE = {
  'Leave Application': 'leave',
  Timesheet: 'timesheet',
  'HR Request': 'requests',
}

function openLog(row) {
  const route = ROUTE_FOR_DOCTYPE[row.document_type]
  if (route) router.push(route)
}

const markAllRead = createResource({
  url: 'frappe.desk.doctype.notification_log.notification_log.mark_all_as_read',
  method: 'POST',
})

async function markAll() {
  await markAllRead.submit()
  logs.reload()
}
</script>

<template>
  <div>
    <PageHeader title="Notifications">
      <template #actions>
        <Button
          variant="subtle"
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
      <ul class="space-y-2">
        <li
          v-for="row in rows"
          :key="row.name"
        >
          <button
            class="surface-card elev-1 flex w-full cursor-pointer items-start gap-3 p-3 text-left"
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
              <span class="tabular mt-1 block text-xs text-ink-gray-5">
                {{ formatDateTime(row.creation) }}
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
    </AsyncState>
  </div>
</template>
