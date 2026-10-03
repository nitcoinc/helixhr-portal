<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { Button } from 'frappe-ui'
import { call } from '@/lib/api'
import { spokenValue } from '@/lib/profileCorrection'

// Plan 2026-10-02-001 U14 (R28). HR's half of a profile correction: the
// field, masked current -> masked proposed, the proof, and a logged Reveal.
//
// The full value lives in this component only, never in the detail resource
// or any store, and is dropped when it auto-hides, when the request changes
// (the parent keys this by request name) and when the user navigates away
// (unmount). It is never logged.
const props = defineProps({
  request: { type: String, required: true },
  correction: { type: Object, required: true },
  attachments: { type: Array, default: () => [] },
  canReveal: { type: Boolean, default: false },
})

const REVEAL_SECONDS = 30

const confirming = ref(false)
const revealed = ref('')
const revealing = ref(false)
const revealError = ref('')
let hideTimer = null

function hide() {
  revealed.value = ''
  clearTimeout(hideTimer)
  hideTimer = null
}

async function reveal() {
  revealError.value = ''
  revealing.value = true
  try {
    const { value } = await call('helixhr.api.reveal_correction_value', { name: props.request })
    revealed.value = value
    hideTimer = setTimeout(hide, REVEAL_SECONDS * 1000)
  } catch (error) {
    revealError.value = error?.messages?.[0] || 'The full value couldn’t be shown. Try again.'
  } finally {
    revealing.value = false
    confirming.value = false
  }
}

onBeforeUnmount(hide)
</script>

<template>
  <section
    class="mt-3 rounded-lg border border-outline-gray-2 p-3 text-sm"
    data-testid="correction-review"
    aria-label="Requested correction"
  >
    <p class="font-medium text-ink-gray-9">
      Change {{ correction.label }}
    </p>
    <dl class="mt-2 space-y-1">
      <div class="flex justify-between gap-3">
        <dt class="text-ink-gray-6">
          Now
        </dt>
        <dd
          class="tabular text-ink-gray-9"
          data-testid="correction-current"
          :aria-label="spokenValue(correction.current_masked || 'Not recorded')"
        >
          {{ correction.current_masked || 'Not recorded' }}
        </dd>
      </div>
      <div class="flex justify-between gap-3">
        <dt class="text-ink-gray-6">
          Proposed
        </dt>
        <dd
          class="tabular text-ink-gray-9"
          data-testid="correction-proposed"
          aria-live="polite"
        >
          <span v-if="revealed">{{ revealed }}</span>
          <span
            v-else
            :aria-label="spokenValue(correction.proposed_masked)"
          >{{ correction.proposed_masked }}</span>
        </dd>
      </div>
    </dl>

    <div
      v-if="canReveal"
      class="mt-2"
    >
      <Button
        v-if="revealed"
        variant="subtle"
        @click="hide"
      >
        Hide
      </Button>
      <template v-else-if="confirming">
        <p class="text-ink-gray-7">
          Showing the full value is recorded on this request. It hides again after
          {{ REVEAL_SECONDS }} seconds.
        </p>
        <div class="mt-2 flex gap-2">
          <Button
            variant="solid"
            :loading="revealing"
            @click="reveal"
          >
            Show full value
          </Button>
          <Button
            variant="subtle"
            @click="confirming = false"
          >
            Cancel
          </Button>
        </div>
      </template>
      <Button
        v-else
        variant="outline"
        @click="confirming = true"
      >
        Reveal
      </Button>
      <p
        v-if="revealError"
        class="mt-1 text-signal"
        role="alert"
      >
        {{ revealError }}
      </p>
    </div>

    <ul
      v-if="attachments.length"
      class="mt-3 flex flex-wrap gap-2"
      aria-label="Proof"
    >
      <li
        v-for="attachment in attachments"
        :key="attachment.name"
      >
        <a
          class="inline-flex min-h-11 items-center rounded-full border border-outline-gray-2 px-3 text-ink-gray-8 hover:bg-surface-gray-2"
          :href="attachment.file_url"
          target="_blank"
          rel="noopener noreferrer"
        >Proof: {{ attachment.file_name }}</a>
      </li>
    </ul>

    <p class="mt-3 text-ink-gray-6">
      Marking this Done writes the new value to the employee record.
    </p>
  </section>
</template>
