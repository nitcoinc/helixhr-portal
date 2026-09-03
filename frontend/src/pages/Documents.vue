<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'

const me = createResource({
  url: 'hrms.api.get_current_employee_info',
  auto: true,
  onSuccess: () => documents.fetch(),
})

// R23: company-less links (for everyone) plus links for the employee's
// own company -- "or_filters" is needed because that's an OR across two
// conditions on the same field, which a plain equality filters list
// can't express.
const documents = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'HelixHR Document Link',
    fields: ['name', 'title', 'url', 'description', 'company'],
    or_filters: [
      ['company', 'is', 'not set'],
      ['company', '=', me.data?.company],
    ],
    order_by: 'title asc',
    limit_page_length: 0,
  }),
  auto: false,
})

const rows = computed(() => documents.data || [])
</script>

<template>
  <div class="min-h-screen bg-surface-gray-1 pb-24">
    <header class="border-b border-outline-gray-2 bg-surface-white px-4 py-4">
      <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
        Documents
      </h1>
    </header>

    <div class="space-y-2 px-4 py-4">
      <p
        v-if="documents.loading"
        class="text-ink-gray-5"
      >
        Loading…
      </p>
      <p
        v-else-if="rows.length === 0"
        class="text-ink-gray-5"
      >
        HR hasn't added any documents yet.
      </p>
      <a
        v-for="row in rows"
        :key="row.name"
        :href="row.url"
        target="_blank"
        rel="noopener noreferrer"
        class="block rounded-lg border border-outline-gray-2 bg-surface-white p-4 hover:border-outline-gray-3"
      >
        <p class="font-medium text-ink-gray-9">{{ row.title }}</p>
        <p
          v-if="row.description"
          class="mt-1 text-sm text-ink-gray-6"
        >
          {{ row.description }}
        </p>
      </a>
    </div>
  </div>
</template>
