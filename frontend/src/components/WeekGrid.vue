<script setup>
import { FormControl, Button } from 'frappe-ui'

const props = defineProps({
  rows: { type: Array, required: true },
  projects: { type: Array, required: true },
  readOnly: { type: Boolean, default: false },
  weekDates: { type: Array, required: true },
})
const emit = defineEmits(['add-row', 'remove-row'])

function tasksFor(projectName) {
  return props.projects.find((p) => p.name === projectName)?.tasks || []
}
</script>

<template>
  <div class="space-y-3">
    <div
      v-for="(row, index) in rows"
      :key="index"
      class="grid grid-cols-1 gap-2 rounded-lg border border-outline-gray-2 p-3 sm:grid-cols-5 sm:items-end"
    >
      <FormControl
        v-model="row.date"
        type="select"
        label="Day"
        :disabled="readOnly"
        :options="weekDates.map((d) => ({ label: d.label, value: d.iso }))"
      />
      <FormControl
        v-model="row.project"
        type="select"
        label="Project"
        :disabled="readOnly"
        :options="[{ label: 'Select a project', value: '' }, ...projects.map((p) => ({ label: p.project_name || p.name, value: p.name }))]"
      />
      <FormControl
        v-model="row.task"
        type="select"
        label="Task"
        :disabled="readOnly"
        :options="[{ label: 'No task', value: '' }, ...tasksFor(row.project).map((t) => ({ label: t.subject, value: t.name }))]"
      />
      <FormControl
        v-model="row.hours"
        type="number"
        label="Hours"
        :disabled="readOnly"
        step="0.25"
        min="0.25"
        max="24"
      />
      <FormControl
        v-model="row.note"
        type="text"
        label="Note"
        :disabled="readOnly"
      />
      <Button
        v-if="!readOnly"
        variant="ghost"
        theme="red"
        class="sm:col-span-5"
        @click="emit('remove-row', index)"
      >
        Remove row
      </Button>
    </div>

    <Button
      v-if="!readOnly"
      variant="subtle"
      @click="emit('add-row')"
    >
      Add row
    </Button>
  </div>
</template>
