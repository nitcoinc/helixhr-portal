<script setup>
import { reactive, ref } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

// P5-KTD12: exactly the five fields `LEAVE_TYPE_EDITABLE_FIELDS` names in
// helixhr/utils.py -- not the thirty HRMS ships on this form.
const props = defineProps({
  leaveTypes: { type: Array, required: true },
})
const emit = defineEmits(['saved'])

const editing = ref(null)
const form = reactive({
  leave_type_name: '',
  max_leaves_allowed: 0,
  is_carry_forward: false,
  is_lwp: false,
  helixhr_hr_approves: false,
})
const formError = ref('')

function startCreate() {
  editing.value = '__new__'
  Object.assign(form, {
    leave_type_name: '',
    max_leaves_allowed: 0,
    is_carry_forward: false,
    is_lwp: false,
    helixhr_hr_approves: false,
  })
  formError.value = ''
}

function startEdit(row) {
  editing.value = row.name
  Object.assign(form, {
    leave_type_name: row.leave_type_name,
    max_leaves_allowed: row.max_leaves_allowed || 0,
    is_carry_forward: !!row.is_carry_forward,
    is_lwp: !!row.is_lwp,
    helixhr_hr_approves: !!row.helixhr_hr_approves,
  })
  formError.value = ''
}

function cancel() {
  editing.value = null
  formError.value = ''
}

const save = createResource({ url: 'helixhr.api.save_leave_type', method: 'POST' })

async function submit() {
  formError.value = ''
  const isNew = editing.value === '__new__'
  try {
    await save.submit({
      name: isNew ? form.leave_type_name : editing.value,
      leave_type_name: form.leave_type_name,
      max_leaves_allowed: form.max_leaves_allowed,
      is_carry_forward: form.is_carry_forward ? 1 : 0,
      is_lwp: form.is_lwp ? 1 : 0,
      helixhr_hr_approves: form.helixhr_hr_approves ? 1 : 0,
    })
    editing.value = null
    emit('saved')
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="label">
        Leave types
      </h2>
      <Button
        variant="outline"
        @click="startCreate"
      >
        New leave type
      </Button>
    </div>

    <div class="surface-card elev-1 divide-y divide-outline-gray-1">
      <div
        v-for="row in props.leaveTypes"
        :key="row.name"
        class="flex items-center justify-between gap-3 px-4 py-3"
        data-testid="settings-leave-type-row"
      >
        <div class="min-w-0">
          <p class="truncate font-medium text-ink-gray-9">
            {{ row.leave_type_name }}
          </p>
          <p class="truncate text-sm text-ink-gray-6">
            Up to {{ row.max_leaves_allowed || 0 }} days
          </p>
        </div>
        <Button
          variant="ghost"
          @click="startEdit(row)"
        >
          Edit
        </Button>
      </div>
      <p
        v-if="!props.leaveTypes.length"
        class="px-4 py-6 text-sm text-ink-gray-6"
      >
        No leave types yet.
      </p>
    </div>

    <div
      v-if="editing"
      class="surface-card elev-1 space-y-4 p-4"
      data-testid="settings-leave-type-form"
    >
      <FormControl
        v-model="form.leave_type_name"
        label="Name"
        :disabled="editing !== '__new__'"
      />
      <FormControl
        v-model="form.max_leaves_allowed"
        type="number"
        label="Maximum days allowed"
      />
      <FormControl
        v-model="form.is_carry_forward"
        type="checkbox"
        label="Carries forward to the next leave period"
      />
      <FormControl
        v-model="form.is_lwp"
        type="checkbox"
        label="Leave without pay"
      />
      <FormControl
        v-model="form.helixhr_hr_approves"
        type="checkbox"
        label="HR approves this leave type (not the line manager)"
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
