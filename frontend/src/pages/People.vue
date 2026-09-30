<script setup>
import Avatar from '@/components/Avatar.vue'
import { computed, reactive, ref, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Dialog, Button, FormControl } from 'frappe-ui'
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
  approvers: [
    'reports_to',
    'leave_approver',
    'expense_approver',
    'shift_request_approver',
    'default_shift',
    'holiday_list',
  ],
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
  leave_approver: '',
  expense_approver: '',
  shift_request_approver: '',
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
    // The picker's own values are employee ids, never logins -- the
    // `_employee` id backing each approver's User value, not the value
    // `save_person` actually writes to Employee (KTD5).
    leave_approver: profile.value.leave_approver_employee || '',
    expense_approver: profile.value.expense_approver_employee || '',
    shift_request_approver: profile.value.shift_request_approver_employee || '',
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
              </div>
              <div>
                <label
                  for="person-edit-leave-approver"
                  class="text-sm text-ink-gray-7"
                >
                  Leave approver
                </label>
                <select
                  id="person-edit-leave-approver"
                  v-model="form.leave_approver"
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
              </div>
              <div>
                <label
                  for="person-edit-expense-approver"
                  class="text-sm text-ink-gray-7"
                >
                  Expense approver
                </label>
                <select
                  id="person-edit-expense-approver"
                  v-model="form.expense_approver"
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
              </div>
              <div>
                <label
                  for="person-edit-shift-request-approver"
                  class="text-sm text-ink-gray-7"
                >
                  Shift request approver
                </label>
                <select
                  id="person-edit-shift-request-approver"
                  v-model="form.shift_request_approver"
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
              <Avatar
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-sm font-bold text-ink-green-3"
                :photo-url="row.photo_url"
                :initials="row.initials"
                :name="row.employee_name"
                :size="40"
              />
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
