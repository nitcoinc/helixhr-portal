<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { session } from '@/lib/session'

// P2-U3 / P2-R21. The company comes from the one bootstrap, not from a
// page-local `hrms.api.get_current_employee_info`.
const company = computed(() => session.employee?.company)

// R23: company-less links (for everyone) plus links for the employee's
// own company -- "or_filters" is needed because that's an OR across two
// conditions on the same field, which a plain equality filters list
// can't express. The server-side scope that makes this safe rather than
// cosmetic landed in P2-U1.
const documents = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => ({
    doctype: 'HelixHR Document Link',
    fields: ['name', 'title', 'url', 'description', 'company'],
    or_filters: [
      ['company', 'is', 'not set'],
      ['company', '=', company.value],
    ],
    order_by: 'title asc',
    limit_page_length: 0,
  }),
  auto: true,
})

const rows = computed(() => documents.data || [])

// The canvas groups these two ways round: what everyone gets, then what
// belongs to your company. Grouping is a label above a run of cards, never a
// second surface.
const groups = computed(() => [
  { label: 'For everyone', rows: rows.value.filter((row) => !row.company) },
  { label: company.value || 'Your company', rows: rows.value.filter((row) => row.company) },
])

/** The host, so a link says where it is about to send you. Anything that
 * does not parse is shown as nothing rather than as a broken string --
 * P2-U1 already refuses non-HTTP(S) schemes at the server. */
function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}
</script>

<template>
  <div>
    <PageHeader title="Documents" />

    <AsyncState
      section="documents"
      :resource="documents"
      :empty="rows.length === 0"
      empty-title="No documents yet"
      empty-body="HR adds handbooks, policies and forms here. Ask HR if you're looking for something."
      skeleton="row"
      :skeleton-rows="4"
    >
      <div class="space-y-6">
        <section
          v-for="group in groups"
          v-show="group.rows.length"
          :key="group.label"
        >
          <h2 class="label mb-2">
            {{ group.label }}
          </h2>
          <ul class="space-y-2">
            <li
              v-for="row in group.rows"
              :key="row.name"
            >
              <a
                :href="row.url"
                target="_blank"
                rel="noopener noreferrer"
                class="surface-card elev-1 flex items-center gap-3 p-3"
              >
                <span
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-gray-2 text-ink-gray-6"
                  aria-hidden="true"
                >
                  <Icon
                    name="documents"
                    size="h-4 w-4"
                  />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block font-medium text-ink-gray-9">{{ row.title }}</span>
                  <span
                    v-if="row.description"
                    class="block truncate text-sm text-ink-gray-6"
                  >
                    {{ row.description }}
                  </span>
                  <span
                    v-if="hostOf(row.url)"
                    class="block text-xs text-ink-gray-5"
                  >
                    {{ hostOf(row.url) }}
                    <span class="sr-only">(opens in a new tab)</span>
                  </span>
                </span>
                <Icon
                  name="chevronRight"
                  size="h-4 w-4"
                  class="shrink-0 text-ink-gray-4"
                />
              </a>
            </li>
          </ul>
        </section>
      </div>
    </AsyncState>
  </div>
</template>
