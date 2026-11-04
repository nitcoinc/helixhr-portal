<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { Button } from 'frappe-ui'
import { submitProfileChanges } from '@/lib/api'
import { formatProfileValue, spokenValue } from '@/lib/profileCorrection'
import {
  entryErrors,
  initialForm,
  missingCore,
  newOperationKey,
  placeErrors,
  routes,
  seenStates,
  stagedChanges,
} from '@/lib/profileChanges'

// Plan 2026-11-03-001 U9 (R11-R14, R17; review B7-B9). The Bank & IDs tab in
// edit mode: every field the site has, one Send for all of it.
//
// Per group the page previews what the server decides: nothing on file ->
// "fills in now" (password step-up), anything on file -> "goes to HR" (proof).
// A masked identifier starts empty with its last four as a hint and is typed
// twice. A refusal comes back as data and is shown where it belongs; what the
// employee typed stays. Fields already waiting in an open request are locked.
const props = defineProps({
  fields: { type: Array, required: true },
  pending: { type: Array, default: () => [] },
  // False on SSO-only sites: no password to re-check, the fill goes through.
  fillNeedsPassword: { type: Boolean, default: true },
})
const emit = defineEmits(['done', 'cancel'])

const GROUP_LABELS = {
  bank: 'Bank',
  pan: 'PAN',
  pf: 'PF account',
  passport: 'Passport',
  insurance: 'Health insurance',
}
const PROOF_ACCEPT = '.pdf,.png,.jpg,.jpeg,.docx,.xlsx'

const editable = computed(() => props.fields.filter((field) => field.change))
const locked = computed(() => new Set(props.pending))
const runs = computed(() => {
  const out = []
  for (const field of editable.value) {
    let run = out.find((r) => r.group === field.change.group)
    if (!run) out.push((run = { group: field.change.group, label: GROUP_LABELS[field.change.group], fields: [] }))
    run.fields.push(field)
  }
  return out
})

const form = reactive(initialForm(props.fields))
const password = ref('')
const proof = ref(null)
const proofInput = ref(null)
const operationKey = ref(newOperationKey())
const sending = ref(false)
const errors = ref(placeErrors([]))
const formTop = ref(null)
const root = ref(null)

const staged = computed(() =>
  stagedChanges(
    editable.value.filter((field) => !locked.value.has(field.fieldname)),
    form,
  ),
)
const route = computed(() => routes(staged.value, props.fields))
const mismatches = computed(() => entryErrors(staged.value))
const missing = computed(() => missingCore(staged.value, props.fields))
const needsPassword = computed(
  () => props.fillNeedsPassword && Object.values(route.value).includes('fill'),
)
const needsProof = computed(() => Object.values(route.value).includes('hr'))
const labelOf = (name) => props.fields.find((field) => field.fieldname === name)?.label || name
const canSend = computed(
  () =>
    staged.value.length > 0 &&
    !Object.keys(mismatches.value).length &&
    !Object.keys(missing.value).length &&
    (!needsPassword.value || !!password.value) &&
    (!needsProof.value || !!proof.value) &&
    !sending.value,
)

function fieldErrors(name) {
  const messages = [...(errors.value.fields[name] || [])]
  const entry = form[name]
  // Said once the second box has something, never while typing the first.
  if (mismatches.value[name] && entry?.confirm) messages.push(mismatches.value[name])
  return messages
}

function describedBy(field, ...extra) {
  const ids = [...extra]
  if (fieldErrors(field.fieldname).length) ids.push(`bank-edit-${field.fieldname}-error`)
  return ids.join(' ') || undefined
}

function hint(field) {
  if (!field.masked || !field.value) return ''
  return `On file: ${field.value}`
}

async function send() {
  if (!canSend.value) return
  sending.value = true
  errors.value = placeErrors([])
  try {
    const result = await submitProfileChanges(needsProof.value ? proof.value : null, {
      changes: staged.value,
      seen: seenStates(staged.value, props.fields),
      operationKey: operationKey.value,
      password: needsPassword.value ? password.value : undefined,
    })
    if (result?.status === 'refused') {
      // Nothing was written: the next press is a new attempt.
      operationKey.value = newOperationKey()
      errors.value = placeErrors(result.errors, [...editable.value.map((f) => f.fieldname), 'password', 'file'])
      if (errors.value.fields.password) password.value = ''
      await nextTick()
      root.value?.querySelector('[aria-invalid="true"], [data-error]')?.focus()
      return
    }
    password.value = ''
    emit('done', result)
  } catch (error) {
    const status = error?.response?.status
    // A real refusal rotates the key; an unknown outcome keeps it, so a
    // retry returns whatever the first attempt made.
    if (status && status < 500 && status !== 408 && status !== 499) operationKey.value = newOperationKey()
    errors.value = placeErrors([
      {
        message:
          error?.messages?.[0] ||
          'We couldn’t send that. It may already have gone through — check your profile and Requests before sending again.',
      },
    ])
    await nextTick()
    formTop.value?.focus()
  } finally {
    sending.value = false
  }
}

function onProof(event) {
  proof.value = event.target.files?.[0] || null
}

onMounted(() => {
  root.value?.querySelector('input:not([disabled]), select:not([disabled])')?.focus()
})
</script>

<template>
  <form
    ref="root"
    class="space-y-5"
    aria-label="Edit bank and ID details"
    data-testid="bank-edit"
    novalidate
    @submit.prevent="send"
  >
    <div
      v-if="errors.form.length"
      ref="formTop"
      class="surface-alert space-y-1 p-3 text-sm"
      role="alert"
      tabindex="-1"
      data-error
      data-testid="bank-edit-form-error"
    >
      <p
        v-for="message in errors.form"
        :key="message"
      >
        {{ message }}
      </p>
    </div>

    <section
      v-for="run in runs"
      :key="run.group"
      :aria-labelledby="`bank-edit-group-${run.group}`"
      :data-testid="`bank-edit-group-${run.group}`"
    >
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3
          :id="`bank-edit-group-${run.group}`"
          class="label"
        >
          {{ run.label }}
        </h3>
        <span
          v-if="route[run.group]"
          class="rounded-full px-2 py-0.5 text-xs font-medium"
          :class="route[run.group] === 'fill' ? 'bg-surface-green-2 text-ink-green-3' : 'bg-surface-amber-1 text-ink-amber-3'"
          :data-testid="`bank-edit-route-${run.group}`"
        >
          {{ route[run.group] === 'fill' ? 'Fills in now' : 'Goes to HR' }}
        </span>
      </div>
      <p
        v-if="route[run.group] === 'hr'"
        class="mb-2 text-sm text-ink-gray-6"
      >
        You already have {{ run.label.toLowerCase() }} details on file, so HR checks these changes against your proof.
      </p>
      <div
        v-for="message in [
          ...(errors.groups[run.group] || []),
          ...(missing[run.group]
            ? [`To fill these in for the first time, also give ${missing[run.group].map(labelOf).join(', ')}.`]
            : []),
        ]"
        :key="message"
        class="mb-2 text-sm text-ink-red-4"
        :role="errors.groups[run.group]?.includes(message) ? 'alert' : undefined"
        :tabindex="errors.groups[run.group]?.includes(message) ? -1 : undefined"
        :data-error="errors.groups[run.group]?.includes(message) ? '' : undefined"
        :data-testid="`bank-edit-group-error-${run.group}`"
      >
        {{ message }}
      </div>

      <div class="surface-card elev-1 divide-y divide-outline-gray-1">
        <div
          v-for="field in run.fields"
          :key="field.fieldname"
          class="space-y-2 px-4 py-3"
          :data-testid="`bank-edit-${field.fieldname}`"
        >
          <template v-if="locked.has(field.fieldname)">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="text-sm text-ink-gray-6">{{ field.label }}</span>
              <span
                class="text-ink-gray-9"
                :aria-label="field.masked ? spokenValue(formatProfileValue(field.value)) : undefined"
              >{{ formatProfileValue(field.value) }}</span>
            </div>
            <p
              class="text-sm text-ink-gray-6"
              :data-testid="`bank-edit-locked-${field.fieldname}`"
            >
              <span class="font-medium text-ink-amber-3">Waiting for HR.</span>
              It’s in a request HR hasn’t closed yet; you can change it once that closes.
            </p>
          </template>
          <template v-else>
            <label
              class="label block"
              :for="`bank-edit-${field.fieldname}-input`"
            >{{ field.label }}</label>
            <select
              v-if="field.change.options"
              :id="`bank-edit-${field.fieldname}-input`"
              v-model="form[field.fieldname].value"
              class="min-h-11 w-full cursor-pointer rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-ink-gray-9 focus:border-outline-gray-4"
              :aria-invalid="!!fieldErrors(field.fieldname).length"
              :aria-describedby="describedBy(field)"
            >
              <option value="">
                Choose…
              </option>
              <option
                v-for="option in field.change.options"
                :key="option"
                :value="option"
              >
                {{ option }}
              </option>
            </select>
            <input
              v-else
              :id="`bank-edit-${field.fieldname}-input`"
              v-model="form[field.fieldname].value"
              :type="field.change.type === 'Date' ? 'date' : 'text'"
              class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-ink-gray-9 focus:border-outline-gray-4"
              autocomplete="off"
              spellcheck="false"
              maxlength="140"
              :placeholder="field.masked ? (field.value ? 'Type a new value to change it' : 'Type it in') : undefined"
              :aria-invalid="!!fieldErrors(field.fieldname).length"
              :aria-describedby="describedBy(field, hint(field) ? `bank-edit-${field.fieldname}-hint` : '')"
            >
            <p
              v-if="hint(field)"
              :id="`bank-edit-${field.fieldname}-hint`"
              class="text-sm text-ink-gray-5"
            >
              On file: <span :aria-label="spokenValue(field.value)">{{ field.value }}</span>
            </p>
            <div v-if="field.masked && form[field.fieldname].value">
              <label
                class="label block"
                :for="`bank-edit-${field.fieldname}-confirm`"
              >Type the new {{ field.label }} again</label>
              <input
                :id="`bank-edit-${field.fieldname}-confirm`"
                v-model="form[field.fieldname].confirm"
                type="text"
                class="mt-1 min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-ink-gray-9 focus:border-outline-gray-4"
                autocomplete="off"
                spellcheck="false"
                maxlength="140"
                :aria-invalid="!!fieldErrors(field.fieldname).length"
                :aria-describedby="describedBy(field)"
                :data-testid="`bank-edit-${field.fieldname}-confirm`"
              >
            </div>
            <div
              v-if="fieldErrors(field.fieldname).length"
              :id="`bank-edit-${field.fieldname}-error`"
              class="text-sm text-ink-red-4"
              :data-testid="`bank-edit-${field.fieldname}-error`"
            >
              <p
                v-for="message in fieldErrors(field.fieldname)"
                :key="message"
              >
                {{ message }}
              </p>
            </div>
          </template>
        </div>
      </div>
    </section>

    <section
      class="surface-card elev-1 space-y-4 p-4"
      aria-label="Send your changes"
    >
      <p
        class="text-sm text-ink-gray-7"
        aria-live="polite"
        data-testid="bank-edit-summary"
      >
        <template v-if="!staged.length">
          Nothing changed yet.
        </template>
        <template v-else>
          <span class="tabular">{{ staged.length }}</span>
          change{{ staged.length === 1 ? '' : 's' }}.
          <template v-if="needsPassword">
            Blank details fill in as soon as you send.
          </template>
          <template v-if="needsProof">
            Changes to details on file go to HR with your proof.
          </template>
        </template>
      </p>

      <div v-if="needsPassword">
        <label
          class="label mb-1 block"
          for="bank-edit-password"
        >Your password</label>
        <input
          id="bank-edit-password"
          v-model="password"
          type="password"
          class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-ink-gray-9 focus:border-outline-gray-4"
          autocomplete="current-password"
          :aria-invalid="!!errors.fields.password"
          aria-describedby="bank-edit-password-help"
        >
        <p
          id="bank-edit-password-help"
          class="mt-1 text-sm text-ink-gray-5"
        >
          Filling in details for the first time needs your password. HR is told and checks them later.
        </p>
        <p
          v-for="message in errors.fields.password || []"
          :key="message"
          class="mt-1 text-sm text-ink-red-4"
          data-testid="bank-edit-password-error"
        >
          {{ message }}
        </p>
      </div>

      <div v-if="needsProof">
        <label
          class="label mb-1 block"
          for="bank-edit-proof"
        >Proof (required)</label>
        <input
          id="bank-edit-proof"
          ref="proofInput"
          type="file"
          class="block w-full text-sm text-ink-gray-7"
          :accept="PROOF_ACCEPT"
          :aria-invalid="!!errors.fields.file"
          aria-describedby="bank-edit-proof-help"
          data-testid="bank-edit-proof"
          @change="onProof"
        >
        <p
          id="bank-edit-proof-help"
          class="mt-1 text-sm text-ink-gray-5"
        >
          A bank letter, cancelled cheque or ID scan · PDF, PNG, JPEG, Word or Excel · up to
          <span class="tabular">10</span> MB
        </p>
        <p
          v-for="message in errors.fields.file || []"
          :key="message"
          class="mt-1 text-sm text-ink-red-4"
          data-testid="bank-edit-proof-error"
        >
          {{ message }}
        </p>
      </div>

      <p class="text-sm text-ink-gray-6">
        We email your work and personal addresses about every change.
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <Button
          variant="solid"
          theme="blue"
          type="submit"
          :loading="sending"
          :disabled="!canSend"
          data-testid="bank-edit-send"
        >
          {{ needsProof ? 'Send' : 'Save' }}
        </Button>
        <Button
          variant="subtle"
          type="button"
          :disabled="sending"
          @click="emit('cancel')"
        >
          Cancel
        </Button>
      </div>
    </section>
  </form>
</template>
