<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { createResource, Button, Dialog, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import { useIsDesktop } from '@/lib/useIsDesktop'

// P3-U8 / P3-R22, P3-R23. Find a colleague: their role, their department,
// their manager and the work email HR published for them.
//
// The search and the department chip are *server* questions -- the list is
// paged (P3-R25), so filtering the rows already in the browser would filter
// one page rather than the company. Nothing on this page is derived from a
// filter the browser invents: `get_directory` resolves the reader's company
// from the session, and a chip only ever names a department the server just
// sent (P3-KTD1).
const isDesktop = useIsDesktop()

const PAGE = 50
const MAX_PAGE = 200

const query = ref('')
const search = ref('')
const department = ref(null)
const pageLimit = ref(PAGE)

const directory = createResource({
  url: 'helixhr.api.get_directory',
  makeParams: () => ({
    query: search.value || undefined,
    department: department.value || undefined,
    limit: pageLimit.value,
  }),
  auto: true,
})

// Typing is not a request per keystroke: the field stays instant and the
// server hears one question when the person stops typing.
let pending = null
watch(query, (value) => {
  clearTimeout(pending)
  pending = setTimeout(() => {
    search.value = value.trim()
    pageLimit.value = PAGE
    directory.reload()
  }, 250)
})
onUnmounted(() => clearTimeout(pending))

const rows = computed(() => directory.data?.people || [])
const total = computed(() => directory.data?.total || 0)
const departments = computed(() => directory.data?.departments || [])
const moreCount = computed(() => Math.max(0, total.value - rows.value.length))

function showMore() {
  pageLimit.value = Math.min(MAX_PAGE, pageLimit.value + PAGE)
  directory.reload()
}

function filterByDepartment(value) {
  department.value = value
  pageLimit.value = PAGE
  directory.reload()
}

/** Two letters at most, the same monogram the Approvals queue and the leave
 * form use. No photos here: a face is personal data the directory does not
 * need to answer "who do I ask about payroll" (P3-R22). */
function initials(name) {
  return (name || '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('')
}

/** One run of cards per department, in the order the server sent the people
 * (by name). "No department" is a real group, not a gap: an employee HR has
 * not filed yet is still a colleague you may need to find. */
const groups = computed(() => {
  const byDepartment = new Map()
  for (const person of rows.value) {
    const key = person.department || 'No department'
    if (!byDepartment.has(key)) byDepartment.set(key, [])
    byDepartment.get(key).push(person)
  }
  return [...byDepartment.entries()].map(([label, people]) => ({ label, people }))
})

// --- the open person ---------------------------------------------------

// The phone shape of one row. Not a route: the directory is a lookup, and
// `/directory/<employee id>` would publish an employee id in a URL people
// paste to each other -- the id is in the payload because the manager link
// needs it, and that is as far as it goes.
const openName = ref('')
const selected = computed(() => rows.value.find((person) => person.name === openName.value) || null)
const sheetOpen = computed(() => !!selected.value && !isDesktop.value)

function openPerson(person) {
  // On a phone the row opens the sheet; at desktop widths the card already
  // shows everything the sheet would, so tapping it does nothing.
  if (!isDesktop.value) openName.value = person.name
}

function closePerson() {
  openName.value = ''
}

/** Whether the manager is somebody the reader can be shown. The directory is
 * paged and company-scoped, so a manager in another company, or one who has
 * left, is a name and not a link. */
function managerRow(person) {
  return rows.value.find((row) => row.name === person.manager) || null
}

/** Follow the manager link. On a phone the sheet swaps to them in place; at
 * desktop widths the search box does the moving, because their card may be
 * further down a three-column grid than a scroll would help with. */
function openManager(person) {
  const manager = managerRow(person)
  if (!manager) return
  if (isDesktop.value) {
    query.value = manager.employee_name
    closePerson()
  } else {
    openName.value = manager.name
  }
}

const emptyTitle = computed(() =>
  search.value || department.value ? 'Nobody matches that search' : 'Nobody else here yet',
)
const emptyBody = computed(() =>
  search.value || department.value
    ? 'Try a first name, a role or a department. The directory only covers your own company.'
    : 'Colleagues in your company appear here once HR has added them. Ask HR if somebody is missing.',
)
</script>

<template>
  <div>
    <PageHeader title="Directory" />

    <!-- Search first in the DOM, so a phone gets the control before the
         explanation (the same order Documents uses). -->
    <div class="mb-4 lg:flex lg:flex-row-reverse lg:items-center lg:justify-between lg:gap-6">
      <div class="lg:w-80 lg:shrink-0">
        <FormControl
          v-model="query"
          type="text"
          label="Search"
          placeholder="Name, role or department"
          maxlength="60"
        />
      </div>
      <p class="mt-3 text-sm text-ink-gray-6 lg:mt-0">
        Colleagues in your company, with their role, department and manager. Work email is there
        when HR has published one.
      </p>
    </div>

    <!-- Department chips. Desktop only: a phone has the search field and no
         room for a second filter beside it, and the chips would push the
         first colleague below the fold. -->
    <div
      v-if="departments.length"
      class="mb-4 hidden flex-wrap items-center gap-2 lg:flex"
      role="group"
      aria-label="Filter the directory by department"
    >
      <button
        type="button"
        class="min-h-11 rounded-full border px-4 text-sm font-medium"
        :class="
          department === null
            ? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
            : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2'
        "
        :aria-pressed="department === null"
        @click="filterByDepartment(null)"
      >
        Everyone
      </button>
      <button
        v-for="chip in departments"
        :key="chip.name"
        type="button"
        class="min-h-11 rounded-full border px-4 text-sm font-medium"
        :class="
          department === chip.name
            ? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
            : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2'
        "
        :aria-pressed="department === chip.name"
        @click="filterByDepartment(chip.name)"
      >
        {{ chip.name }}
        <span class="tabular text-ink-gray-5">{{ chip.count }}</span>
      </button>
    </div>

    <AsyncState
      section="directory"
      :resource="directory"
      :empty="rows.length === 0"
      :empty-title="emptyTitle"
      :empty-body="emptyBody"
      skeleton="row"
      :skeleton-rows="6"
    >
      <template #error-title>
        We couldn't load the directory
      </template>

      <div
        v-for="group in groups"
        :key="group.label"
        class="mb-6 last:mb-0"
      >
        <h2 class="label mb-2">
          {{ group.label }}
        </h2>
        <!-- Three columns at desktop widths, one on a phone. Same card
             either way; only how many fit on a line changes. -->
        <ul class="space-y-2 lg:grid lg:grid-cols-3 lg:gap-3 lg:space-y-0">
          <li
            v-for="person in group.people"
            :key="person.name"
            class="surface-card elev-1 h-full"
          >
            <button
              type="button"
              class="flex w-full min-w-0 items-center gap-3 p-3 text-left lg:cursor-default"
              :aria-expanded="!isDesktop ? person.name === openName : undefined"
              @click="openPerson(person)"
            >
              <span
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                aria-hidden="true"
              >{{ initials(person.employee_name) }}</span>

              <span class="min-w-0 flex-1">
                <span class="block truncate font-medium text-ink-gray-9">
                  {{ person.employee_name }}
                </span>
                <span class="block truncate text-sm text-ink-gray-6">
                  {{ person.designation || 'Role not published' }}
                </span>
              </span>

              <Icon
                v-if="!isDesktop"
                name="chevronRight"
                class="shrink-0 text-ink-gray-4"
              />
            </button>

            <!-- The rest of the card, at desktop widths. On a phone the same
                 three facts arrive in the sheet instead. -->
            <div
              v-if="isDesktop"
              class="border-t border-outline-gray-1 px-3 py-2 text-sm text-ink-gray-6"
            >
              <p v-if="person.department">
                {{ person.department }}
              </p>
              <p v-if="person.manager_name">
                Reports to
                <button
                  v-if="managerRow(person)"
                  type="button"
                  class="cursor-pointer text-blue-700 underline underline-offset-2"
                  @click="openManager(person)"
                >
                  {{ person.manager_name }}
                </button>
                <template v-else>
                  {{ person.manager_name }}
                </template>
              </p>
              <a
                v-if="person.email"
                class="mt-1 inline-flex min-h-11 items-center text-blue-700 underline underline-offset-2"
                :href="`mailto:${person.email}`"
              >
                {{ person.email }}
              </a>
            </div>
          </li>
        </ul>
      </div>

      <div
        v-if="moreCount"
        class="mt-4 text-center"
      >
        <Button
          variant="ghost"
          :loading="directory.loading"
          @click="showMore"
        >
          Show {{ moreCount }} more
        </Button>
      </div>
    </AsyncState>

    <!-- The phone shape of one row: the same four facts, on a surface a
         thumb can reach. reka-ui under frappe-ui's Dialog supplies the focus
         trap, Escape and focus restoration (P2-R6). -->
    <Dialog
      :model-value="sheetOpen"
      :options="{ title: selected?.employee_name || 'Colleague', size: 'sm' }"
      @update:model-value="(open) => !open && closePerson()"
    >
      <template #body-content>
        <div
          v-if="selected"
          class="space-y-3"
        >
          <div class="flex items-center gap-3">
            <span
              class="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-base font-bold text-ink-green-3"
              aria-hidden="true"
            >{{ initials(selected.employee_name) }}</span>
            <div class="min-w-0">
              <p class="font-medium text-ink-gray-9">
                {{ selected.employee_name }}
              </p>
              <p class="text-sm text-ink-gray-6">
                {{ selected.designation || 'Role not published' }}
              </p>
            </div>
          </div>

          <dl class="surface-inset space-y-2 p-3 text-sm">
            <div class="flex justify-between gap-3">
              <dt class="text-ink-gray-6">
                Department
              </dt>
              <dd class="text-right text-ink-gray-9">
                {{ selected.department || '—' }}
              </dd>
            </div>
            <div class="flex justify-between gap-3">
              <dt class="text-ink-gray-6">
                Manager
              </dt>
              <dd class="text-right text-ink-gray-9">
                <button
                  v-if="managerRow(selected)"
                  type="button"
                  class="cursor-pointer text-blue-700 underline underline-offset-2"
                  @click="openManager(selected)"
                >
                  {{ selected.manager_name }}
                </button>
                <template v-else>
                  {{ selected.manager_name || '—' }}
                </template>
              </dd>
            </div>
          </dl>

          <a
            v-if="selected.email"
            class="flex min-h-11 items-center justify-center rounded-full bg-surface-gray-3 px-5 text-sm font-bold text-ink-gray-9"
            :href="`mailto:${selected.email}`"
          >
            Email {{ selected.employee_name.split(/\s+/)[0] }}
          </a>
          <!-- No work email is a gap in HR's record, not a broken page, and
               it says so rather than offering a dead button (P3-R22). -->
          <p
            v-else
            class="text-sm text-ink-gray-5"
          >
            No work email published. Ask HR if you need to reach them.
          </p>
        </div>
      </template>
    </Dialog>
  </div>
</template>
