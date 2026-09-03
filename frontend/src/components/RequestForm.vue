<script setup>
import { ref } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'
import { uploadFile } from '@/lib/api'

const props = defineProps({
  initialCategory: { type: String, default: '' },
  initialSubject: { type: String, default: '' },
})
const emit = defineEmits(['created', 'cancel'])

const CATEGORIES = ['HR Letter', 'IT / Asset', 'Payroll Question', 'Other']

const category = ref(props.initialCategory || CATEGORIES[0])
const subject = ref(props.initialSubject || '')
const details = ref('')
const file = ref(null)
const error = ref('')

const create = createResource({ url: 'frappe.client.insert', method: 'POST' })
const uploading = ref(false)

function onFileChange(e) {
  file.value = e.target.files?.[0] || null
}

async function submit() {
  error.value = ''
  try {
    const doc = await create.submit({
      doc: JSON.stringify({
        doctype: 'HR Request',
        category: category.value,
        subject: subject.value,
        details: details.value,
      }),
    })
    if (file.value) {
      uploading.value = true
      await uploadFile(file.value, { doctype: 'HR Request', docname: doc.name })
      uploading.value = false
    }
    emit('created', doc)
  } catch (e) {
    uploading.value = false
    error.value = e?.messages?.[0] || 'Could not send your request. Please try again.'
  }
}
</script>

<template>
  <form
    class="space-y-4"
    @submit.prevent="submit"
  >
    <FormControl
      v-model="category"
      type="select"
      label="Category"
      :options="CATEGORIES.map((c) => ({ label: c, value: c }))"
    />
    <FormControl
      v-model="subject"
      type="text"
      label="Subject"
      required
    />
    <FormControl
      v-model="details"
      type="textarea"
      label="Details"
    />
    <div>
      <label class="mb-1 block text-sm text-ink-gray-7">Attach a file (optional)</label>
      <input
        type="file"
        @change="onFileChange"
      >
    </div>

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
        type="submit"
        :loading="create.loading || uploading"
      >
        Send request
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
