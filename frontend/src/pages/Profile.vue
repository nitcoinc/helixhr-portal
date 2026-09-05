<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { session } from '@/lib/session'
import { formatDate } from '@/lib/dates'

// P2-U3 / P2-R21. Identity comes from the one bootstrap the shell already
// made (`lib/session.js`), not from a page-local
// `hrms.api.get_current_employee_info`. Five pages held a copy of that
// resource left over from before P2-U2 -- five repeated identity round trips
// for a value that cannot change while the tab is open.
const me = computed(() => session.employee)

const employee = createResource({
  url: 'frappe.client.get',
  makeParams: () => ({ doctype: 'Employee', name: me.value.name }),
  auto: false,
})

watch(
  () => me.value?.name,
  (name) => {
    if (name) employee.fetch()
  },
  { immediate: true },
)

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

// `frappe.client.get` strips every permlevel-1 field before it answers, and
// the U1 fixtures put designation, department, branch, date_of_joining and
// the rest behind permlevel 1 -- so the document alone renders half this page
// as em-dashes. `get_dashboard` resolves the same five values server-side
// with `frappe.db.get_value`, which is not permlevel-filtered, and is already
// on this page for the manager's name. `header` is that answer.
const header = computed(() => dashboard.data?.employee || {})
const managerName = computed(() => header.value.manager_name || '')

// ── The field block ────────────────────────────────────────────────────
// The canvas gives Profile the same anchored region every other page has:
// who you are, on the deep field, with the initials monogram in signal
// yellow -- the one place on this screen the accent is legal.
const initials = computed(() =>
  (me.value?.employee_name || '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join(''),
)

const roleLine = computed(() =>
  [header.value.designation, header.value.department].filter(Boolean).join(' · '),
)

const placeLine = computed(() =>
  [managerName.value ? `Reports to ${managerName.value}` : null, header.value.branch]
    .filter(Boolean)
    .join(' · '),
)

// ── Read-only rows ─────────────────────────────────────────────────────
// Name, designation, department, manager and location moved *into* the field
// block above, so the card below holds only the facts that are not already
// on screen. "Ask HR" stays on the rows an employee would plausibly need
// corrected, and nowhere else -- an Ask HR link next to a joining date is an
// invitation to raise a request nobody can act on.
const READONLY_FIELDS = [
  { field: 'employee_number', label: 'Employee ID', askHr: false },
  { field: 'date_of_joining', label: 'Joined', askHr: false, date: true },
  { field: 'work_email', label: 'Work email', askHr: true },
  { field: 'manager_name', label: 'Manager', askHr: true, header: true },
  { field: 'branch', label: 'Location', askHr: true, header: true },
  { field: 'designation', label: 'Designation', askHr: true, header: true },
  { field: 'department', label: 'Department', askHr: true, header: true },
  { field: 'status', label: 'Status', askHr: false },
]

function readonlyValue(row) {
  // The sign-in address is the work email as far as this portal is concerned;
  // Employee.company_email is permlevel-locked and comes back empty.
  if (row.field === 'work_email') return me.value?.user_id || '—'
  const value = row.header ? header.value[row.field] : employee.data?.[row.field]
  if (!value) return '—'
  return row.date ? formatDate(value) : value
}

function askHrLink(label) {
  return {
    path: '/requests',
    query: { category: 'HR Letter', subject: `Update my ${label.toLowerCase()}` },
  }
}

// ── Editable fields, one Save bar ──────────────────────────────────────
// The page used to carry a Save button *per field* -- seven of them, each
// its own request, each with its own "Saved" flash. Updating a phone number
// and an address was two saves and two round trips, and there was no way to
// tell whether you had finished. `update_my_profile` already takes several
// fields at once, so one bar saves whatever actually changed and says how
// much that is (the Profile artboard).
const EDITABLE_FIELDS = [
  { field: 'cell_number', label: 'Mobile', type: 'text' },
  { field: 'personal_email', label: 'Personal email', type: 'email' },
  { field: 'current_address', label: 'Current address', type: 'textarea' },
  { field: 'permanent_address', label: 'Permanent address', type: 'textarea' },
  { field: 'person_to_be_contacted', label: 'Emergency contact name', type: 'text' },
  { field: 'relation', label: 'Relation', type: 'text' },
  { field: 'emergency_phone_number', label: 'Emergency contact phone', type: 'text' },
]

const form = reactive({})
const saved = reactive({})

function resetForm(doc) {
  if (!doc) return
  for (const { field } of EDITABLE_FIELDS) {
    saved[field] = doc[field] || ''
    form[field] = saved[field]
  }
}
watch(() => employee.data, resetForm, { immediate: true })

const changedFields = computed(() =>
  EDITABLE_FIELDS.map(({ field }) => field).filter((field) => form[field] !== saved[field]),
)
const dirty = computed(() => changedFields.value.length > 0)

const save = createResource({ url: 'helixhr.api.update_my_profile', method: 'POST' })
const saveError = ref('')
const justSaved = ref(false)

function discard() {
  for (const field of changedFields.value) form[field] = saved[field]
  saveError.value = ''
}

async function saveChanges() {
  saveError.value = ''
  const payload = Object.fromEntries(changedFields.value.map((field) => [field, form[field]]))
  try {
    // The server answers with the persisted values for every editable
    // field, so the baseline is what the record now holds rather than what
    // the browser hoped it sent.
    const persisted = await save.submit(payload)
    for (const { field } of EDITABLE_FIELDS) {
      saved[field] = persisted?.[field] ?? form[field]
      form[field] = saved[field]
    }
    justSaved.value = true
    setTimeout(() => (justSaved.value = false), 3000)
  } catch (error) {
    // P2-R25: a failed save keeps every value the person typed. Nothing is
    // reset, so Retry is one more tap rather than re-entering the form.
    saveError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div>
    <PageHeader title="Your profile" />

    <AsyncState
      section="profile"
      :resource="employee"
      :empty="!employee.data"
      empty-title="We couldn't find your employee record"
      empty-body="Ask HR to link your sign-in to an employee record."
      skeleton="block"
      skeleton-height="h-96"
    >
      <div class="space-y-6">
        <!-- The one anchored region on this page. Everything below it rests
             on paper; nothing below it may use the signal yellow. -->
        <section
          class="surface-field elev-2 flex items-center gap-4 p-5"
          aria-label="Your identity"
          data-testid="profile-identity"
        >
          <span
            class="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-white/10 font-heading text-xl font-bold text-signal"
            aria-hidden="true"
          >
            {{ initials || '—' }}
          </span>
          <div class="min-w-0">
            <h2 class="type-section font-heading text-white">
              {{ me?.employee_name || '—' }}
            </h2>
            <p
              v-if="roleLine"
              class="mt-0.5 text-sm text-blue-100"
            >
              {{ roleLine }}
            </p>
            <p
              v-if="placeLine"
              class="text-sm text-blue-200"
            >
              {{ placeLine }}
            </p>
          </div>
        </section>

        <section aria-labelledby="profile-readonly-heading">
          <h2
            id="profile-readonly-heading"
            class="label mb-2"
          >
            Your information
          </h2>
          <div class="surface-card elev-1 divide-y divide-outline-gray-1">
            <div
              v-for="row in READONLY_FIELDS"
              :key="row.field"
              :data-testid="`profile-readonly-${row.field}`"
              class="flex items-center justify-between gap-3 px-4 py-3"
            >
              <span class="shrink-0 text-sm text-ink-gray-6">{{ row.label }}</span>
              <span class="flex min-w-0 items-center gap-3">
                <span class="truncate text-right text-ink-gray-9">
                  {{ readonlyValue(row) }}
                </span>
                <!-- Inline, on the row it is about: the canvas puts the way
                     to get a wrong value fixed next to the wrong value,
                     rather than in a "something else?" line at the bottom. -->
                <router-link
                  v-if="row.askHr"
                  :to="askHrLink(row.label)"
                  class="-my-2 inline-flex min-h-11 shrink-0 cursor-pointer items-center text-sm font-medium text-blue-700 underline decoration-dotted underline-offset-4"
                >
                  Ask HR
                </router-link>
              </span>
            </div>
          </div>
        </section>

        <section aria-labelledby="profile-editable-heading">
          <h2
            id="profile-editable-heading"
            class="label mb-2"
          >
            You can update
          </h2>
          <div class="surface-card elev-1 space-y-4 p-4">
            <div
              v-for="row in EDITABLE_FIELDS"
              :key="row.field"
              :data-testid="`profile-editable-${row.field}`"
            >
              <FormControl
                v-model="form[row.field]"
                :label="row.label"
                :type="row.type"
              />
            </div>
          </div>
        </section>

        <p
          v-if="saveError"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ saveError }}
        </p>

        <!-- One bar for the whole form. It only appears once something has
             actually changed, so a page you came to read has no dead control
             on it, and it sits above the tab bar and inside the safe area
             (index.css, `.action-bar`). -->
        <div
          v-if="dirty || justSaved"
          class="action-bar flex items-center justify-between gap-3"
          data-testid="profile-save-bar"
        >
          <p
            class="text-sm text-ink-gray-6"
            aria-live="polite"
          >
            <template v-if="dirty">
              <span class="tabular">{{ changedFields.length }}</span>
              unsaved change{{ changedFields.length === 1 ? '' : 's' }}
            </template>
            <template v-else>
              Saved
            </template>
          </p>
          <div
            v-if="dirty"
            class="flex shrink-0 items-center gap-2"
          >
            <Button
              variant="ghost"
              @click="discard"
            >
              Discard
            </Button>
            <Button
              variant="solid"
              theme="blue"
              :loading="save.loading"
              @click="saveChanges"
            >
              Save
            </Button>
          </div>
        </div>
      </div>
    </AsyncState>
  </div>
</template>
