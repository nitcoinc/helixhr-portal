<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { createResource, Button, Badge, Dialog } from 'frappe-ui'
import RequestForm from '@/components/RequestForm.vue'
import PageHeader from '@/components/PageHeader.vue'

const route = useRoute()

const requests = createResource({
  url: 'frappe.client.get_list',
  params: {
    doctype: 'HR Request',
    fields: ['name', 'category', 'subject', 'status', 'hr_note', 'creation'],
    order_by: 'creation desc',
    limit_page_length: 0,
  },
  auto: true,
})

const showForm = ref(!!route.query.subject || !!route.query.category)

function onCreated() {
  showForm.value = false
  requests.reload()
}

function badgeTheme(status) {
  if (status === 'Done') return 'green'
  if (status === 'Rejected') return 'red'
  if (status === 'In Progress') return 'blue'
  return 'orange'
}

const rows = computed(() => requests.data || [])
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Requests">
      <template #actions>
        <Button
          variant="solid"
          theme="blue"
          @click="showForm = true"
        >
          New request
        </Button>
      </template>
    </PageHeader>

    <div class="space-y-3">
      <p
        v-if="requests.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <p
        v-else-if="rows.length === 0"
        class="text-ink-gray-5"
      >
        You have no requests yet. New request to get started.
      </p>
      <div
        v-for="row in rows"
        :key="row.name"
        class="rounded-lg border border-outline-gray-2 bg-surface-white p-4"
      >
        <div class="flex items-start justify-between">
          <div>
            <p class="text-sm text-ink-gray-5">
              {{ row.category }}
            </p>
            <p class="font-medium text-ink-gray-9">
              {{ row.subject }}
            </p>
          </div>
          <Badge :theme="badgeTheme(row.status)">
            {{ row.status }}
          </Badge>
        </div>
        <p
          v-if="row.hr_note"
          class="mt-2 rounded-md bg-surface-gray-1 p-2 text-sm text-ink-gray-7"
        >
          HR: {{ row.hr_note }}
        </p>
      </div>
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
