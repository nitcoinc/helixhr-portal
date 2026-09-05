<script setup>
import { ref, computed } from 'vue'
import { createResource, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { session } from '@/lib/session'

// P2-U8 step 6 / P2-R19 / P2-AE2. One session-scoped read.
//
// This page used to send its own `or_filters` to `frappe.client.get_list`:
// the browser named the company it wanted to see. The server-side scope that
// makes the answer safe landed in P2-U1 (`permission_query_conditions` plus
// `has_permission` on HelixHR Document Link), but as long as the *question*
// came from the browser the page still read as though the filter were the
// boundary. It asks `helixhr.api.get_my_documents` instead, which resolves
// the employee and their company from the session and sends no filter at all.
const documents = createResource({
  url: 'helixhr.api.get_my_documents',
  auto: true,
})

const rows = computed(() => documents.data || [])
const company = computed(() => session.employee?.company)

const query = ref('')

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

/** PDF or link, derived from the address rather than stored: a policy
 * catalogue is a list of URLs, and the one thing the URL reliably says about
 * its target is whether it ends in a document. */
function isPdf(url) {
  try {
    return /\.pdf$/i.test(new URL(url).pathname)
  } catch {
    return false
  }
}

const matches = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return rows.value
  return rows.value.filter((row) =>
    [row.title, row.description, hostOf(row.url)]
      .filter(Boolean)
      .some((field) => field.toLowerCase().includes(needle)),
  )
})

// The canvas groups these two ways round: what everyone gets, then what
// belongs to your company. Grouping is a label above a run of cards, never a
// second surface -- and it ships whatever the row count is, because these two
// groups *are* the permission model P2-R19 enforces, made visible.
const groups = computed(() =>
  [
    { key: 'everyone', label: 'For everyone', rows: matches.value.filter((row) => !row.company) },
    {
      key: 'company',
      label: company.value || 'Your company',
      rows: matches.value.filter((row) => row.company),
    },
  ].filter((group) => group.rows.length),
)

// Where "Ask HR" goes: one request, with the search that found nothing
// already written into its subject.
const askHr = computed(() => ({
  name: 'Requests',
  query: {
    category: 'Other',
    subject: query.value.trim()
      ? `Looking for a document: ${query.value.trim()}`
      : 'Looking for a document',
  },
}))
</script>

<template>
  <div>
    <PageHeader title="Documents" />

    <!-- Search first in the DOM, so a phone gets the control before the
         explanation; `flex-row-reverse` puts it back top-right at lg:, which
         is where the desktop artboard has it. -->
    <div class="mb-4 lg:flex lg:flex-row-reverse lg:items-center lg:justify-between lg:gap-6">
      <div class="lg:w-80 lg:shrink-0">
        <FormControl
          v-model="query"
          type="text"
          label="Search"
          placeholder="Policies and forms"
        />
      </div>
      <p class="mt-3 text-sm text-ink-gray-6 lg:mt-0">
        Policies and forms HR keeps for you. Links open in a new tab. Missing something?
        <router-link
          class="cursor-pointer text-blue-700 underline underline-offset-2"
          :to="askHr"
        >
          Ask HR
        </router-link>.
      </p>
    </div>

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
          :key="group.key"
          :aria-label="group.label"
        >
          <h2 class="label mb-2">
            {{ group.label }}
          </h2>
          <!-- Three columns at desktop widths, one on a phone. Same card
               either way; only how many fit on a line changes. -->
          <ul class="space-y-2 lg:grid lg:grid-cols-3 lg:gap-3 lg:space-y-0">
            <li
              v-for="row in group.rows"
              :key="row.name"
            >
              <a
                :href="row.url"
                target="_blank"
                rel="noopener noreferrer"
                class="surface-card elev-1 flex h-full items-start gap-3 p-3"
              >
                <span
                  class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-green-2 text-ink-green-3"
                  aria-hidden="true"
                >
                  <Icon
                    :name="isPdf(row.url) ? 'requests' : 'documents'"
                    size="h-4 w-4"
                  />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block font-medium text-ink-gray-9">{{ row.title }}</span>
                  <span
                    v-if="row.description"
                    class="block text-sm text-ink-gray-6"
                  >
                    {{ row.description }}
                  </span>
                  <span class="mt-0.5 flex items-center gap-1 text-xs text-ink-gray-5">
                    <Icon
                      name="chevronRight"
                      size="h-3 w-3"
                      class="shrink-0"
                    />
                    {{ hostOf(row.url) || 'Link' }}<template v-if="isPdf(row.url)"> · PDF</template>
                    <span class="sr-only">(opens in a new tab)</span>
                  </span>
                </span>
              </a>
            </li>
          </ul>
        </section>

        <!-- A search that matched nothing is not an empty catalogue, and must
             not borrow the empty state's words (P2-R2). -->
        <p
          v-if="!groups.length"
          class="surface-card p-5 text-sm text-ink-gray-6"
          data-testid="documents-no-match"
        >
          Nothing here matches “{{ query.trim() }}”.
          <router-link
            class="cursor-pointer text-blue-700 underline underline-offset-2"
            :to="askHr"
          >
            Ask HR
          </router-link>
          if it should be.
        </p>
      </div>
    </AsyncState>
  </div>
</template>
