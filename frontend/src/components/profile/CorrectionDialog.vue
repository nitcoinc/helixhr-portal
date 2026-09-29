<script setup>
import { computed, ref, watch } from 'vue'
import { Dialog } from 'frappe-ui'
import RequestForm from '@/components/RequestForm.vue'
import { correctionDraft } from '@/lib/profileCorrection'

// Plan 2026-09-29-001 U4. "Request a correction", without leaving Profile.
//
// `field` is what the employee picked -- `{ label, display, masked, table }`
// -- and `category` the Profile correction category the server reported
// active. RequestForm owns every send state (busy, refusal, lost response);
// this only frames it and says where the request went. frappe-ui's Dialog
// traps focus, closes on Esc and hands focus back to the button that opened
// it.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  field: { type: Object, default: null },
  category: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue'])
const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const sent = ref(null)
const draft = computed(() => (props.field ? correctionDraft(props.field) : null))

// A fresh draft every time the dialog opens: RequestForm reads its initial
// props once, so it is re-keyed per field rather than reused.
watch(open, (isOpen) => {
  if (isOpen) sent.value = null
})

function onCreated({ name }) {
  sent.value = name
}
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title: sent ? 'Request sent' : 'Request a correction' }"
  >
    <template #body-content>
      <div
        v-if="sent"
        class="space-y-3"
        aria-live="polite"
      >
        <p class="text-ink-gray-7">
          HR has your request and will reply there. Your profile changes once HR updates your record.
        </p>
        <router-link
          :to="{ name: 'RequestDetail', params: { name: sent } }"
          class="inline-flex min-h-11 cursor-pointer items-center font-medium text-blue-700 hover:underline"
          @click="open = false"
        >
          View your request
        </router-link>
      </div>
      <RequestForm
        v-else-if="draft"
        :key="`${field.label}-${field.display}`"
        :initial-category="category"
        :initial-subject="draft.subject"
        :initial-details="draft.details"
        @created="onCreated"
        @cancel="open = false"
      />
    </template>
  </Dialog>
</template>
