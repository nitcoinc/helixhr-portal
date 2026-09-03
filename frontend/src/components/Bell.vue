<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource } from 'frappe-ui'

const router = useRouter()

// frappe.client.get_list already scopes to the session user via
// Notification Log's own for_user permission -- no explicit filter
// needed here.
const unreadCount = createResource({
  url: 'frappe.client.get_list',
  params: {
    doctype: 'Notification Log',
    filters: { read: 0 },
    fields: ['name'],
    limit_page_length: 0,
  },
  auto: true,
  transform: (rows) => rows.length,
})

let poll
onMounted(() => {
  poll = setInterval(() => unreadCount.reload(), 60000)
})
onUnmounted(() => clearInterval(poll))

function openBell() {
  router.push('/notifications')
}
</script>

<template>
  <button
    class="relative rounded-full p-2 text-ink-gray-6 hover:bg-surface-gray-2"
    aria-label="Notifications"
    @click="openBell"
  >
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.7"
      class="h-5 w-5"
    >
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5m6 0a3 3 0 1 1-6 0m6 0H9"
      />
    </svg>
    <span
      v-if="unreadCount.data > 0"
      class="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-surface-red-3 px-1 text-[10px] font-medium text-ink-white"
    >
      {{ unreadCount.data > 9 ? '9+' : unreadCount.data }}
    </span>
  </button>
</template>
