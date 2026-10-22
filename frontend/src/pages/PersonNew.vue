<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import { today } from '@/lib/dates'
import { notify } from '@/lib/notify'

// Plan 2026-10-22-001 U4 (R1, R3, R4, R7, R18). HR adds a person, and
// their login, on one screen.
//
// `get_new_person_options` and `create_employee` are the server's own gates
// (`_can_add_people`): anyone else hitting /people/new gets AsyncState's
// 'forbidden' region, the same posture People.vue takes. The server refuses
// in one plain sentence with no field key, so the required fields are
// checked here for the inline errors, and a refusal the server makes on its
// own (a duplicate, a date rule, scope) goes to the form-level alert.
const router = useRouter()

const options = createResource({
  url: 'helixhr.api.get_new_person_options',
  auto: true,
  onSuccess(data) {
    // One company in reach: nothing to choose.
    if (data.companies?.length === 1 && !form.company) form.company = data.companies[0]
  },
})
const createEmployee = createResource({ url: 'helixhr.api.create_employee', method: 'POST' })

const form = reactive({
  first_name: '',
  last_name: '',
  gender: '',
  date_of_birth: '',
  company: '',
  date_of_joining: '',
  designation: '',
  department: '',
  reports_to: '',
  company_email: '',
})
const createLogin = ref(true)

// The department and manager lists are the chosen company's.
const companyOptions = computed(() => options.data?.by_company?.[form.company] || { departments: [], people: [] })
watch(
  () => form.company,
  () => {
    if (!companyOptions.value.departments.includes(form.department)) form.department = ''
    if (!companyOptions.value.people.some((person) => person.name === form.reports_to)) form.reports_to = ''
  },
)

// The same sentences `create_employee` refuses with (`_NEW_PERSON_REQUIRED`),
// keyed by the field they belong under.
const REQUIRED = {
  first_name: 'Enter their first name.',
  gender: 'Choose their gender.',
  date_of_birth: 'Enter their date of birth.',
  company: 'Choose the company they join.',
  date_of_joining: 'Enter their joining date.',
}
const errors = reactive({})
const formError = ref('')
const errorCount = computed(() => Object.values(errors).filter(Boolean).length)

function validate() {
  for (const key of Object.keys(errors)) delete errors[key]
  for (const [field, message] of Object.entries(REQUIRED)) {
    if (!form[field].trim()) errors[field] = message
  }
  if (createLogin.value && !form.company_email.trim()) {
    errors.company_email = 'Add a company email to create a login, or turn off Create login now.'
  }
  return errorCount.value === 0
}

function fieldAttrs(field) {
  return {
    id: `new-person-${field}`,
    'aria-invalid': errors[field] ? 'true' : undefined,
    'aria-describedby': errors[field] ? `new-person-${field}-error` : undefined,
  }
}

const WELCOME_TEXT = {
  sent: 'Login created; welcome email sent.',
  held: 'Login created; welcome email held until Microsoft sign-in is on.',
  not_sent: 'Login created; welcome email not sent.',
}

// The employee is saved even when the login is refused, so the toast states
// both outcomes. A refused login is an error toast, which stays until it is
// dismissed: HR has a step left to take on the person page.
function announce(result) {
  const added = `Added ${result.employee_name}.`
  if (result.login_error) {
    notify.error(`${added} Login not created: ${result.login_error}`)
  } else if (result.login_created) {
    notify.success(`${added} ${WELCOME_TEXT[result.welcome] || 'Login created.'}`)
  } else {
    notify.success(added)
  }
}

async function submit() {
  formError.value = ''
  if (!validate()) {
    await nextTick()
    document.querySelector('[aria-invalid="true"]')?.focus()
    return
  }
  try {
    const result = await createEmployee.submit({ ...form, create_login: createLogin.value ? 1 : 0 })
    announce(result)
    router.push(`/people/${result.employee}`)
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not add them. Please try again.'
  }
}

const inputClass =
  'mt-1 block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8'
const labelClass = 'text-sm text-ink-gray-7'
const errorClass = 'mt-1 text-sm text-ink-red-4'
</script>

<template>
  <div>
    <PageHeader
      title="Add employee"
      subtitle="They start as Active. A login lets them sign in to HelixHR with their company email."
    >
      <template #actions>
        <Button
          variant="ghost"
          @click="router.push('/people')"
        >
          Back to people
        </Button>
      </template>
    </PageHeader>

    <AsyncState
      section="new-person"
      :resource="options"
      :empty="false"
      skeleton="block"
      skeleton-height="h-96"
    >
      <form
        class="surface-card elev-1 max-w-2xl space-y-6 p-4"
        novalidate
        data-testid="new-person-form"
        @submit.prevent="submit"
      >
        <p
          v-if="errorCount > 1"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ errorCount }} fields need an answer. They are marked below.
        </p>

        <fieldset>
          <legend class="label mb-2">
            Person
          </legend>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label
                for="new-person-first_name"
                :class="labelClass"
              >First name</label>
              <input
                v-bind="fieldAttrs('first_name')"
                v-model="form.first_name"
                type="text"
                autocomplete="off"
                maxlength="140"
                :class="inputClass"
              >
              <p
                v-if="errors.first_name"
                id="new-person-first_name-error"
                :class="errorClass"
              >
                {{ errors.first_name }}
              </p>
            </div>
            <div>
              <label
                for="new-person-last_name"
                :class="labelClass"
              >Last name <span class="text-ink-gray-5">(optional)</span></label>
              <input
                id="new-person-last_name"
                v-model="form.last_name"
                type="text"
                autocomplete="off"
                maxlength="140"
                :class="inputClass"
              >
            </div>
            <div>
              <label
                for="new-person-gender"
                :class="labelClass"
              >Gender</label>
              <select
                v-bind="fieldAttrs('gender')"
                v-model="form.gender"
                :class="inputClass"
              >
                <option value="">
                  Choose
                </option>
                <option
                  v-for="option in options.data?.genders || []"
                  :key="option"
                  :value="option"
                >
                  {{ option }}
                </option>
              </select>
              <p
                v-if="errors.gender"
                id="new-person-gender-error"
                :class="errorClass"
              >
                {{ errors.gender }}
              </p>
            </div>
            <div>
              <label
                for="new-person-date_of_birth"
                :class="labelClass"
              >Date of birth</label>
              <input
                v-bind="fieldAttrs('date_of_birth')"
                v-model="form.date_of_birth"
                type="date"
                :max="today()"
                :class="inputClass"
              >
              <p
                v-if="errors.date_of_birth"
                id="new-person-date_of_birth-error"
                :class="errorClass"
              >
                {{ errors.date_of_birth }}
              </p>
            </div>
          </div>
        </fieldset>

        <fieldset>
          <legend class="label mb-2">
            Work
          </legend>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label
                for="new-person-company"
                :class="labelClass"
              >Company</label>
              <select
                v-bind="fieldAttrs('company')"
                v-model="form.company"
                :class="inputClass"
              >
                <option value="">
                  Choose
                </option>
                <option
                  v-for="option in options.data?.companies || []"
                  :key="option"
                  :value="option"
                >
                  {{ option }}
                </option>
              </select>
              <p
                v-if="errors.company"
                id="new-person-company-error"
                :class="errorClass"
              >
                {{ errors.company }}
              </p>
            </div>
            <div>
              <label
                for="new-person-date_of_joining"
                :class="labelClass"
              >Joining date</label>
              <input
                v-bind="fieldAttrs('date_of_joining')"
                v-model="form.date_of_joining"
                type="date"
                :class="inputClass"
              >
              <p
                v-if="errors.date_of_joining"
                id="new-person-date_of_joining-error"
                :class="errorClass"
              >
                {{ errors.date_of_joining }}
              </p>
            </div>
            <div>
              <label
                for="new-person-designation"
                :class="labelClass"
              >Designation</label>
              <select
                id="new-person-designation"
                v-model="form.designation"
                :class="inputClass"
              >
                <option value="">
                  None
                </option>
                <option
                  v-for="option in options.data?.designations || []"
                  :key="option"
                  :value="option"
                >
                  {{ option }}
                </option>
              </select>
            </div>
            <div>
              <label
                for="new-person-department"
                :class="labelClass"
              >Department</label>
              <select
                id="new-person-department"
                v-model="form.department"
                :class="inputClass"
                :disabled="!form.company"
              >
                <option value="">
                  None
                </option>
                <option
                  v-for="option in companyOptions.departments"
                  :key="option"
                  :value="option"
                >
                  {{ option }}
                </option>
              </select>
            </div>
            <div class="sm:col-span-2">
              <label
                for="new-person-reports_to"
                :class="labelClass"
              >Manager</label>
              <select
                id="new-person-reports_to"
                v-model="form.reports_to"
                :class="inputClass"
                :disabled="!form.company"
                aria-describedby="new-person-reports_to-help"
              >
                <option value="">
                  None
                </option>
                <option
                  v-for="option in companyOptions.people"
                  :key="option.name"
                  :value="option.name"
                >
                  {{ option.employee_name }}
                </option>
              </select>
              <p
                id="new-person-reports_to-help"
                class="mt-1 text-xs text-ink-gray-5"
              >
                {{ form.company ? 'Their leave, expense and shift requests go to this person.' : 'Choose the company first.' }}
              </p>
            </div>
          </div>
        </fieldset>

        <fieldset>
          <legend class="label mb-2">
            Login
          </legend>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div class="sm:col-span-2">
              <label
                for="new-person-company_email"
                :class="labelClass"
              >Company email <span
                v-if="!createLogin"
                class="text-ink-gray-5"
              >(optional)</span></label>
              <input
                v-bind="fieldAttrs('company_email')"
                v-model="form.company_email"
                type="email"
                autocomplete="off"
                maxlength="140"
                :class="inputClass"
              >
              <p
                v-if="errors.company_email"
                id="new-person-company_email-error"
                :class="errorClass"
              >
                {{ errors.company_email }}
              </p>
            </div>
          </div>
          <label class="mt-2 flex min-h-11 items-center gap-2 text-sm text-ink-gray-8">
            <input
              v-model="createLogin"
              type="checkbox"
              class="size-4"
              data-testid="new-person-create-login"
            >
            Create login now
          </label>
          <p class="text-xs text-ink-gray-5">
            Their company email becomes their login, with access to their own leave, attendance and requests. HelixHR emails them how to sign in.
          </p>
        </fieldset>

        <p
          v-if="formError"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ formError }}
        </p>

        <Button
          type="submit"
          variant="solid"
          theme="blue"
          :loading="createEmployee.loading"
        >
          Add employee
        </Button>
      </form>
    </AsyncState>
  </div>
</template>
