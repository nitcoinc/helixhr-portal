<script setup>
// Plan 2026-10-15-003 KTD8: shared thread and comment box for requests.
// Used on the Requests page (employee + handler) and Approvals detail.
import { ref, computed } from 'vue'
import { call } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'

const props = defineProps({
  /** Thread entries: [{author, message, on}, ...] */
  entries: { type: Array, default: () => [] },
  /** Whether the caller may leave a comment. */
  canComment: { type: Boolean, default: false },
  /** Posting mode: 'employee' or 'handler'. Determines which endpoint is called. */
  mode: { type: String, default: 'employee' },
  /** Whether the request is closed (Done or Rejected). */
  closed: { type: Boolean, default: false },
  /** The request name, for the API call. */
  requestName: { type: String, default: '' },
  /** The handler's label prefix for the comment box. */
  employeeName: { type: String, default: '' },
  /** Expected modified timestamp for employee replies (status-change case). */
  expectedModified: { type: String, default: null },
})

const emit = defineEmits(['posted'])

const message = ref('')
const posting = ref(false)
const postError = ref('')
const detailsMax = 6000

const isEmployee = computed(() => props.mode === 'employee')
const placeholder = computed(() => {
  if (props.closed) return ''
  if (isEmployee.value) return props.expectedModified ? 'Reply…' : 'Add a comment…'
  return props.employeeName ? `Comment (visible to ${props.employeeName})…` : 'Comment…'
})
const buttonLabel = computed(() => {
  if (props.closed) return ''
  if (posting.value) return 'Sending…'
  if (isEmployee.value && props.expectedModified) return 'Reply'
  return 'Comment'
})

async function post() {
  const text = message.value.trim()
  if (!text || posting.value || props.closed) return
  if (text.length > detailsMax) {
    postError.value = `Too long. Keep it under ${detailsMax} characters.`
    return
  }

  posting.value = true
  postError.value = ''
  try {
    if (isEmployee.value) {
      await call('helixhr.api.reply_to_my_request', {
        name: props.requestName,
        message: text,
        expected_modified: props.expectedModified,
      })
    } else {
      await call('helixhr.api.comment_on_request', {
        name: props.requestName,
        message: text,
      })
    }
    message.value = ''
    emit('posted')
  } catch (failure) {
    postError.value = failure?.messages?.[0] || "That didn't send. Try again."
  } finally {
    posting.value = false
  }
}
</script>

<template>
  <section
    class="space-y-2"
    data-testid="request-thread"
  >
    <!-- Thread entries -->
    <ul
      v-if="entries.length"
      class="space-y-2"
      aria-label="Conversation"
    >
      <li
        v-for="(entry, index) in entries"
        :key="index"
        class="surface-inset rounded-md p-3 text-sm"
      >
        <p class="text-xs font-medium text-ink-gray-5">
          {{ entry.author || 'Employee' }}
          · {{ entry.on ? formatDateTime(entry.on) : '' }}
        </p>
        <p class="mt-1 whitespace-pre-line text-ink-gray-8">
          {{ entry.message }}
        </p>
      </li>
    </ul>
    <p
      v-else
      class="text-sm text-ink-gray-5"
    >
      No details were written with this request.
    </p>

    <!-- Comment box -->
    <div v-if="!closed && canComment">
      <textarea
        v-model="message"
        :placeholder="placeholder"
        class="min-h-20 w-full rounded-md border border-outline-gray-2 bg-surface-white p-2 text-sm text-ink-gray-8"
        :disabled="posting"
        rows="2"
      ></textarea>
      <div class="mt-1 flex items-center gap-2">
        <button
          type="button"
          class="inline-flex min-h-11 cursor-pointer items-center rounded-lg bg-signal px-4 text-sm font-medium text-white disabled:opacity-50"
          :disabled="!message.trim() || posting"
          @click="post"
        >
          {{ buttonLabel }}
        </button>
        <span
          v-if="message.length > detailsMax * 0.8"
          class="text-xs text-ink-amber-6"
        >{{ message.length }}/{{ detailsMax }}</span>
      </div>
      <p
        v-if="postError"
        class="mt-1 text-sm text-ink-red-4"
        role="alert"
      >
        {{ postError }}
      </p>
    </div>

    <!-- Closed state -->
    <div
      v-if="closed"
      class="mt-2 text-sm text-ink-gray-6"
    >
      <p>This request is closed.</p>
      <router-link
        v-if="isEmployee"
        :to="{ name: 'NewRequest' }"
        class="inline-flex items-center text-blue-700 underline underline-offset-2"
      >
        Start a new request
      </router-link>
    </div>
  </section>
</template>