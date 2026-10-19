<script setup>
// Plan 2026-10-19-001 U7 (R17-R23): People & access, elevated first. An
// attention banner, the Elevated access table, then All employees with a
// server search -- each its own AsyncState region that fails on its own.
//
// Opening a person is a route (`/settings/people-access/<user>`), so Back
// closes the editor: beside the lists from lg up, a pushed view below it.
// A dirty draft asks "Discard changes?" before any navigation leaves it,
// and closing returns focus to the row that opened it.
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Dialog, createResource } from 'frappe-ui'
import { call } from '@/lib/api'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'
import AsyncState from '@/components/AsyncState.vue'
import SearchField from '@/components/SearchField.vue'
import ElevatedAccessTable from '@/components/settings/ElevatedAccessTable.vue'
import PersonAccessEditor from '@/components/settings/PersonAccessEditor.vue'

const PERSON_ROUTE = 'SettingsPeopleAccessPerson'
const PAGE = 50

const route = useRoute()
const router = useRouter()
const openUser = computed(() => (route.name === PERSON_ROUTE ? String(route.params.user || '') : ''))

// --- Elevated access -----------------------------------------------------

const elevated = createResource({ url: 'helixhr.api.get_elevated_access', auto: true })
const people = computed(() => elevated.data?.people || [])
const attention = computed(() => elevated.data?.counts?.needs_attention || 0)
const filter = ref('all')
const table = ref(null)
const elevatedHeading = ref(null)

/** The banner sets "Needs attention" and puts focus on the table. */
function showAttention() {
  filter.value = 'attention'
  nextTick(() => table.value?.focus())
}

// --- All employees -------------------------------------------------------

const query = ref('')
const org = ref('')
// A resource-shaped object, so AsyncState's Retry reloads it.
const employees = reactive({
  rows: [],
  hasMore: false,
  companies: [],
  loading: true,
  error: null,
  loadingMore: false,
  moreError: '',
  reload: () => loadEmployees(),
})
let employeesSeq = 0

async function loadEmployees(more = false) {
  const mine = ++employeesSeq
  if (more) {
    employees.loadingMore = true
    employees.moreError = ''
  } else {
    employees.loading = true
    employees.error = null
  }
  try {
    const page = await call('helixhr.api.search_people_access', {
      query: query.value.trim() || undefined,
      company: org.value || undefined,
      start: more ? employees.rows.length : 0,
      page_length: PAGE,
    })
    if (mine !== employeesSeq) return
    employees.rows = more ? [...employees.rows, ...page.rows] : page.rows
    employees.hasMore = page.has_more
    employees.companies = page.companies || []
  } catch (failure) {
    if (mine !== employeesSeq) return
    if (more) employees.moreError = "Couldn't load more people. Try again."
    else employees.error = failure
  } finally {
    if (mine === employeesSeq) {
      employees.loading = false
      employees.loadingMore = false
    }
  }
}

// The debounce spaces out keystrokes; `loadEmployees` keeps its own
// sequence, so an older answer never overwrites a newer one.
const debounced = useDebouncedSearch(() => loadEmployees())
watch(query, () => {
  employees.loading = true
  debounced.search()
})
watch(org, () => loadEmployees())
loadEmployees()

// --- the editor, as a route ----------------------------------------------

const editorDirty = ref(false)
const discardOpen = ref(false)
let discardAnswer = null

function askDiscard() {
  discardOpen.value = true
  return new Promise((resolve) => {
    discardAnswer = resolve
  })
}
function answerDiscard(yes) {
  const resolve = discardAnswer
  discardAnswer = null
  discardOpen.value = false
  resolve?.(yes)
}
// Escape, the close button or a tap outside: keep editing.
watch(discardOpen, (open) => {
  if (!open && discardAnswer) answerDiscard(false)
})

// Back, Close, opening another person or leaving Settings all pass here.
const removeGuard = router.beforeEach(async (to, from) => {
  if (!editorDirty.value || from.name !== PERSON_ROUTE) return true
  if (to.name === PERSON_ROUTE && to.params.user === from.params.user) return true
  const discard = await askDiscard()
  if (discard) editorDirty.value = false
  return discard
})
onBeforeUnmount(removeGuard)

function openPerson(user) {
  if (user === openUser.value) return
  router.push({ name: PERSON_ROUTE, params: { user } })
}

function closeEditor() {
  // Opened from the list: going back is the same as Back. Opened from a
  // link or a reload: there is no list behind it to go back to.
  const back = String(window.history.state?.back || '').split('?')[0]
  if (/^\/settings(\/people-access)?\/?$/.test(back)) router.back()
  else router.push({ name: 'SettingsSection', params: { section: 'people-access' } })
}

function focusOpener(user) {
  const target = [...document.querySelectorAll('[data-person-open]')].find(
    (element) => element.dataset.personOpen === user && element.offsetParent !== null,
  )
  if (target) target.focus()
  else elevatedHeading.value?.focus()
}
watch(openUser, (now, before) => {
  if (!now) editorDirty.value = false
  if (!now && before) nextTick(() => focusOpener(before))
})

function onSaved() {
  elevated.reload()
}
</script>

<template>
  <section
    aria-label="People &amp; access"
    :class="openUser ? 'lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start lg:gap-5' : ''"
  >
    <!-- The lists stay mounted under an open person, so filters, loaded
         pages and the opening row survive the round trip. -->
    <div
      class="space-y-8"
      :class="openUser ? 'hidden lg:block' : ''"
      data-testid="people-access-lists"
    >
      <template v-if="elevated.data && !elevated.loading && !elevated.error">
        <button
          v-if="attention > 0"
          type="button"
          class="surface-alert flex min-h-11 w-full cursor-pointer items-center justify-between gap-3 p-3 text-left text-sm font-medium"
          data-testid="access-banner"
          @click="showAttention"
        >
          <span>{{ attention }} {{ attention === 1 ? 'person needs' : 'people need' }} attention</span>
          <span class="underline underline-offset-2">Show them</span>
        </button>
        <p
          v-else
          class="surface-inset p-3 text-sm text-ink-gray-7"
          data-testid="access-banner"
        >
          Nothing needs attention
        </p>
      </template>

      <section aria-labelledby="elevated-access-heading">
        <h2
          id="elevated-access-heading"
          ref="elevatedHeading"
          tabindex="-1"
          class="type-section mb-3 text-ink-gray-9 focus:outline-none"
        >
          Elevated access
        </h2>
        <AsyncState
          section="elevated-access"
          :resource="elevated"
          :empty="people.length === 0"
          empty-title="No one has elevated access in your orgs."
          skeleton="row"
          :skeleton-rows="4"
        >
          <ElevatedAccessTable
            ref="table"
            v-model:filter="filter"
            :people="people"
            :selected="openUser"
            :compact="!!openUser"
            @open="openPerson"
          />
        </AsyncState>
      </section>

      <section aria-labelledby="all-employees-heading">
        <h2
          id="all-employees-heading"
          class="type-section mb-3 text-ink-gray-9"
        >
          All employees
        </h2>
        <div class="mb-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
          <SearchField
            v-model="query"
            label="Search name or ID"
            placeholder="Name, employee ID or email"
            autocomplete="off"
            maxlength="80"
            data-testid="employees-search"
          />
          <label>
            <span class="mb-1 block text-sm text-ink-gray-7">Org</span>
            <select
              v-model="org"
              class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 text-sm text-ink-gray-8"
              data-testid="employees-org"
            >
              <option value="">All my orgs</option>
              <option
                v-for="company in employees.companies"
                :key="company"
                :value="company"
              >{{ company }}</option>
            </select>
          </label>
        </div>
        <AsyncState
          section="all-employees"
          :resource="employees"
          :empty="employees.rows.length === 0"
          empty-title="No one matches."
          skeleton="row"
          :skeleton-rows="5"
          skeleton-height="h-12"
        >
          <ul
            class="surface-card divide-y divide-outline-gray-1"
            data-testid="employees-list"
          >
            <li
              v-for="row in employees.rows"
              :key="row.employee"
              class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
              :class="openUser && openUser === row.user ? 'bg-surface-gray-2' : ''"
              :data-testid="`employee-row-${row.employee}`"
            >
              <!-- Without a login there is no access to give: no editor. -->
              <button
                v-if="row.has_login && row.user"
                type="button"
                class="-my-2 inline-flex min-h-11 min-w-0 cursor-pointer items-center text-left font-medium text-ink-gray-9 hover:underline"
                :aria-current="openUser === row.user ? 'true' : undefined"
                :data-person-open="row.user"
                :data-testid="`employee-open-${row.user}`"
                @click="openPerson(row.user)"
              >
                {{ row.employee_name }}
              </button>
              <span
                v-else
                class="font-medium text-ink-gray-9"
              >{{ row.employee_name }}</span>
              <span class="text-sm text-ink-gray-6">{{ row.employee }} · {{ row.company }}</span>
              <span
                v-if="!row.has_login"
                class="ml-auto rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
                data-testid="employee-no-login"
              >No login</span>
            </li>
          </ul>
          <p
            v-if="employees.moreError"
            class="mt-2 text-sm text-ink-red-4"
            role="alert"
          >
            {{ employees.moreError }}
          </p>
          <Button
            v-if="employees.hasMore"
            variant="ghost"
            class="mt-2 min-h-11"
            :loading="employees.loadingMore"
            data-testid="employees-more"
            @click="loadEmployees(true)"
          >
            Show more
          </Button>
        </AsyncState>
      </section>
    </div>

    <div
      v-if="openUser"
      class="lg:sticky lg:top-4"
    >
      <Button
        variant="ghost"
        class="mb-2 min-h-11"
        data-testid="person-access-close"
        @click="closeEditor"
      >
        <span class="lg:hidden">Back to People &amp; access</span>
        <span class="hidden lg:inline">Close</span>
      </Button>
      <PersonAccessEditor
        :key="openUser"
        :user="openUser"
        @dirty="editorDirty = $event"
        @saved="onSaved"
      />
    </div>

    <Dialog
      v-model="discardOpen"
      :options="{ title: 'Discard changes?', size: 'sm' }"
    >
      <template #body-content>
        <div data-testid="access-discard-dialog">
          <p class="text-sm text-ink-gray-7">
            Your changes to this person's access are not saved.
          </p>
          <div class="mt-4 flex flex-wrap gap-2">
            <Button
              variant="solid"
              theme="red"
              class="min-h-11"
              data-testid="access-discard-confirm"
              @click="answerDiscard(true)"
            >
              Discard changes
            </Button>
            <Button
              variant="subtle"
              class="min-h-11"
              @click="answerDiscard(false)"
            >
              Keep editing
            </Button>
          </div>
        </div>
      </template>
    </Dialog>
  </section>
</template>
