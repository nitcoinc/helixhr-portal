<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
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
  <div class="space-y-4">
    <PageHeader title="Notifications">
      <template #actions>
        <Button
          variant="subtle"
          size="sm"
          @click="markAll"
        >
          Mark all read
        </Button>
      </template>
    </PageHeader>

    <div class="space-y-2">
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
      <!-- Unread is marked with a dot, not a thick coloured left border: the
           border reads as decoration, costs 3px of text alignment between
           read and unread rows, and is invisible to anyone who can't pick the
           hue out. The dot is captioned for screen readers too. -->
      <button
        v-for="row in rows"
        :key="row.name"
        class="flex w-full cursor-pointer items-start gap-3 rounded-lg border bg-surface-white p-3 text-left transition-colors duration-200"
        :class="
          row.read
            ? 'border-outline-gray-2 hover:border-outline-gray-3'
            : 'border-outline-gray-2 hover:border-blue-600'
        "
        @click="openLog(row)"
      >
        <span
          class="mt-1.5 h-2 w-2 shrink-0 rounded-full"
          :class="row.read ? 'bg-transparent' : 'bg-blue-700'"
        >
          <span class="sr-only">{{ row.read ? '' : 'Unread' }}</span>
        </span>
        <span class="min-w-0 flex-1">
          <span
            class="block text-sm text-ink-gray-9"
            :class="row.read ? '' : 'font-medium'"
          >
            {{ row.subject }}
          </span>
          <span class="tabular mt-1 block text-xs text-ink-gray-5">
            {{ formatDateTime(row.creation) }}
          </span>
        </span>
      </button>
    </div>
  </div>
</template>
