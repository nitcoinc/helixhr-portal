<script setup>
// Plan 2026-10-05-001 U12: the company logo every email carries, shared by
// the celebration editor and the message-template editor (both render
// through helixhr_layout.html). `set_company_logo` is the real gate.
import { ref } from 'vue'
import { createResource, Button } from 'frappe-ui'
import { uploadCompanyLogo } from '@/lib/api'

const props = defineProps({
  company: { type: String, required: true },
  logoUrl: { type: String, default: '' },
  /** false: show the logo only (the caller may not change it). */
  editable: { type: Boolean, default: true },
})
const emit = defineEmits(['changed'])

// `Company.company_logo`, set for the selected company only. Type and size
// are checked here for a quick answer and again on the server, which is
// the real gate (signature, not name).
const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const LOGO_MAX_BYTES = 2 * 1024 * 1024
const logoInput = ref(null)
const logoError = ref('')
const logoStatus = ref('')
const logoBusy = ref(false)
const removeLogoResource = createResource({ url: 'helixhr.api.set_company_logo', method: 'POST' })

async function onLogoPicked(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  logoError.value = ''
  logoStatus.value = ''
  if (!file) return
  if (!LOGO_TYPES.includes(file.type)) {
    logoError.value = 'The logo must be a PNG, JPEG or WebP image.'
    return
  }
  if (file.size > LOGO_MAX_BYTES) {
    logoError.value = 'That file is bigger than 2 MB. Pick a smaller one.'
    return
  }
  logoBusy.value = true
  try {
    await uploadCompanyLogo(file, { company: props.company })
    emit('changed')
    logoStatus.value = 'Logo saved. Every email for ' + props.company + ' now carries it.'
  } catch (error) {
    logoError.value = error?.messages?.[0] || 'Could not save the logo. Please try again.'
  } finally {
    logoBusy.value = false
  }
}

async function removeLogo() {
  logoError.value = ''
  logoStatus.value = ''
  try {
    await removeLogoResource.submit({ company: props.company, remove: 1 })
    emit('changed')
    logoStatus.value = 'Logo removed.'
  } catch (error) {
    logoError.value = error?.messages?.[0] || 'Could not remove the logo. Please try again.'
  }
}
</script>

<template>
  <div
    class="surface-card elev-1 flex flex-wrap items-center gap-3 p-4"
    data-testid="company-logo"
  >
    <div class="flex h-12 w-32 shrink-0 items-center justify-center rounded border border-outline-gray-1 bg-surface-white">
      <img
        v-if="props.logoUrl"
        :src="props.logoUrl"
        :alt="`${props.company} logo`"
        class="max-h-10 max-w-full"
      >
      <span
        v-else
        class="text-xs text-ink-gray-6"
      >No logo</span>
    </div>
    <div class="min-w-0 flex-1">
      <p class="font-medium text-ink-gray-9">
        Email logo
      </p>
      <p class="text-sm text-ink-gray-6">
        One logo for {{ props.company }}: shown at the top of every portal message,
        celebration and holiday email. Untick "Include company logo" on a template to
        leave it out there.
        {{ props.editable ? 'PNG, JPEG or WebP, up to 2 MB.' : 'An HR Manager changes it under Celebrations & holidays.' }}
      </p>
    </div>
    <template v-if="props.editable">
      <input
        ref="logoInput"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        class="hidden"
        data-testid="company-logo-input"
        @change="onLogoPicked"
      >
      <Button
        variant="subtle"
        :loading="logoBusy"
        @click="logoInput?.click()"
      >
        {{ props.logoUrl ? 'Replace' : 'Upload' }}
      </Button>
      <Button
        v-if="props.logoUrl"
        variant="ghost"
        :loading="removeLogoResource.loading"
        @click="removeLogo"
      >
        Remove
      </Button>
    </template>
    <p
      v-if="logoError"
      class="surface-alert w-full p-3 text-sm"
      role="alert"
    >
      {{ logoError }}
    </p>
    <p
      v-if="logoStatus"
      class="w-full text-sm text-ink-gray-6"
      role="status"
      data-testid="company-logo-status"
    >
      {{ logoStatus }}
    </p>
  </div>
</template>
