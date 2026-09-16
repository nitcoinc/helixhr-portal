<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import { attachToRequest } from '@/lib/api'

// P2-U8. The new-request sheet.
//
// Two things about it are contracts rather than styling:
//
//   * **One key per attempt.** `crypto.randomUUID()` runs once when an
//     attempt begins, and the same key rides every retry of that attempt. A
//     create whose response was lost therefore returns the request it already
//     made instead of making a second one (P2-AE7). A deliberate new
//     submission -- after a refusal the server actually answered, or after a
//     key collision -- gets a new key, because it is a new request.
//   * **Two steps, told apart.** Creating the request and attaching the file
//     are separate observable outcomes. When the request commits and the
//     upload fails, this reports the request as *sent* and hands the file back
//     to the page so the detail view can offer Retry upload. Saying "couldn't
//     send" about a record that exists is the defect this replaces (P2-R18).
const props = defineProps({
  initialCategory: { type: String, default: '' },
  initialSubject: { type: String, default: '' },
})
const emit = defineEmits(['created', 'cancel'])

// P5-R1: categories are app records. HR can add or retire one without a
// frontend deploy; the server validates the selected name again on submit.
const categories = createResource({
  url: 'helixhr.api.get_request_categories',
  auto: true,
})
const categoryOptions = computed(() => categories.data || [])

// The rule, stated up front on the sheet and enforced by
// `helixhr.api.attach_to_my_request`. Both numbers come from one place here
// so the sentence and the check cannot drift apart in the copy.
const MAX_MB = 10

// P2-U9 step 5. The exact five types `helixhr.utils.validate_portal_upload`
// accepts, by extension *and* by content. This attribute only filters the
// file picker -- it is a courtesy, never the boundary -- but a picker that
// offers something the server will refuse is a promise the app then breaks.
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.docx,.xlsx'

const category = ref(props.initialCategory)
const subject = ref(props.initialSubject || '')

watch(
  categoryOptions,
  (options) => {
    if (!options.some((option) => option.name === category.value)) {
      category.value = options[0]?.name || ''
    }
  },
  { immediate: true },
)
const details = ref('')
const file = ref(null)
const error = ref('')

/** A v4 UUID. `crypto.randomUUID` needs a secure context, which the portal
 * always has in production and on localhost; the fallback keeps a plain-HTTP
 * LAN deployment from losing the idempotency guarantee entirely. */
function newOperationKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

const operationKey = ref(newOperationKey())
// Set by an ambiguous failure. From that moment the request may already
// exist, and the retry's payload is ignored by design -- the server answers
// the key, not the body. So the fields stop being editable: an employee who
// fixes a typo and presses Send again would otherwise watch the correction
// vanish with no way to apply it, Send being the only write path there is.
const frozen = ref(false)
// Set when the server gave a real verdict on the last attempt: the request
// was refused and nothing was written, so the next press of Send is a fresh
// attempt and takes a fresh key. An *ambiguous* failure sets nothing, which
// is what makes its retry idempotent.
const rotateBeforeNextSend = ref(false)

const create = createResource({ url: 'helixhr.api.create_my_request', method: 'POST' })
const uploading = ref(false)

// One busy flag over both steps. Send is disabled while either is in flight,
// so a second press cannot start a second create against the same key or a
// second upload against the same request.
const busy = computed(() => create.loading || uploading.value)
const canSend = computed(() => !!category.value && !!subject.value.trim() && !busy.value)

function onFileChange(e) {
  file.value = e.target.files?.[0] || null
}

function plainError(e, fallback) {
  return e?.messages?.[0] || fallback
}

// A reverse proxy that gives up on a slow POST answers 408 (Request Timeout)
// or, on nginx, 499 (client closed) -- while the request may well have
// committed behind it. Both sit under 500 and used to read as a verdict, so
// the retry carried a *fresh* key and created exactly the duplicate the
// operation key exists to prevent (P2-AE7).
const AMBIGUOUS_STATUS = [408, 499]

/** Did the server actually answer? A network drop, a proxy timeout or a 5xx
 * leaves the outcome unknown, and an unknown outcome must reuse the key. */
function isAmbiguous(e) {
  const status = e?.response?.status
  return !status || status >= 500 || AMBIGUOUS_STATUS.includes(status)
}

async function submit() {
  if (!canSend.value) return
  error.value = ''

  if (rotateBeforeNextSend.value) {
    operationKey.value = newOperationKey()
    rotateBeforeNextSend.value = false
  }

  let created
  try {
    created = await create.submit({
      category: category.value,
      subject: subject.value.trim(),
      details: details.value.trim(),
      operation_key: operationKey.value,
    })
  } catch (e) {
    if (e?.exc_type === 'DuplicateEntryError') {
      // The key already belongs to somebody else's request. The server tells
      // us nothing about it, and the only thing to change is the key.
      operationKey.value = newOperationKey()
      // A fresh key is a fresh attempt, so the fields are the employee's
      // again -- nothing the old key wrote can be reused now.
      frozen.value = false
      error.value = 'That didn’t go through. Press Send to HR again.'
      return
    }
    if (isAmbiguous(e)) {
      // Nothing here clears the form, and from now on nothing may change it
      // either: the retry sends the key, and the key already has a payload.
      frozen.value = true
      error.value =
        'We didn’t hear back, so your request may already have gone through. ' +
        'Press Send to HR again — it won’t send a second one.'
      return
    }
    rotateBeforeNextSend.value = true
    // Every field stays exactly as typed -- nothing here clears the form.
    error.value = plainError(e, 'Could not send your request. Please try again.')
    return
  }

  // From here the request exists. Whatever the upload does, this attempt
  // ends with the employee on that request.
  let uploadError = ''
  if (file.value) {
    uploading.value = true
    try {
      await attachToRequest(file.value, { name: created.name })
    } catch (e) {
      uploadError = plainError(e, 'The file didn’t upload.')
    } finally {
      uploading.value = false
    }
  }

  emit('created', {
    name: created.name,
    // False when the server answered this operation key from an earlier
    // attempt: the request on screen is that one, and anything typed since is
    // not on it. The page says so rather than reporting a plain success.
    created: created.created !== false,
    uploadError,
    // Handed back so Retry upload has the same bytes to send again. It lives
    // in memory only: after a reload the chip is gone, and the honest way to
    // add the file then is to attach it to the request again.
    pendingFile: uploadError ? file.value : null,
  })
}
</script>

<template>
  <form
    class="space-y-5"
    @submit.prevent="submit"
  >
    <AsyncState
      :resource="categories"
      section="request-categories"
      :empty="categoryOptions.length === 0"
      empty-title="No request categories are available"
      empty-body="Ask HR to add one before sending a request."
      skeleton="field"
      skeleton-height="h-28"
    >
      <fieldset>
        <legend class="label mb-2">
          What is it about?
        </legend>
        <div class="grid grid-cols-2 gap-2">
          <button
            v-for="option in categoryOptions"
            :key="option.name"
            type="button"
            :disabled="frozen"
            class="min-h-11 cursor-pointer rounded-lg border p-3 text-left transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-60"
            :class="
              category === option.name
                ? 'border-field bg-surface-white ring-1 ring-field'
                : 'border-outline-gray-2 bg-surface-white hover:bg-surface-gray-2'
            "
            :aria-pressed="category === option.name"
            @click="category = option.name"
          >
            <span class="block font-medium text-ink-gray-9">{{ option.category_name }}</span>
            <span class="mt-0.5 block text-sm text-ink-gray-6">{{ option.hint }}</span>
          </button>
        </div>
      </fieldset>
    </AsyncState>

    <FormControl
      v-model="subject"
      type="text"
      label="Subject"
      required
      :disabled="frozen"
    />
    <FormControl
      v-model="details"
      type="textarea"
      label="Details"
      placeholder="What do you need, and by when?"
      :disabled="frozen"
    />

    <div>
      <label
        class="label mb-1 block"
        for="request-attachment"
      >Attachment (optional)</label>
      <input
        id="request-attachment"
        type="file"
        class="block w-full text-sm text-ink-gray-7"
        :accept="ACCEPT"
        :disabled="frozen"
        @change="onFileChange"
      >
      <!-- The rule up front, because it is the server's rule: the same limits
           are enforced by helixhr.api.attach_to_my_request. -->
      <p class="mt-1 text-sm text-ink-gray-5">
        PDF, PNG or JPEG image, or a Word or Excel document ·
        up to <span class="tabular">{{ MAX_MB }}</span> MB
      </p>
    </div>

    <p
      v-if="error"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      {{ error }}
    </p>

    <div
      class="sticky bottom-0 -mx-1 flex items-center gap-2 border-t border-outline-gray-1 bg-surface-white px-1 py-3"
    >
      <Button
        variant="solid"
        theme="blue"
        type="submit"
        :loading="busy"
        :disabled="!canSend"
      >
        Send to HR
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
