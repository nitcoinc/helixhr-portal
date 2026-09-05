<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import RequestForm from '@/components/RequestForm.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDate } from '@/lib/dates'

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

const rows = computed(() => requests.data || [])
</script>

<template>
  <div>
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

    <AsyncState
      section="requests-list"
      :resource="requests"
      :empty="rows.length === 0"
      empty-title="No requests yet"
      empty-body="Ask HR for a letter, a payroll correction, or anything else you need."
      :skeleton-rows="3"
    >
      <template #empty-action>
        <!-- Not the header's wording: with an empty list both are on screen
             at once, and two controls with the same accessible name is a
             duplicate rather than an affordance. -->
        <Button
          variant="solid"
          theme="blue"
          @click="showForm = true"
        >
          Send your first request
        </Button>
      </template>

      <ul class="space-y-2">
        <li
          v-for="row in rows"
          :key="row.name"
          class="surface-card elev-1 p-4"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="label">
                {{ row.category }}
              </p>
              <p class="mt-1 font-medium text-ink-gray-9">
                {{ row.subject }}
              </p>
              <p class="mt-0.5 text-sm text-ink-gray-5">
                Sent {{ formatDate(row.creation) }}
              </p>
            </div>
            <StatusBadge
              kind="request"
              :status="row.status"
            />
          </div>
          <!-- HR's reply is attributed and quoted rather than prefixed with a
               bare "HR:", so a reply reads as somebody having answered. -->
          <div
            v-if="row.hr_note"
            class="surface-inset mt-3 p-3"
          >
            <p class="label">
              HR replied
            </p>
            <p class="mt-1 text-sm text-ink-gray-7">
              {{ row.hr_note }}
            </p>
          </div>
        </li>
      </ul>
    </AsyncState>

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
