<script setup>
// Plan 2026-10-15-003 KTD8, Plan 2026-10-16-001 U7:
// Shared thread and message box for requests.
import { ref, computed, nextTick, useId } from 'vue'
import { call } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'
import { Button } from 'frappe-ui'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  /** Thread entries: [{author, message, on, kind}, ...] */
  entries: { type: Array, default: () => [] },
  /** Whether the caller may leave a comment. */
  canComment: { type: Boolean, default: false },
  /** Posting mode: 'employee' or 'handler'. Determines which endpoint is called. */
  mode: { type: String, default: 'employee' },
  /** Whether the request is closed (Done or Rejected). */
  closed: { type: Boolean, default: false },
  /** The request name, for the API call. */
  requestName: { type: String, default: '' },
  /** The employee's name, for the handler box's label. */
  employeeName: { type: String, default: '' },
  /** Handler / assignee name, for employee reply prompt. */
  handlerName: { type: String, default: '' },
  /** Only while Waiting on Employee: the token the status move is checked against. */
  expectedModified: { type: String, default: null },
  /** Stored state of the request. */
  expectedState: { type: String, default: null },
  /** Whether server currently offers 'Need info' action. */
  canNeedInfo: { type: Boolean, default: false },
  /** On a New request, Need info also assigns it to the sender (KTD4). */
  assignsOnWait: { type: Boolean, default: false },
  /** Milestones to show in-line: [{ label, at }] */
  milestones: { type: Array, default: () => [] },
})

const emit = defineEmits(['posted', 'refetch', 'new-request'])

const message = ref('')
const posting = ref(false)
const postError = ref('')
// R12: set when Send and wait was refused; the offer shows only once the
// refetched detail no longer allows Need info.
const waitRefused = ref(false)
const canSendAsNote = computed(() => waitRefused.value && !props.canNeedInfo)
const box = ref(null)
const boxId = useId()
// The server's `_DETAILS_MAX`; the server still refuses past it.
const detailsMax = 6000

const isEmployee = computed(() => props.mode === 'employee')
const isHandler = computed(() => props.mode === 'handler')

// Employee reply while waiting
const isWaiting = computed(() => isEmployee.value && !!props.expectedModified)

const employeeFirstName = computed(() => {
  const name = props.employeeName || ''
  return name.split(' ')[0] || 'The employee'
})

const boxLabel = computed(() => {
  if (isWaiting.value) {
    return props.handlerName ? `Reply to ${props.handlerName}` : 'Reply'
  }
  if (isEmployee.value) return 'Add a comment'
  return props.employeeName
    ? `Comment (visible to ${props.employeeName})`
    : 'Comment (visible to the employee)'
})

const tooLong = computed(() => message.value.length > detailsMax)

const combinedTimeline = computed(() => {
  const result = []
  for (const entry of props.entries) {
    result.push({
      isMilestone: false,
      author: entry.author,
      message: entry.message,
      on: entry.on,
      kind: entry.kind || 'note',
    })
  }
  for (const ms of props.milestones) {
    if (ms.at) {
      result.push({
        isMilestone: true,
        label: ms.label,
        on: ms.at,
      })
    }
  }
  // Frappe timestamps share one zero-padded site-time format, so they sort as
  // strings; `new Date()` rejects the space-separated form in Safari.
  result.sort((a, b) => String(a.on || '').localeCompare(String(b.on || '')))
  return result
})

async function sendNote() {
  const text = message.value.trim()
  if (!text || posting.value || props.closed) return
  if (tooLong.value) {
    postError.value = `Too long. Keep it under ${detailsMax} characters.`
    return
  }

  posting.value = true
  postError.value = ''
  waitRefused.value = false
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
    await nextTick()
    box.value?.focus()
  } catch (failure) {
    postError.value = failure?.messages?.[0] || "That didn't send. Try again."
  } finally {
    posting.value = false
  }
}

async function sendAndWait() {
  const text = message.value.trim()
  if (!text || posting.value || props.closed) return
  if (tooLong.value) {
    postError.value = `Too long. Keep it under ${detailsMax} characters.`
    return
  }

  posting.value = true
  postError.value = ''
  waitRefused.value = false
  try {
    await call('helixhr.api.act_on_approval', {
      doctype: 'HR Request',
      name: props.requestName,
      action: 'Need info',
      comment: text,
      expected_modified: props.expectedModified,
      expected_state: props.expectedState,
    })
    message.value = ''
    emit('posted')
    await nextTick()
    box.value?.focus()
  } catch (failure) {
    postError.value = failure?.messages?.[0] || "That didn't send. Try again."
    waitRefused.value = true
    emit('refetch')
  } finally {
    posting.value = false
  }
}
</script>

<template>
  <section
    class="space-y-3"
    data-testid="request-thread"
  >
    <!-- Thread entries -->
    <h3 class="label">
      Conversation
    </h3>
    <ul
      v-if="combinedTimeline.length"
      class="space-y-2"
      aria-label="Conversation"
      aria-live="polite"
    >
      <li
        v-for="(item, index) in combinedTimeline"
        :key="index"
      >
        <!-- Milestone -->
        <div
          v-if="item.isMilestone"
          class="flex items-center justify-center py-2 text-xs text-ink-gray-5"
          data-testid="thread-milestone"
        >
          <span class="mr-3 flex-grow border-t border-outline-gray-2" />
          <span>{{ item.label }} · {{ formatDateTime(item.on) }}</span>
          <span class="ml-3 flex-grow border-t border-outline-gray-2" />
        </div>

        <!-- Question entry -->
        <div
          v-else-if="item.kind === 'question'"
          class="rounded-r-md border-l-4 border-l-amber-500 bg-amber-50/50 p-3 text-sm"
          data-testid="thread-question"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800">
              <Icon
                name="requests"
                size="h-3 w-3"
              />
              <span>Question</span>
            </span>
            <span class="text-xs text-ink-gray-5">
              {{ item.author }} · {{ item.on ? formatDateTime(item.on) : '' }}
            </span>
          </div>
          <p class="mt-1 whitespace-pre-line font-medium text-ink-gray-9">
            {{ item.message }}
          </p>
        </div>

        <!-- Declined entry -->
        <div
          v-else-if="item.kind === 'declined'"
          class="rounded-r-md border-l-4 border-l-red-500 bg-red-50/50 p-3 text-sm"
          data-testid="thread-declined"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="inline-flex items-center gap-1 rounded bg-red-100 px-1.5 py-0.5 text-xs font-semibold text-red-800">
              <span>Declined</span>
            </span>
            <span class="text-xs text-ink-gray-5">
              {{ item.author }} · {{ item.on ? formatDateTime(item.on) : '' }}
            </span>
          </div>
          <p class="mt-1 whitespace-pre-line text-ink-gray-8">
            {{ item.message }}
          </p>
        </div>

        <!-- Opening / Note entry -->
        <div
          v-else
          class="surface-inset rounded-md p-3 text-sm"
          :data-testid="item.kind === 'opening' ? 'thread-opening' : 'thread-note'"
        >
          <p class="text-xs font-medium text-ink-gray-5">
            {{ item.author || 'Employee' }}
            · {{ item.on ? formatDateTime(item.on) : '' }}
          </p>
          <p class="mt-1 whitespace-pre-line text-ink-gray-8">
            {{ item.message }}
          </p>
        </div>
      </li>
    </ul>
    <p
      v-else
      class="text-sm text-ink-gray-5"
    >
      No details were written with this request.
    </p>

    <!-- Message box -->
    <div
      v-if="!closed && canComment"
      class="pt-2"
    >
      <label
        :for="boxId"
        class="label"
      >{{ boxLabel }}</label>
      <textarea
        :id="boxId"
        ref="box"
        v-model="message"
        data-testid="thread-box"
        class="min-h-20 w-full rounded-md border border-outline-gray-2 bg-surface-white p-2 text-sm text-ink-gray-8"
        :disabled="posting"
        rows="2"
      />

      <!-- Handler mode: two buttons with plain explanations -->
      <div
        v-if="isHandler"
        class="mt-2 space-y-2"
      >
        <div class="flex flex-wrap items-center gap-2">
          <Button
            variant="solid"
            theme="blue"
            class="min-h-11"
            :disabled="!message.trim() || tooLong || posting"
            data-testid="thread-post"
            @click="sendNote"
          >
            Send
          </Button>
          <button
            v-if="canNeedInfo"
            type="button"
            class="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-outline-gray-3 bg-surface-white px-4 text-sm font-medium text-ink-gray-9 hover:bg-surface-gray-2 disabled:opacity-50"
            :disabled="!message.trim() || tooLong || posting"
            data-testid="thread-send-and-wait"
            @click="sendAndWait"
          >
            Send and wait for reply
          </button>
        </div>
        <p class="text-xs text-ink-gray-5">
          <span class="font-medium text-ink-gray-7">Send:</span> {{ employeeFirstName }} sees this. The request stays with you.
          <template v-if="canNeedInfo">
            <br>
            <span class="font-medium text-ink-gray-7">Send and wait for reply:</span> {{ employeeFirstName }} is asked to reply. The request waits until they do.
            <span
              v-if="assignsOnWait"
              data-testid="need-info-assigns"
            >This also assigns the request to you.</span>
          </template>
        </p>
      </div>

      <!-- Employee mode -->
      <div
        v-else
        class="mt-2 flex items-center gap-2"
      >
        <Button
          variant="solid"
          theme="blue"
          class="min-h-11"
          :disabled="!message.trim() || tooLong || posting"
          data-testid="thread-post"
          @click="sendNote"
        >
          {{ isWaiting ? 'Reply' : 'Send' }}
        </Button>
        <span
          v-if="message.length > detailsMax * 0.8"
          class="text-xs text-ink-amber-3"
        >{{ message.length }}/{{ detailsMax }}</span>
      </div>

      <div
        v-if="postError"
        class="mt-2 text-sm text-ink-red-4"
        role="alert"
      >
        <p>{{ postError }}</p>
        <button
          v-if="canSendAsNote"
          type="button"
          class="mt-1 inline-flex min-h-11 cursor-pointer items-center font-medium text-blue-700 underline underline-offset-2"
          data-testid="send-as-note-instead"
          @click="sendNote"
        >
          Send as a note instead
        </button>
      </div>
    </div>

    <!-- Closed state -->
    <div
      v-if="closed"
      class="mt-2 text-sm text-ink-gray-6"
    >
      <p>This request is closed.</p>
      <button
        v-if="isEmployee"
        type="button"
        class="inline-flex min-h-11 cursor-pointer items-center text-blue-700 underline underline-offset-2"
        data-testid="thread-new-request"
        @click="emit('new-request')"
      >
        Start a new request
      </button>
    </div>
  </section>
</template>
