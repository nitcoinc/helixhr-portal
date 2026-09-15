<script setup>
import { reactive, ref } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

// P5-KTD12: name, start time, end time, and the check-in/check-out window
// (both stored as minutes on Shift Type -- HRMS's own field shape).
const props = defineProps({
  shiftTypes: { type: Array, required: true },
})
const emit = defineEmits(['saved'])

const editing = ref(null)
const form = reactive({
  name: '',
  start_time: '',
  end_time: '',
  begin_check_in_before_shift_start_time: 0,
  allow_check_out_after_shift_end_time: 0,
})
const formError = ref('')

function startCreate() {
  editing.value = '__new__'
  Object.assign(form, {
    name: '',
    start_time: '',
    end_time: '',
    begin_check_in_before_shift_start_time: 0,
    allow_check_out_after_shift_end_time: 0,
  })
  formError.value = ''
}

function startEdit(row) {
  editing.value = row.name
  Object.assign(form, {
    name: row.name,
    start_time: row.start_time || '',
    end_time: row.end_time || '',
    begin_check_in_before_shift_start_time: row.begin_check_in_before_shift_start_time || 0,
    allow_check_out_after_shift_end_time: row.allow_check_out_after_shift_end_time || 0,
  })
  formError.value = ''
}

function cancel() {
  editing.value = null
  formError.value = ''
}

const save = createResource({ url: 'helixhr.api.save_shift_type', method: 'POST' })

async function submit() {
  formError.value = ''
  const isNew = editing.value === '__new__'
  try {
    await save.submit({
      name: isNew ? form.name : editing.value,
      start_time: form.start_time,
      end_time: form.end_time,
      begin_check_in_before_shift_start_time: form.begin_check_in_before_shift_start_time,
      allow_check_out_after_shift_end_time: form.allow_check_out_after_shift_end_time,
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
        Shift types
      </h2>
      <Button
        variant="outline"
        @click="startCreate"
      >
        New shift type
      </Button>
    </div>

    <div class="surface-card elev-1 divide-y divide-outline-gray-1">
      <div
        v-for="row in props.shiftTypes"
        :key="row.name"
        class="flex items-center justify-between gap-3 px-4 py-3"
        data-testid="settings-shift-type-row"
      >
        <div class="min-w-0">
          <p class="truncate font-medium text-ink-gray-9">
            {{ row.name }}
          </p>
          <p class="truncate text-sm text-ink-gray-6">
            {{ row.start_time }} to {{ row.end_time }}
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
        v-if="!props.shiftTypes.length"
        class="px-4 py-6 text-sm text-ink-gray-6"
      >
        No shift types yet.
      </p>
    </div>

    <div
      v-if="editing"
      class="surface-card elev-1 space-y-4 p-4"
      data-testid="settings-shift-type-form"
    >
      <FormControl
        v-model="form.name"
        label="Name"
        :disabled="editing !== '__new__'"
      />
      <div class="grid grid-cols-2 gap-3">
        <FormControl
          v-model="form.start_time"
          type="time"
          label="Start time"
        />
        <FormControl
          v-model="form.end_time"
          type="time"
          label="End time"
        />
      </div>
      <FormControl
        v-model="form.begin_check_in_before_shift_start_time"
        type="number"
        label="Allow check-in this many minutes before the shift starts"
      />
      <FormControl
        v-model="form.allow_check_out_after_shift_end_time"
        type="number"
        label="Allow check-out this many minutes after the shift ends"
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
