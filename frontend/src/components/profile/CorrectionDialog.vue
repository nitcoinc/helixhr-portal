<script setup>
import { computed, ref, watch } from 'vue'
import { Button, Dialog } from 'frappe-ui'
import RequestForm from '@/components/RequestForm.vue'
import { createCorrectionRequest } from '@/lib/api'
import {
  CORRECTION_VALUE_MAX,
  correctionDraft,
  correctionEntryError,
  correctionParams,
} from '@/lib/profileCorrection'

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

// Plan 2026-10-02-001 U14: a correctable field (bank details) takes the new
// value typed twice plus a required proof, sent in one multipart call. The
// typed value lives only in these refs and is cleared whenever the dialog
// opens or closes -- never cached, never logged.
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.docx,.xlsx'
const newValue = ref('')
const confirmValue = ref('')
const proof = ref(null)
const sending = ref(false)
const sendError = ref('')
const operationKey = ref('')
const mismatch = computed(() => correctionEntryError(newValue.value, confirmValue.value))
const params = computed(() =>
  correctionParams(draft.value, {
    value: newValue.value,
    confirm: confirmValue.value,
    category: props.category,
    operationKey: operationKey.value,
  }),
)
const canSend = computed(() => !!params.value && !!proof.value && !sending.value)

function newKey() {
  return typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : String(Date.now())
}

function reset() {
  newValue.value = ''
  confirmValue.value = ''
  proof.value = null
  sendError.value = ''
  operationKey.value = newKey()
}

// A fresh draft every time the dialog opens: RequestForm reads its initial
// props once, so it is re-keyed per field rather than reused.
watch(open, (isOpen) => {
  reset()
  if (isOpen) sent.value = null
})

async function sendCorrection() {
  if (!canSend.value) return
  sending.value = true
  sendError.value = ''
  try {
    const created = await createCorrectionRequest(proof.value, params.value)
    newValue.value = ''
    confirmValue.value = ''
    sent.value = created.name
  } catch (e) {
    const status = e?.response?.status
    // A real refusal: the next press is a new attempt. An unknown outcome
    // keeps the key so a retry returns the request it may already have made.
    if (status && status < 500 && status !== 408 && status !== 499) operationKey.value = newKey()
    sendError.value =
      e?.messages?.[0] ||
      'We couldn’t send that. Your request may already have gone through — check Requests before sending again.'
  } finally {
    sending.value = false
  }
}

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
      <form
        v-else-if="draft?.correction_field"
        class="space-y-4"
        data-testid="structured-correction"
        @submit.prevent="sendCorrection"
      >
        <div>
          <label
            class="label mb-1 block"
            for="correction-new"
          >New value</label>
          <input
            id="correction-new"
            v-model="newValue"
            type="text"
            class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-ink-gray-9 focus:border-outline-gray-4"
            autocomplete="off"
            spellcheck="false"
            :maxlength="CORRECTION_VALUE_MAX"
            required
          >
        </div>
        <div>
          <label
            class="label mb-1 block"
            for="correction-confirm"
          >Type it again</label>
          <input
            id="correction-confirm"
            v-model="confirmValue"
            type="text"
            class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-ink-gray-9 focus:border-outline-gray-4"
            autocomplete="off"
            spellcheck="false"
            :maxlength="CORRECTION_VALUE_MAX"
            required
            :aria-invalid="!!mismatch"
            :aria-describedby="mismatch ? 'correction-mismatch' : undefined"
          >
          <p
            v-if="mismatch"
            id="correction-mismatch"
            class="mt-1 text-sm text-signal"
            role="alert"
          >
            {{ mismatch }}
          </p>
        </div>
        <div>
          <label
            class="label mb-1 block"
            for="correction-proof"
          >Proof (required)</label>
          <input
            id="correction-proof"
            type="file"
            class="block w-full text-sm text-ink-gray-7"
            :accept="ACCEPT"
            required
            @change="proof = $event.target.files?.[0] || null"
          >
          <p class="mt-1 text-sm text-ink-gray-5">
            A bank letter or cancelled cheque · PDF, PNG, JPEG, Word or Excel · up to
            <span class="tabular">10</span> MB
          </p>
        </div>
        <p class="text-sm text-ink-gray-6">
          We email your work and personal addresses now and again when HR applies the change. If
          your personal email changed in the last 72 hours, HR has to wait until that passes.
        </p>
        <p
          v-if="sending"
          class="text-sm text-ink-gray-6"
          aria-live="polite"
        >
          Sending your request and uploading the proof…
        </p>
        <p
          v-if="sendError"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ sendError }}
        </p>
        <div class="flex items-center gap-2">
          <Button
            variant="solid"
            theme="blue"
            type="submit"
            :loading="sending"
            :disabled="!canSend"
          >
            Send to HR
          </Button>
          <Button
            variant="subtle"
            type="button"
            @click="open = false"
          >
            Cancel
          </Button>
        </div>
      </form>
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
