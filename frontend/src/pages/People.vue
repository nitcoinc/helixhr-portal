<script setup>
import Avatar from '@/components/Avatar.vue'
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Dialog, Button, FormControl } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import Icon from '@/components/Icon.vue'
import SearchField from '@/components/SearchField.vue'
import SegmentedChips from '@/components/SegmentedChips.vue'
import { formatDate } from '@/lib/dates'
import { employeeNumberAddsNothing, fieldOptions, filterByField, sortRows } from '@/lib/lists'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'
import { useIsDesktop } from '@/lib/useIsDesktop'
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
//
// Plan 2026-10-17-001 U17 (HR-14): one request at the server's own cap, 200
// rows -- the stated headcount -- so the department chips, the count and the
// table's sort cover everyone the search matched, all client-side. Past 200
// the count line says only the first 200 are loaded and a search narrows it.
// The server returns Active employees only, so there is no status filter to
// offer here.
const LIMIT = 200

const query = ref('')
const search = ref('')

const results = createResource({
  url: 'helixhr.api.search_people',
  makeParams: () => ({ query: search.value || undefined, limit: LIMIT }),
  auto: true,
})

const debounced = useDebouncedSearch((value) => {
  search.value = value.trim()
  results.reload()
})
watch(query, debounced.search)

const rows = computed(() => results.data?.people || [])
// MH-11: a refused caller gets AsyncState's refusal, not a search box that
// can never return anything.
const forbidden = computed(() => /PermissionError/.test(results.error?.exc_type || ''))
const total = computed(() => results.data?.total || 0)

const isDesktop = useIsDesktop()

const department = ref(null)
const departmentOptions = computed(() =>
  fieldOptions(rows.value, 'department', { blankLabel: 'No department' }),
)
// A search that leaves the chosen department with nobody in it goes back to
// All, rather than an empty list under a chip that is no longer drawn.
watch(departmentOptions, (options) => {
  if (department.value !== null && !options.some((option) => option.value === department.value)) {
    department.value = null
  }
})

// The table's sort, in the same aria-sort shape as ReportTable. Text starts
// A-Z; the joining date starts newest first. The server already sends rows
// by name, so that is the starting state.
const FIRST_ORDER = { employee_name: 'asc', department: 'asc', date_of_joining: 'desc' }
const sort = ref({ field: 'employee_name', order: 'asc' })

function ariaSort(field) {
  if (sort.value.field !== field) return 'none'
  return sort.value.order === 'desc' ? 'descending' : 'ascending'
}

function sortArrow(field) {
  return { ascending: '↑', descending: '↓' }[ariaSort(field)] || ''
}

function toggleSort(field) {
  const current = sort.value.field === field ? sort.value.order : null
  const order = current ? (current === 'asc' ? 'desc' : 'asc') : FIRST_ORDER[field]
  sort.value = { field, order }
}

const shown = computed(() =>
  sortRows(filterByField(rows.value, 'department', department.value), sort.value.field, sort.value.order),
)

// HR-18: the company says nothing when every row has the same one, and an
// employee number that only repeats the name says nothing either.
const manyCompanies = computed(() => new Set(rows.value.map((row) => row.company)).size > 1)

function personNumber(row) {
  return employeeNumberAddsNothing(row) ? '' : row.employee_number
}

function cardLine(row) {
  return [personNumber(row), row.designation, manyCompanies.value ? row.company : null]
    .filter(Boolean)
    .join(' · ')
}

function peopleCount(count) {
  return `${count} ${count === 1 ? 'person' : 'people'}`
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
// Plan 2026-10-14-001 KTD4: someone read through a company access grant is
// view-only. `save_person` refuses them too; this only hides what it would.
const canEdit = computed(() => !!person.data?.can_edit)

function sectionFailed(name) {
  return failedSections.value.includes(name)
}

// --- editing (P8-U7/U8/U9) -------------------------------------------------
//
// Reverses P6-R3's read-only posture deliberately (KTD3): the person view's
// three overview/joining/approver-and-shift cards each get an Edit button
// behind a dialog, matching the pattern Settings' Leave Type editor already
// takes (P8-U5) -- so an edit never feels different depending on which
// screen it's on.
//
// One `form` object and one `save_person` resource cover all three cards;
// `CARD_FIELDS` says which of `form`'s keys a given card actually submits,
// so opening "Overview" and saving never touches "Joining details"' or
// "Approvers and shift"'s fields even though they all live in the same
// reactive object.
const CARD_FIELDS = {
  overview: ['designation', 'department', 'branch', 'company_email'],
  joining: [
    'date_of_joining',
    'employment_type',
    'grade',
    'scheduled_confirmation_date',
    'final_confirmation_date',
    'status',
  ],
  // The three approvers are not here: they follow `reports_to` on the
  // server (plan 2026-10-07-001) and are never typed.
  approvers: ['reports_to', 'default_shift', 'holiday_list'],
}

// Why the approvers are empty, keyed by `get_person`'s `approver_problem`.
const APPROVER_PROBLEM_TEXT = {
  no_manager: 'No approver: no manager is set. Pick a manager to route leave, expense and shift requests.',
  manager_not_active: "No approver: the manager isn't active. Pick an active manager, or reactivate them.",
  manager_no_login: 'No approver: the manager has no portal login. Link a user to their employee record in Desk.',
}

// Employee.status's own Select options (erpnext/setup/doctype/employee/
// employee.json) -- small and stable enough on ERPNext's own core doctype
// to name directly, the same way LeaveTypesSection names its own fixed
// checkbox set rather than fetching it.
const STATUS_OPTIONS = ['Active', 'Inactive', 'Suspended', 'Left']

const editingCard = ref(null)
const form = reactive({
  designation: '',
  department: '',
  branch: '',
  company_email: '',
  date_of_joining: '',
  employment_type: '',
  grade: '',
  scheduled_confirmation_date: '',
  final_confirmation_date: '',
  status: '',
  reports_to: '',
  default_shift: '',
  holiday_list: '',
})
const editError = ref('')

const formOptions = createResource({
  url: 'helixhr.api.get_person_form_options',
  makeParams: () => ({ employee: props.employee }),
  auto: false,
})
const savePerson = createResource({ url: 'helixhr.api.save_person', method: 'POST' })

const editDialogOpen = computed({
  get: () => editingCard.value !== null,
  set: (value) => {
    if (!value) closeEdit()
  },
})
const editDialogTitle = computed(
  () =>
    ({
      overview: 'Edit overview',
      joining: 'Edit joining details',
      approvers: 'Edit approvers and shift',
    })[editingCard.value] || '',
)

function openEdit(card) {
  editingCard.value = card
  editError.value = ''
  Object.assign(form, {
    designation: profile.value.designation || '',
    department: profile.value.department || '',
    branch: profile.value.branch || '',
    company_email: profile.value.company_email || '',
    date_of_joining: profile.value.date_of_joining || '',
    employment_type: profile.value.employment_type || '',
    grade: profile.value.grade || '',
    scheduled_confirmation_date: profile.value.scheduled_confirmation_date || '',
    final_confirmation_date: profile.value.final_confirmation_date || '',
    status: profile.value.status || '',
    reports_to: profile.value.reports_to || '',
    default_shift: profile.value.default_shift || '',
    holiday_list: profile.value.holiday_list || '',
  })
  if (!formOptions.data) formOptions.reload()
}

function closeEdit() {
  editingCard.value = null
  editError.value = ''
}

async function submitEdit() {
  editError.value = ''
  const fields = CARD_FIELDS[editingCard.value] || []
  const payload = { employee: props.employee }
  for (const field of fields) payload[field] = form[field]
  try {
    const updated = await savePerson.submit(payload)
    // save_person returns the same projection get_person's own `employee`
    // key already carries, so the card reflects the write without a
    // reload of every other section on the page.
    person.data = { ...person.data, employee: updated }
    editingCard.value = null
  } catch (error) {
    editError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
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
            variant="ghost"
            @click="router.push('/people')"
          >
            Back to search
          </Button>
          <a
            v-if="person.data?.desk_url"
            :href="person.data.desk_url"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex min-h-11 items-center rounded px-3 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-3"
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
          <p
            v-if="!canEdit"
            class="text-sm text-ink-gray-6"
            data-testid="person-view-only"
          >
            View only. This person is outside the company you manage, so their record can't be changed here.
          </p>
          <!-- Overview (P8-U8). -->
          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="person-overview-heading"
          >
            <div class="flex items-center justify-between">
              <h2
                id="person-overview-heading"
                class="font-heading text-base font-semibold text-ink-gray-9"
              >
                Overview
              </h2>
              <Button
                v-if="canEdit"
                variant="ghost"
                data-testid="person-edit-overview"
                @click="openEdit('overview')"
              >
                Edit
              </Button>
            </div>
            <div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p class="text-sm text-ink-gray-6">
                  Designation
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.designation || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Department
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.department || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Branch
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.branch || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Work email
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.company_email || '—' }}
                </p>
              </div>
            </div>
          </section>

          <!-- Joining details (P8-U8). -->
          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="person-joining-heading"
          >
            <div class="flex items-center justify-between">
              <h2
                id="person-joining-heading"
                class="font-heading text-base font-semibold text-ink-gray-9"
              >
                Joining details
              </h2>
              <Button
                v-if="canEdit"
                variant="ghost"
                data-testid="person-edit-joining"
                @click="openEdit('joining')"
              >
                Edit
              </Button>
            </div>
            <div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
                  Employment type
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.employment_type || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Grade
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.grade || '—' }}
                </p>
              </div>
            </div>
          </section>

          <!-- Approvers and shift (P8-U9). -->
          <section
            class="surface-card elev-1 p-4"
            aria-labelledby="person-approvers-heading"
          >
            <div class="flex items-center justify-between">
              <h2
                id="person-approvers-heading"
                class="font-heading text-base font-semibold text-ink-gray-9"
              >
                Approvers and shift
              </h2>
              <Button
                v-if="canEdit"
                variant="ghost"
                data-testid="person-edit-approvers"
                @click="openEdit('approvers')"
              >
                Edit
              </Button>
            </div>
            <div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
                  Leave approver
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.leave_approver_name || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Expense approver
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.expense_approver_name || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Shift request approver
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.shift_request_approver_name || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Default shift
                </p>
                <p class="font-medium text-ink-gray-9">
                  {{ profile.default_shift || '—' }}
                </p>
              </div>
              <div>
                <p class="text-sm text-ink-gray-6">
                  Shift today
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
            </div>
            <!-- A dated Shift Assignment (Desk-only, KTD4) overrides the
                 default the moment one exists for today -- named here so
                 "Shift today" disagreeing with "Default shift" reads as
                 an explanation, not a bug. -->
            <p
              v-if="!sectionFailed('shift') && person.data?.shift && person.data.shift !== profile.default_shift"
              class="mt-3 text-sm text-ink-gray-6"
            >
              A dated shift assignment in Desk is currently overriding the default shift above.
            </p>
            <p
              v-if="APPROVER_PROBLEM_TEXT[profile.approver_problem]"
              class="surface-alert mt-3 p-3 text-sm"
              data-testid="person-approver-problem"
            >
              {{ APPROVER_PROBLEM_TEXT[profile.approver_problem] }}
            </p>
            <p
              v-else
              class="mt-3 text-sm text-ink-gray-6"
            >
              Leave, expense and shift approvers follow the manager.
            </p>
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

      <!-- One dialog serves all three edit cards (P8-U8/U9) -- which
           fields it shows and submits is decided by `editingCard`/
           `CARD_FIELDS`, not by which dialog markup is on screen. -->
      <Dialog
        v-model="editDialogOpen"
        :options="{ title: editDialogTitle, size: 'lg' }"
      >
        <template #body-content>
          <div
            class="space-y-4"
            data-testid="person-edit-form"
          >
            <template v-if="editingCard === 'overview'">
              <div>
                <label
                  for="person-edit-designation"
                  class="text-sm text-ink-gray-7"
                >
                  Designation
                </label>
                <select
                  id="person-edit-designation"
                  v-model="form.designation"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.designations || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
              <div>
                <label
                  for="person-edit-department"
                  class="text-sm text-ink-gray-7"
                >
                  Department
                </label>
                <select
                  id="person-edit-department"
                  v-model="form.department"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.departments || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
              <div>
                <label
                  for="person-edit-branch"
                  class="text-sm text-ink-gray-7"
                >
                  Branch
                </label>
                <select
                  id="person-edit-branch"
                  v-model="form.branch"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.branches || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
              <FormControl
                v-model="form.company_email"
                type="email"
                label="Work email"
              />
            </template>

            <template v-else-if="editingCard === 'joining'">
              <div>
                <label
                  for="person-edit-status"
                  class="text-sm text-ink-gray-7"
                >
                  Status
                </label>
                <select
                  id="person-edit-status"
                  v-model="form.status"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option
                    v-for="option in STATUS_OPTIONS"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
              <FormControl
                v-model="form.date_of_joining"
                type="date"
                label="Joined"
              />
              <div>
                <label
                  for="person-edit-employment-type"
                  class="text-sm text-ink-gray-7"
                >
                  Employment type
                </label>
                <select
                  id="person-edit-employment-type"
                  v-model="form.employment_type"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.employment_types || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
              <div>
                <label
                  for="person-edit-grade"
                  class="text-sm text-ink-gray-7"
                >
                  Grade
                </label>
                <select
                  id="person-edit-grade"
                  v-model="form.grade"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.grades || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
              <FormControl
                v-model="form.scheduled_confirmation_date"
                type="date"
                label="Scheduled confirmation date"
              />
              <FormControl
                v-model="form.final_confirmation_date"
                type="date"
                label="Final confirmation date"
              />
            </template>

            <template v-else-if="editingCard === 'approvers'">
              <div>
                <label
                  for="person-edit-manager"
                  class="text-sm text-ink-gray-7"
                >
                  Manager
                </label>
                <select
                  id="person-edit-manager"
                  v-model="form.reports_to"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.people || []"
                    :key="option.name"
                    :value="option.name"
                  >
                    {{ option.employee_name }}
                  </option>
                </select>
                <p class="mt-1 text-xs text-ink-gray-5">
                  Leave, expense and shift approvals follow the manager. Requests waiting on the old manager move to the new one.
                </p>
              </div>
              <div>
                <label
                  for="person-edit-default-shift"
                  class="text-sm text-ink-gray-7"
                >
                  Default shift
                </label>
                <select
                  id="person-edit-default-shift"
                  v-model="form.default_shift"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.shift_types || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
                <p class="mt-1 text-xs text-ink-gray-5">
                  A dated shift assignment made in Desk overrides this for the days it covers.
                </p>
              </div>
              <div>
                <label
                  for="person-edit-holiday-list"
                  class="text-sm text-ink-gray-7"
                >
                  Holiday list
                </label>
                <select
                  id="person-edit-holiday-list"
                  v-model="form.holiday_list"
                  class="mt-1 block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                >
                  <option value="">
                    None
                  </option>
                  <option
                    v-for="option in formOptions.data?.holiday_lists || []"
                    :key="option"
                    :value="option"
                  >
                    {{ option }}
                  </option>
                </select>
              </div>
            </template>

            <p
              v-if="editError"
              class="surface-alert p-3 text-sm"
              role="alert"
            >
              {{ editError }}
            </p>
          </div>
        </template>
        <template #actions>
          <div class="flex items-center gap-2">
            <Button
              variant="solid"
              theme="blue"
              :loading="savePerson.loading"
              @click="submitEdit"
            >
              Save
            </Button>
            <Button
              variant="ghost"
              @click="closeEdit"
            >
              Cancel
            </Button>
          </div>
        </template>
      </Dialog>
    </template>

    <!-- Search. Reached at /people. -->
    <template v-else>
      <PageHeader
        title="People"
        subtitle="Find a colleague to see their leave, attendance and requests."
      />

      <div
        v-if="!forbidden"
        class="mb-4 lg:w-80"
      >
        <SearchField
          v-model="query"
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
        <!-- U17 (HR-14): the department chips and the count, at every width.
             One department draws no chips -- there is nothing to choose. -->
        <SegmentedChips
          v-if="departmentOptions.length > 2"
          v-model="department"
          class="mb-3"
          label="Filter people by department"
          :options="departmentOptions"
        />
        <p
          class="mb-3 text-sm text-ink-gray-6"
          aria-live="polite"
          data-testid="people-count"
        >
          <span class="tabular">{{ department === null ? peopleCount(shown.length) : `${shown.length} of ${peopleCount(rows.length)}` }}</span>.
          <template v-if="total > rows.length">
            Only the first <span class="tabular">{{ rows.length }}</span> of
            <span class="tabular">{{ total }}</span> are loaded; search to narrow.
          </template>
        </p>

        <!-- Desktop: a compact table in plain markup, sortable headers with
             `aria-sort` the way ReportTable does it (KTD1). The name is the
             same button the phone card is, so a row opens exactly as before. -->
        <div
          v-if="isDesktop"
          class="surface-card elev-1 overflow-x-auto"
        >
          <table
            class="w-full border-separate border-spacing-0 text-sm"
            data-testid="people-table"
          >
            <caption class="sr-only">
              People
            </caption>
            <thead>
              <tr>
                <th
                  scope="col"
                  :aria-sort="ariaSort('employee_name')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('employee_name')"
                  >
                    Name
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('employee_name') }}</span>
                  </button>
                </th>
                <th
                  scope="col"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  Designation
                </th>
                <th
                  scope="col"
                  :aria-sort="ariaSort('department')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('department')"
                  >
                    Department
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('department') }}</span>
                  </button>
                </th>
                <th
                  v-if="manyCompanies"
                  scope="col"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  Company
                </th>
                <th
                  scope="col"
                  :aria-sort="ariaSort('date_of_joining')"
                  class="label border-b border-outline-gray-2 px-3 py-2 text-left"
                >
                  <button
                    type="button"
                    class="inline-flex min-h-9 cursor-pointer items-center gap-1 uppercase"
                    @click="toggleSort('date_of_joining')"
                  >
                    Joined
                    <span
                      aria-hidden="true"
                      class="w-3"
                    >{{ sortArrow('date_of_joining') }}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in shown"
                :key="row.name"
                class="text-ink-gray-7 hover:bg-surface-gray-2"
                data-testid="people-row"
              >
                <td class="border-b border-outline-gray-2 px-3 py-0.5">
                  <button
                    type="button"
                    class="flex min-h-11 w-full min-w-0 cursor-pointer items-center gap-2 text-left"
                    @click="openPerson(row)"
                  >
                    <Avatar
                      class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-xs font-bold text-ink-green-3"
                      :photo-url="row.photo_url"
                      :initials="row.initials"
                      :size="28"
                    />
                    <span class="min-w-0">
                      <span
                        class="block truncate font-medium text-ink-gray-9"
                        data-testid="people-name"
                      >{{ row.employee_name }}</span>
                      <span
                        v-if="personNumber(row)"
                        class="block truncate text-xs text-ink-gray-6"
                      >{{ personNumber(row) }}</span>
                    </span>
                  </button>
                </td>
                <td class="border-b border-outline-gray-2 px-3 py-2">
                  {{ row.designation || '—' }}
                </td>
                <td class="border-b border-outline-gray-2 px-3 py-2">
                  {{ row.department || '—' }}
                </td>
                <td
                  v-if="manyCompanies"
                  class="border-b border-outline-gray-2 px-3 py-2"
                >
                  {{ row.company }}
                </td>
                <td
                  class="tabular whitespace-nowrap border-b border-outline-gray-2 px-3 py-2"
                  data-testid="people-joined"
                  :data-date="row.date_of_joining || ''"
                >
                  {{ row.date_of_joining ? formatDate(row.date_of_joining) : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Phone: the cards, in the same order the table would show. -->
        <ul
          v-else
          class="space-y-2"
        >
          <li
            v-for="row in shown"
            :key="row.name"
          >
            <button
              type="button"
              class="surface-card elev-1 flex w-full min-w-0 items-center gap-3 p-3 text-left"
              data-testid="people-row"
              @click="openPerson(row)"
            >
              <Avatar
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                :photo-url="row.photo_url"
                :initials="row.initials"
                :size="40"
              />
              <span class="min-w-0 flex-1">
                <span class="block truncate font-medium text-ink-gray-9">{{ row.employee_name }}</span>
                <span
                  v-if="cardLine(row)"
                  class="block truncate text-sm text-ink-gray-6"
                >
                  {{ cardLine(row) }}
                </span>
              </span>
              <Icon
                name="chevronRight"
                class="shrink-0 text-ink-gray-4"
              />
            </button>
          </li>
        </ul>
      </AsyncState>
    </template>
  </div>
</template>
