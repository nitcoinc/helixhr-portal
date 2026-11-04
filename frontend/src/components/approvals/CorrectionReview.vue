<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
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

// Plan 2026-11-03-001 U9: a row correction lists every field; a legacy one
// is a single row with no id. Each row reveals on its own, one comment each.
const rows = computed(() =>
  props.correction.rows?.length
    ? props.correction.rows
    : [{ row: null, label: props.correction.label, ...props.correction }],
)

const confirming = ref(undefined)
const revealed = ref({})
const revealing = ref(false)
const revealError = ref('')
const timers = {}

function keyOf(row) {
  return row.row || '_'
}

function hide(row) {
  const key = keyOf(row)
  const { [key]: _gone, ...rest } = revealed.value
  revealed.value = rest
  clearTimeout(timers[key])
  delete timers[key]
}

function hideAll() {
  revealed.value = {}
  for (const key of Object.keys(timers)) clearTimeout(timers[key])
}

async function reveal(row) {
  revealError.value = ''
  revealing.value = true
  try {
    const params = { name: props.request, ...(row.row ? { row: row.row } : {}) }
    const { value } = await call('helixhr.api.reveal_correction_value', params)
    revealed.value = { ...revealed.value, [keyOf(row)]: value }
    timers[keyOf(row)] = setTimeout(() => hide(row), REVEAL_SECONDS * 1000)
  } catch (error) {
    revealError.value = error?.messages?.[0] || 'The full value couldn’t be shown. Try again.'
  } finally {
    revealing.value = false
    confirming.value = undefined
  }
}

onBeforeUnmount(hideAll)
</script>

<template>
  <section
    class="mt-3 rounded-lg border border-outline-gray-2 p-3 text-sm"
    data-testid="correction-review"
    aria-label="Requested correction"
  >
    <p class="font-medium text-ink-gray-9">
      Change {{ rows.length === 1 ? rows[0].label : `${rows.length} details` }}
    </p>
    <!-- A list of label/now/proposed cards rather than a table: it reads the
         same on a phone and nothing scrolls sideways. -->
    <ul class="mt-2 divide-y divide-outline-gray-1">
      <li
        v-for="row in rows"
        :key="keyOf(row)"
        class="py-2"
        data-testid="correction-row"
      >
        <p
          v-if="rows.length > 1"
          class="font-medium text-ink-gray-8"
        >
          {{ row.label }}
        </p>
        <dl class="mt-1 space-y-1">
          <div class="flex justify-between gap-3">
            <dt class="text-ink-gray-6">
              Now
            </dt>
            <dd
              class="tabular text-ink-gray-9"
              data-testid="correction-current"
              :aria-label="spokenValue(row.current_masked || 'Not recorded')"
            >
              {{ row.current_masked || 'Not recorded' }}
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
              <span v-if="revealed[keyOf(row)]">{{ revealed[keyOf(row)] }}</span>
              <span
                v-else
                :aria-label="spokenValue(row.proposed_masked)"
              >{{ row.proposed_masked }}</span>
            </dd>
          </div>
        </dl>
        <!-- Only a masked value has anything to reveal. A legacy row has no
             `masked` flag and always was one. -->
        <div
          v-if="canReveal && row.masked !== false"
          class="mt-2"
        >
          <Button
            v-if="revealed[keyOf(row)]"
            variant="subtle"
            :aria-label="`Hide ${row.label}`"
            @click="hide(row)"
          >
            Hide
          </Button>
          <template v-else-if="confirming === keyOf(row)">
            <p class="text-ink-gray-7">
              Showing the full value is recorded on this request. It hides again after
              {{ REVEAL_SECONDS }} seconds.
            </p>
            <div class="mt-2 flex gap-2">
              <Button
                variant="solid"
                :loading="revealing"
                @click="reveal(row)"
              >
                Show full value
              </Button>
              <Button
                variant="subtle"
                @click="confirming = undefined"
              >
                Cancel
              </Button>
            </div>
          </template>
          <Button
            v-else
            variant="outline"
            :aria-label="`Reveal ${row.label}`"
            @click="confirming = keyOf(row)"
          >
            Reveal
          </Button>
        </div>
      </li>
    </ul>
    <p
      v-if="revealError"
      class="mt-1 text-ink-red-4"
      role="alert"
    >
      {{ revealError }}
    </p>

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
      Marking this Done writes every new value to the employee record, or none if one is stale.
    </p>
  </section>
</template>
