<script setup>
import { reactive, ref } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

// P5-KTD12: the named set -- name, from date, to date, weekly off day, and
// the holiday rows. `holidays` is fetched fresh (with its child rows) only
// when a list is opened for editing; the summary list doesn't carry it.
const props = defineProps({
  holidayLists: { type: Array, required: true },
})
const emit = defineEmits(['saved'])

const editing = ref(null)
const form = reactive({
  holiday_list_name: '',
  from_date: '',
  to_date: '',
  weekly_off: 'Sunday',
  holidays: [],
})
const formError = ref('')

const detail = createResource({
  url: 'frappe.client.get',
  makeParams: (name) => ({ doctype: 'Holiday List', name }),
  auto: false,
})

function startCreate() {
  editing.value = '__new__'
  Object.assign(form, {
    holiday_list_name: '',
    from_date: '',
    to_date: '',
    weekly_off: 'Sunday',
    holidays: [],
  })
  formError.value = ''
}

async function startEdit(row) {
  editing.value = row.name
  formError.value = ''
  Object.assign(form, {
    holiday_list_name: row.holiday_list_name,
    from_date: row.from_date || '',
    to_date: row.to_date || '',
    weekly_off: row.weekly_off || 'Sunday',
    holidays: [],
  })
  const doc = await detail.submit(row.name)
  form.holidays = (doc.holidays || []).map((h) => ({
    holiday_date: h.holiday_date,
    description: h.description || '',
  }))
}

function addHoliday() {
  form.holidays.push({ holiday_date: '', description: '' })
}

function removeHoliday(index) {
  form.holidays.splice(index, 1)
}

function cancel() {
  editing.value = null
  formError.value = ''
}

const save = createResource({ url: 'helixhr.api.save_holiday_list', method: 'POST' })

async function submit() {
  formError.value = ''
  const isNew = editing.value === '__new__'
  try {
    await save.submit({
      name: isNew ? form.holiday_list_name : editing.value,
      holiday_list_name: form.holiday_list_name,
      from_date: form.from_date,
      to_date: form.to_date,
      weekly_off: form.weekly_off,
      holidays: form.holidays.filter((h) => h.holiday_date),
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
        Holiday lists
      </h2>
      <Button
        variant="outline"
        @click="startCreate"
      >
        New holiday list
      </Button>
    </div>

    <div class="surface-card elev-1 divide-y divide-outline-gray-1">
      <div
        v-for="row in props.holidayLists"
        :key="row.name"
        class="flex items-center justify-between gap-3 px-4 py-3"
        data-testid="settings-holiday-list-row"
      >
        <div class="min-w-0">
          <p class="truncate font-medium text-ink-gray-9">
            {{ row.holiday_list_name }}
          </p>
          <p class="truncate text-sm text-ink-gray-6">
            {{ row.from_date }} to {{ row.to_date }}
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
        v-if="!props.holidayLists.length"
        class="px-4 py-6 text-sm text-ink-gray-6"
      >
        No holiday lists yet.
      </p>
    </div>

    <div
      v-if="editing"
      class="surface-card elev-1 space-y-4 p-4"
      data-testid="settings-holiday-list-form"
    >
      <FormControl
        v-model="form.holiday_list_name"
        label="Name"
        :disabled="editing !== '__new__'"
      />
      <div class="grid grid-cols-2 gap-3">
        <FormControl
          v-model="form.from_date"
          type="date"
          label="From date"
        />
        <FormControl
          v-model="form.to_date"
          type="date"
          label="To date"
        />
      </div>
      <div class="space-y-1.5">
        <label
          for="settings-holiday-list-weekly-off"
          class="text-sm text-ink-gray-7"
        >
          Weekly off day
        </label>
        <select
          id="settings-holiday-list-weekly-off"
          v-model="form.weekly_off"
          class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
        >
          <option
            v-for="day in WEEKDAYS"
            :key="day"
            :value="day"
          >
            {{ day }}
          </option>
        </select>
      </div>

      <div>
        <div class="mb-2 flex items-center justify-between">
          <span class="text-sm font-medium text-ink-gray-7">Holidays</span>
          <Button
            variant="ghost"
            @click="addHoliday"
          >
            Add holiday
          </Button>
        </div>
        <div
          v-for="(holiday, index) in form.holidays"
          :key="index"
          class="mb-2 flex items-end gap-2"
          data-testid="settings-holiday-row"
        >
          <FormControl
            v-model="holiday.holiday_date"
            type="date"
            label="Date"
            class="flex-1"
          />
          <FormControl
            v-model="holiday.description"
            label="Description"
            class="flex-1"
          />
          <Button
            variant="ghost"
            theme="red"
            @click="removeHoliday(index)"
          >
            Remove
          </Button>
        </div>
        <p
          v-if="!form.holidays.length"
          class="text-sm text-ink-gray-6"
        >
          No holidays added yet.
        </p>
      </div>

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
