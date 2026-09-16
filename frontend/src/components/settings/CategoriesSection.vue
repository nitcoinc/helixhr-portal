<script setup>
import { reactive, ref, watch } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

// P5-U14: the only two roles `HelixHRRequestCategory.validate()` accepts
// (helixhr/helixhr/doctype/helixhr_request_category/helixhr_request_category.py,
// WORKER_ROLES). The server is the real gate; this is only what the picker
// offers, so the two can never quietly drift into offering a role the save
// would refuse.
const ROUTABLE_ROLES = ['HR Manager', 'IT Team']

const props = defineProps({
  categories: { type: Array, required: true },
})
const emit = defineEmits(['saved'])

const editing = ref(null) // category name being edited, or '__new__'
const form = reactive({ category_name: '', hint: '', route_to_role: 'HR Manager', sla_days: 0, is_active: true })
const formError = ref('')

function startCreate() {
  editing.value = '__new__'
  Object.assign(form, { category_name: '', hint: '', route_to_role: 'HR Manager', sla_days: 0, is_active: true })
  formError.value = ''
}

function startEdit(category) {
  editing.value = category.name
  Object.assign(form, {
    category_name: category.category_name,
    hint: category.hint || '',
    route_to_role: category.route_to_role,
    sla_days: category.sla_days || 0,
    is_active: !!category.is_active,
  })
  formError.value = ''
}

function cancel() {
  editing.value = null
  formError.value = ''
}

const save = createResource({ url: 'helixhr.api.save_request_category', method: 'POST' })

async function submit() {
  formError.value = ''
  const isNew = editing.value === '__new__'
  try {
    await save.submit({
      name: isNew ? form.category_name : editing.value,
      hint: form.hint,
      route_to_role: form.route_to_role,
      sla_days: form.sla_days,
      is_active: form.is_active ? 1 : 0,
    })
    editing.value = null
    emit('saved')
  } catch (error) {
    // P2-R25: a rejected save keeps every value the person typed.
    formError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="label">
        Request categories
      </h2>
      <Button
        variant="outline"
        @click="startCreate"
      >
        New category
      </Button>
    </div>

    <div class="surface-card elev-1 divide-y divide-outline-gray-1">
      <div
        v-for="category in props.categories"
        :key="category.name"
        class="flex items-center justify-between gap-3 px-4 py-3"
        data-testid="settings-category-row"
      >
        <div class="min-w-0">
          <p class="truncate font-medium text-ink-gray-9">
            {{ category.category_name }}
            <span
              v-if="!category.is_active"
              class="ml-1 text-xs font-normal text-ink-gray-5"
            >(inactive)</span>
          </p>
          <p class="truncate text-sm text-ink-gray-6">
            Routes to {{ category.route_to_role }}
          </p>
        </div>
        <Button
          variant="ghost"
          @click="startEdit(category)"
        >
          Edit
        </Button>
      </div>
      <p
        v-if="!props.categories.length"
        class="px-4 py-6 text-sm text-ink-gray-6"
      >
        No categories yet.
      </p>
    </div>

    <div
      v-if="editing"
      class="surface-card elev-1 space-y-4 p-4"
      data-testid="settings-category-form"
    >
      <FormControl
        v-if="editing === '__new__'"
        v-model="form.category_name"
        label="Category name"
      />
      <p
        v-else
        class="text-sm text-ink-gray-6"
      >
        Editing <span class="font-medium text-ink-gray-9">{{ editing }}</span>
      </p>
      <FormControl
        v-model="form.hint"
        label="Hint (shown to the employee on the picker)"
      />
      <div class="space-y-1.5">
        <label
          for="settings-category-route"
          class="text-sm text-ink-gray-7"
        >
          Routes to
        </label>
        <!-- A native `<select>`, not FormControl's `type="select"` (which is
             a combobox in this frappe-ui version, not a real listbox
             `<select>`) -- matching the raw-select pattern WeekGrid.vue
             already uses for a project picker. -->
        <select
          id="settings-category-route"
          v-model="form.route_to_role"
          class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
        >
          <option
            v-for="role in ROUTABLE_ROLES"
            :key="role"
            :value="role"
          >
            {{ role }}
          </option>
        </select>
      </div>
      <FormControl
        v-model="form.sla_days"
        type="number"
        label="SLA (days)"
      />
      <FormControl
        v-model="form.is_active"
        type="checkbox"
        label="Active (shown to employees)"
      />
      <p
        v-if="formError"
        class="surface-alert p-3 text-sm"
        role="alert"
      >
        {{ formError }}
      </p>
      <div class="flex items-center gap-2">
        <Button
          variant="solid"
          theme="blue"
          :loading="save.loading"
          @click="submit"
        >
          Save
        </Button>
        <Button
          variant="ghost"
          @click="cancel"
        >
          Cancel
        </Button>
      </div>
    </div>
  </div>
</template>
