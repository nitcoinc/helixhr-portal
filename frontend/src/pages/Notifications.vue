<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button } from 'frappe-ui'

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
  <div class="min-h-screen bg-surface-gray-1 pb-24">
    <header class="flex items-center justify-between border-b border-outline-gray-2 bg-surface-white px-4 py-4">
      <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
        Notifications
      </h1>
      <Button
        variant="subtle"
        size="sm"
        @click="markAll"
      >
        Mark all read
      </Button>
    </header>

    <div class="space-y-2 px-4 py-4">
      <p
        v-if="logs.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <p
        v-else-if="rows.length === 0"
        class="text-ink-gray-5"
      >
        You're all caught up.
      </p>
      <button
        v-for="row in rows"
        :key="row.name"
        class="block w-full rounded-lg border border-outline-gray-2 bg-surface-white p-3 text-left"
        :class="!row.read ? 'border-l-4 border-l-outline-blue-3' : ''"
        @click="openLog(row)"
      >
        <p class="text-sm text-ink-gray-9">
          {{ row.subject }}
        </p>
        <p class="mt-1 text-xs text-ink-gray-5">
          {{ row.creation }}
        </p>
      </button>
    </div>
  </div>
</template>
