<script setup>
import { ref, watch, computed } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'
import { toPlainLeaveError } from '@/lib/errorMap'

const props = defineProps({
  employee: { type: String, required: true },
})
const emit = defineEmits(['applied', 'cancel'])

const today = new Date().toISOString().slice(0, 10)

const leaveType = ref('')
const fromDate = ref(today)
const toDate = ref(today)
const halfDay = ref(false)
const halfDayDate = ref(today)
const description = ref('')
const error = ref('')

const leaveTypes = createResource({
  url: 'hrms.api.get_leave_types',
  params: { employee: props.employee, date: today },
  auto: true,
})

const approvalDetails = createResource({
  url: 'hrms.api.get_leave_approval_details',
  params: { employee: props.employee },
  auto: true,
})

const apply = createResource({
  url: 'frappe.client.insert',
  method: 'POST',
})

const submitting = computed(() => apply.loading)

watch(halfDay, (value) => {
  if (value) toDate.value = fromDate.value
})

async function submit() {
  error.value = ''
  try {
    const doc = await apply.submit({
      doc: {
        doctype: 'Leave Application',
        employee: props.employee,
        leave_type: leaveType.value,
        from_date: fromDate.value,
        to_date: halfDay.value ? fromDate.value : toDate.value,
        half_day: halfDay.value ? 1 : 0,
        half_day_date: halfDay.value ? halfDayDate.value : undefined,
        description: description.value,
        leave_approver: approvalDetails.data?.leave_approver,
      },
    })
    emit('applied', doc)
  } catch (e) {
    error.value = toPlainLeaveError(e)
  }
}
</script>

<template>
  <form
    class="space-y-4"
    @submit.prevent="submit"
  >
    <FormControl
      v-model="leaveType"
      type="select"
      label="Leave type"
      :options="[{ label: 'Select a leave type', value: '' }, ...(leaveTypes.data || []).map((t) => ({ label: t, value: t }))]"
      required
    />

    <div class="grid grid-cols-2 gap-3">
      <FormControl
        v-model="fromDate"
        type="date"
        label="From"
        required
      />
      <FormControl
        v-model="toDate"
        type="date"
        label="To"
        :disabled="halfDay"
        required
      />
    </div>

    <label class="flex items-center gap-2 text-sm text-ink-gray-7">
      <input
        v-model="halfDay"
        type="checkbox"
      >
      Half day
    </label>

    <FormControl
      v-if="halfDay"
      v-model="halfDayDate"
      type="date"
      label="Half-day date"
    />

    <FormControl
      v-model="description"
      type="textarea"
      label="Reason"
    />

    <p
      v-if="approvalDetails.data && !approvalDetails.data.leave_approver"
      class="text-sm text-ink-gray-5"
    >
      No approver set for you yet. <router-link
        to="/requests"
        class="underline"
      >
        Ask HR
      </router-link>.
    </p>

    <p
      v-if="error"
      class="text-sm text-ink-red-4"
    >
      {{ error }}
    </p>

    <div class="flex items-center gap-2">
      <Button
        variant="solid"
        theme="blue"
        :loading="submitting"
        type="submit"
      >
        Ask for leave
      </Button>
      <Button
        variant="subtle"
        type="button"
        @click="emit('cancel')"
      >
        Cancel
      </Button>
    </div>
  </form>
</template>
