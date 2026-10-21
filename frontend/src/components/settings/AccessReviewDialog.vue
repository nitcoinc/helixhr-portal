<script setup>
import { computed, ref, useId, watch } from 'vue'
import { Button, Dialog } from 'frappe-ui'
import { REASON_MAX, needsReason, reasonValid } from '@/lib/accessProfiles'

// Plan 2026-10-19-001 U7 (R15, R22): the review step before a save. Gains
// and losses in words, a "High risk" word on every high-risk line, and one
// reason field that is required only when such a line is present. frappe-ui's
// Dialog is the bottom sheet on a phone (focus trap, Escape, focus return).
// A refusal stays in here; a stale token keeps the draft and offers Reload.
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** `diffDraft`'s answer: `{gains, losses}` of `{text, highRisk}`. */
  diff: { type: Object, required: true },
  name: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  /** The server's refusal, as a sentence. */
  error: { type: String, default: '' },
  /** The save was refused as stale: someone changed this person. */
  stale: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'confirm', 'reload'])

const id = useId()
const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})
const reason = ref('')
watch(
  () => props.modelValue,
  (value) => {
    if (value) reason.value = ''
  },
)

const required = computed(() => needsReason(props.diff))
const valid = computed(() => reasonValid(props.diff, reason.value))
const tooLong = computed(() => reason.value.trim().length > REASON_MAX)

function confirm() {
  if (!valid.value || props.busy || props.stale) return
  emit('confirm', reason.value.trim())
}
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title: name ? `Review changes for ${name}` : 'Review changes', size: 'md' }"
  >
    <template #body-content>
      <form
        class="space-y-4"
        data-testid="access-review-dialog"
        @submit.prevent="confirm"
      >
        <section v-if="diff.gains.length">
          <h3 class="label">
            They get
          </h3>
          <ul
            class="mt-1 space-y-1"
            data-testid="access-review-gains"
          >
            <li
              v-for="item in diff.gains"
              :key="item.text"
              class="flex flex-wrap items-center gap-2 text-sm text-ink-gray-9"
            >
              {{ item.text }}
              <span
                v-if="item.highRisk"
                class="rounded-full bg-surface-red-2 px-2 py-0.5 text-xs font-medium text-ink-red-4"
                data-testid="access-high-risk"
              >High risk</span>
            </li>
          </ul>
        </section>
        <section v-if="diff.losses.length">
          <h3 class="label">
            They lose
          </h3>
          <ul
            class="mt-1 space-y-1"
            data-testid="access-review-losses"
          >
            <li
              v-for="item in diff.losses"
              :key="item.text"
              class="text-sm text-ink-gray-9"
            >
              {{ item.text }}
            </li>
          </ul>
        </section>

        <div>
          <label
            :for="`${id}-reason`"
            class="mb-1 block text-sm text-ink-gray-7"
          >{{ required ? 'Reason (required for high-risk access)' : 'Reason (optional)' }}</label>
          <textarea
            :id="`${id}-reason`"
            v-model="reason"
            rows="3"
            :maxlength="REASON_MAX"
            :aria-required="required ? 'true' : undefined"
            :aria-describedby="`${id}-reason-help`"
            class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            data-testid="access-reason"
          />
          <p
            :id="`${id}-reason-help`"
            class="mt-1 text-xs text-ink-gray-6"
            :class="tooLong ? 'text-ink-red-4' : ''"
          >
            {{ required ? 'System Managers and Manage access holders are told, with this reason.' : 'Shown in this person\'s access history.' }}
            {{ reason.trim().length }}/{{ REASON_MAX }}
          </p>
        </div>

        <div
          v-if="stale"
          class="surface-alert p-3 text-sm"
          role="alert"
          data-testid="access-stale"
        >
          <p>Someone changed this person's access. Reload and re-apply.</p>
          <Button
            class="mt-2 min-h-11"
            variant="outline"
            type="button"
            data-testid="access-reload"
            @click="emit('reload')"
          >
            Reload
          </Button>
        </div>
        <p
          v-else-if="error"
          class="surface-alert p-3 text-sm"
          role="alert"
          data-testid="access-refusal"
        >
          {{ error }}
        </p>

        <div class="sticky bottom-0 -mx-1 flex items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3">
          <Button
            variant="solid"
            theme="blue"
            type="submit"
            class="min-h-11"
            :loading="busy"
            :disabled="!valid || busy || stale"
            data-testid="access-confirm"
          >
            Confirm
          </Button>
          <Button
            variant="subtle"
            type="button"
            class="min-h-11"
            @click="open = false"
          >
            Cancel
          </Button>
        </div>
      </form>
    </template>
  </Dialog>
</template>
