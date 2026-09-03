<script setup>
import { computed, reactive, watch } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'

const me = createResource({
  url: 'hrms.api.get_current_employee_info',
  auto: true,
})

const employee = createResource({
  url: 'frappe.client.get',
  makeParams: () => ({ doctype: 'Employee', name: me.data.name }),
  auto: false,
})

watch(
  () => me.data?.name,
  (name) => {
    if (name) employee.fetch()
  },
)

// Read-only fields, and which of them show an "Ask HR" link (R11) --
// only the ones an employee would plausibly need corrected, not every
// locked field on the doctype.
const READONLY_FIELDS = [
  { field: 'employee_name', label: 'Full name', askHr: true },
  { field: 'employee_number', label: 'Employee ID', askHr: false },
  { field: 'designation', label: 'Designation', askHr: true },
  { field: 'department', label: 'Department', askHr: true },
  { field: 'reports_to_name', label: 'Manager', askHr: true },
  { field: 'branch', label: 'Location', askHr: true },
  { field: 'date_of_joining', label: 'Date of joining', askHr: false },
  { field: 'status', label: 'Status', askHr: false },
]

// The manager's name has to come from the server, not from a
// `frappe.client.get_value` on their Employee record: U5's permlevel lock
// means an employee cannot read another Employee row, so that call came
// back `{}` and the field rendered a literal "{}" on screen. get_dashboard
// already resolves the same name with the right access (_get_employee_header),
// so the profile reads it from there and the two screens cannot disagree.
const dashboard = createResource({
  url: 'helixhr.api.get_dashboard',
  auto: true,
})

function readonlyValue(field) {
  if (field === 'reports_to_name') return dashboard.data?.employee?.manager_name || '—'
  return employee.data?.[field] || '—'
}

function askHrLink(label) {
  return {
    path: '/requests',
    query: { category: 'HR Letter', subject: `Update my ${label.toLowerCase()}` },
  }
}

// Editable fields (R9) -- inline-save form, one call per save so a mistake
// in one field never blocks the others.
const EDITABLE_FIELDS = [
  { field: 'cell_number', label: 'Mobile number', type: 'text' },
  { field: 'personal_email', label: 'Personal email', type: 'email' },
  { field: 'current_address', label: 'Current address', type: 'textarea' },
  { field: 'permanent_address', label: 'Permanent address', type: 'textarea' },
  { field: 'person_to_be_contacted', label: 'Emergency contact name', type: 'text' },
  { field: 'relation', label: 'Relation', type: 'text' },
  { field: 'emergency_phone_number', label: 'Emergency contact phone', type: 'text' },
]

const form = reactive({})
watch(
  () => employee.data,
  (doc) => {
    if (!doc) return
    for (const { field } of EDITABLE_FIELDS) {
      form[field] = doc[field] || ''
    }
  },
)

const saveField = createResource({
  url: 'helixhr.api.update_my_profile',
  method: 'POST',
})

const savingFieldName = computed(() => (saveField.loading ? saveField.currentField : null))
const savedField = reactive({})
const fieldError = reactive({})

async function save(field) {
  fieldError[field] = ''
  saveField.currentField = field
  try {
    await saveField.submit({ [field]: form[field] })
    savedField[field] = true
    setTimeout(() => (savedField[field] = false), 2000)
  } catch (e) {
    fieldError[field] = e?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Your profile" />

    <div
      v-if="me.loading || employee.loading"
      class="py-6 text-ink-gray-5"
    >
      Loading…
    </div>

    <template v-else-if="employee.data">
      <section>
        <h2 class="mb-3 text-sm font-medium text-ink-gray-6">
          Your information
        </h2>
        <div class="space-y-3 rounded-lg border border-outline-gray-2 bg-surface-white p-4">
          <div
            v-for="row in READONLY_FIELDS"
            :key="row.field"
            :data-testid="`profile-readonly-${row.field}`"
            class="flex items-center justify-between gap-2"
          >
            <span class="text-sm text-ink-gray-6">{{ row.label }}</span>
            <span class="flex items-center gap-2 text-right text-ink-gray-9">
              {{ readonlyValue(row.field) }}
              <router-link
                v-if="row.askHr"
                :to="askHrLink(row.label)"
                class="cursor-pointer text-sm text-blue-700 underline decoration-dotted"
              >
                Ask HR
              </router-link>
            </span>
          </div>
        </div>
      </section>

      <section>
        <h2 class="mb-3 text-sm font-medium text-ink-gray-6">
          You can update
        </h2>
        <div class="space-y-4 rounded-lg border border-outline-gray-2 bg-surface-white p-4">
          <div
            v-for="row in EDITABLE_FIELDS"
            :key="row.field"
            :data-testid="`profile-editable-${row.field}`"
          >
            <FormControl
              v-model="form[row.field]"
              :label="row.label"
              :type="row.type === 'textarea' ? 'textarea' : row.type === 'email' ? 'email' : 'text'"
            />
            <p
              v-if="fieldError[row.field]"
              class="mt-1 text-sm text-ink-red-4"
            >
              {{ fieldError[row.field] }}
            </p>
            <div class="mt-1 flex items-center gap-2">
              <Button
                size="sm"
                :loading="savingFieldName === row.field"
                @click="save(row.field)"
              >
                Save
              </Button>
              <span
                v-if="savedField[row.field]"
                class="text-sm text-ink-green-4"
              >
                Saved
              </span>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
