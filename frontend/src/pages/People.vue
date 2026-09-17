<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { formatDate } from '@/lib/dates'
import { session } from '@/lib/session'

// P6-U5 / P6-R1, P6-R2, P6-R13. Find a person, then read them.
//
// `search_people` and `get_person` are the server's own gates
// (`resolve_admin_scope`) -- a non-HR caller hitting either route directly
// gets AsyncState's 'forbidden' region below, not a client-side redirect
// (the same posture Organisation.vue and Settings.vue already take).
//
// The person's record is addressable by URL (`/people/:employee`), matching
// the P2-R12 rule the rest of the portal follows, so HR can send a colleague
// a link and a reload lands on the same person.
const props = defineProps({
  employee: { type: String, default: '' },
})
const router = useRouter()

// --- search --------------------------------------------------------------

const PAGE = 50
const MAX_PAGE = 200

const query = ref('')
const search = ref('')
const pageLimit = ref(PAGE)

const results = createResource({
  url: 'helixhr.api.search_people',
  makeParams: () => ({ query: search.value || undefined, limit: pageLimit.value }),
  auto: true,
})

let pending = null
watch(query, (value) => {
  clearTimeout(pending)
  pending = setTimeout(() => {
    search.value = value.trim()
    pageLimit.value = PAGE
    results.reload()
  }, 250)
})
onUnmounted(() => clearTimeout(pending))

const rows = computed(() => results.data?.people || [])
const total = computed(() => results.data?.total || 0)
const moreCount = computed(() => Math.max(0, total.value - rows.value.length))

function showMore() {
  pageLimit.value = Math.min(MAX_PAGE, pageLimit.value + PAGE)
  results.reload()
}

function openPerson(person) {
  router.push(`/people/${person.name}`)
}

// --- the person view -------------------------------------------------------

const person = createResource({
  url: 'helixhr.api.get_person',
  makeParams: () => ({ employee: props.employee }),
  auto: false,
})

watch(
  () => props.employee,
  (employee) => {
    if (employee) person.reload()
  },
  { immediate: true },
)

const profile = computed(() => person.data?.employee || null)
const leaveBalances = computed(() => person.data?.leave_balances || [])
const attendance = computed(() => person.data?.attendance || null)
const requests = computed(() => person.data?.requests?.requests || [])
const failedSections = computed(() => person.data?.failed_sections || [])

function sectionFailed(name) {
  return failedSections.value.includes(name)
}
</script>

<template>
  <div>
    <!-- The person view. Reached at /people/:employee. -->
    <template v-if="employee">
      <PageHeader
        :title="profile?.employee_name || 'Person'"
        :subtitle="[profile?.designation, profile?.department].filter(Boolean).join(' · ')"
      >
        <template #actions>
          <Button
            variant="outline"
            @click="router.push('/people')"
          >
            Back to search
          </Button>
          <a
            v-if="person.data?.desk_url"
            :href="person.data.desk_url"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex min-h-11 items-center rounded-lg border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2"
          >
            Open in Desk
          </a>
        </template>
      </PageHeader>

      <AsyncState
        section="person"
        :resource="person"
        :empty="false"
        skeleton="block"
        skeleton-height="h-96"
      >
        <div
          v-if="profile"
          class="space-y-6"
        >
          <section class="surface-card elev-1 grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <p class="text-sm text-ink-gray-6">
                Status
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ profile.status }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Joined
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ profile.date_of_joining ? formatDate(profile.date_of_joining) : '—' }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Manager
              </p>
              <p class="font-medium text-ink-gray-9">
                {{ profile.manager_name || '—' }}
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Shift
              </p>
              <p
                v-if="!sectionFailed('shift')"
                class="font-medium text-ink-gray-9"
              >
                {{ person.data?.shift || 'No shift assigned' }}
              </p>
              <p
                v-else
                class="text-sm text-ink-gray-5"
              >
                Couldn't load this
              </p>
            </div>
            <div>
              <p class="text-sm text-ink-gray-6">
                Holiday list
              </p>
              <p
                v-if="!sectionFailed('holiday_list')"
                class="font-medium text-ink-gray-9"
              >
                {{ person.data?.holiday_list || 'None resolved' }}
              </p>
              <p
                v-else
                class="text-sm text-ink-gray-5"
              >
                Couldn't load this
              </p>
            </div>
          </section>

          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="person-leave-heading"
          >
            <div class="flex items-center justify-between">
              <h2
                id="person-leave-heading"
                class="font-heading text-base font-semibold text-ink-gray-9"
              >
                Leave balance
              </h2>
              <router-link
                v-if="session.canOpenDesk"
                class="text-sm text-blue-700 underline underline-offset-2"
                :to="{ name: 'Reports', query: { employee } }"
              >
                Leave reports
              </router-link>
            </div>
            <p
              v-if="sectionFailed('leave_balances')"
              class="mt-2 text-sm text-ink-gray-5"
            >
              Couldn't load this
            </p>
            <ul
              v-else-if="leaveBalances.length"
              class="mt-2 divide-y divide-outline-gray-1"
            >
              <li
                v-for="balance in leaveBalances"
                :key="balance.leave_type"
                class="flex items-center justify-between gap-3 py-2"
              >
                <span class="text-sm text-ink-gray-7">{{ balance.leave_type }}</span>
                <span class="tabular font-heading text-lg font-semibold text-ink-gray-9">
                  {{ balance.left }}
                </span>
              </li>
            </ul>
            <p
              v-else
              class="mt-2 text-sm text-ink-gray-6"
            >
              No leave type allocated.
            </p>
          </section>

          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="person-attendance-heading"
          >
            <h2
              id="person-attendance-heading"
              class="font-heading text-base font-semibold text-ink-gray-9"
            >
              Attendance this month
            </h2>
            <p
              v-if="sectionFailed('attendance')"
              class="mt-2 text-sm text-ink-gray-5"
            >
              Couldn't load this
            </p>
            <ul
              v-else-if="attendance && Object.keys(attendance.summary || {}).length"
              class="mt-2 flex flex-wrap gap-4"
            >
              <li
                v-for="(count, status) in attendance.summary"
                :key="status"
                class="text-sm text-ink-gray-7"
              >
                <span class="tabular font-heading text-lg font-semibold text-ink-gray-9">{{ count }}</span>
                {{ status }}
              </li>
            </ul>
            <p
              v-else
              class="mt-2 text-sm text-ink-gray-6"
            >
              Nothing recorded yet this month.
            </p>
          </section>

          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="person-requests-heading"
          >
            <h2
              id="person-requests-heading"
              class="font-heading text-base font-semibold text-ink-gray-9"
            >
              Open and recent requests
            </h2>
            <p
              v-if="sectionFailed('requests')"
              class="mt-2 text-sm text-ink-gray-5"
            >
              Couldn't load this
            </p>
            <ul
              v-else-if="requests.length"
              class="mt-2 divide-y divide-outline-gray-1"
            >
              <li
                v-for="row in requests"
                :key="row.name"
                class="py-2 text-sm text-ink-gray-7"
              >
                <span class="font-medium text-ink-gray-9">{{ row.subject }}</span>
                <span class="ml-2 text-ink-gray-5">{{ row.status }}</span>
              </li>
            </ul>
            <p
              v-else
              class="mt-2 text-sm text-ink-gray-6"
            >
              No requests from this person.
            </p>
          </section>
        </div>
      </AsyncState>
    </template>

    <!-- Search. Reached at /people. -->
    <template v-else>
      <PageHeader
        title="People"
        subtitle="Find a colleague to see their leave, attendance and requests."
      />

      <div class="mb-4 lg:w-80">
        <FormControl
          v-model="query"
          type="text"
          label="Search"
          placeholder="Name, employee number or work email"
          maxlength="60"
        />
      </div>

      <AsyncState
        section="people"
        :resource="results"
        :empty="rows.length === 0"
        empty-title="Nobody matches that search"
        empty-body="Try a first name, an employee number or a work email."
        skeleton="row"
        :skeleton-rows="6"
      >
        <ul class="space-y-2">
          <li
            v-for="row in rows"
            :key="row.name"
          >
            <button
              type="button"
              class="surface-card elev-1 flex w-full min-w-0 items-center gap-3 p-3 text-left"
              @click="openPerson(row)"
            >
              <span
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                aria-hidden="true"
              >{{ row.initials }}</span>
              <span class="min-w-0 flex-1">
                <span class="block truncate font-medium text-ink-gray-9">{{ row.employee_name }}</span>
                <span class="block truncate text-sm text-ink-gray-6">
                  {{ [row.employee_number, row.designation, row.company].filter(Boolean).join(' · ') }}
                </span>
              </span>
              <Icon
                name="chevronRight"
                class="shrink-0 text-ink-gray-4"
              />
            </button>
          </li>
        </ul>

        <div
          v-if="moreCount"
          class="mt-4 text-center"
        >
          <Button
            variant="ghost"
            :loading="results.loading"
            @click="showMore"
          >
            Show {{ moreCount }} more
          </Button>
        </div>
      </AsyncState>
    </template>
  </div>
</template>
