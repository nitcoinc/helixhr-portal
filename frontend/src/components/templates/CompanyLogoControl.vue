<script setup>
// Plan 2026-10-05-001 U12: the company logo every email carries, shared by
// the celebration editor and the message-template editor (both render
// through helixhr_layout.html). `set_company_logo` is the real gate.
import { computed, ref, watch } from 'vue'
import { createResource, Button } from 'frappe-ui'
import { uploadCompanyLogo } from '@/lib/api'

const props = defineProps({
  company: { type: String, required: true },
  logoUrl: { type: String, default: '' },
  /** false: show the logo only (the caller may not change it). */
  editable: { type: Boolean, default: true },
  /** Saved `#rrggbb` header background; empty = white. */
  headerColor: { type: String, default: '' },
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

// Email header background (`Company.helixhr_email_header_color`).
// `set_email_header_color` validates again; the layout validates at render.
const HEADER_PRESETS = [
  { name: 'White', hex: '#FFFFFF' },
  { name: 'Navy', hex: '#0B2545' },
  { name: 'Charcoal', hex: '#2B2D33' },
  { name: 'Deep teal', hex: '#0F4C5C' },
  { name: 'Burgundy', hex: '#6D1A36' },
  { name: 'Slate', hex: '#3E4C59' },
]
const HEX_RE = /^#[0-9A-Fa-f]{6}$/
const savedColor = computed(() => (props.headerColor || '#FFFFFF').toUpperCase())
const choice = ref('')
const customHex = ref('')
const colorError = ref('')
const colorStatus = ref('')
const colorResource = createResource({ url: 'helixhr.api.set_email_header_color', method: 'POST' })

function syncColor() {
  const preset = HEADER_PRESETS.find((p) => p.hex === savedColor.value)
  choice.value = preset ? preset.hex : 'custom'
  customHex.value = preset ? '' : savedColor.value
}
watch(savedColor, syncColor, { immediate: true })

const customValid = computed(() => HEX_RE.test(customHex.value.trim()))
const pendingColor = computed(() =>
  choice.value === 'custom' ? (customValid.value ? customHex.value.trim().toUpperCase() : '') : choice.value,
)
const previewBg = computed(() => pendingColor.value || savedColor.value)

// WCAG relative luminance; same rule as helixhr.utils.email_header_colors.
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const previewDark = computed(() => {
  const lum = luminance(previewBg.value)
  return 1.05 / (lum + 0.05) > (lum + 0.05) / (luminance('#1F2328') + 0.05)
})
const previewFg = computed(() => (previewDark.value ? '#FFFFFF' : '#1F2328'))
const customHint = computed(() =>
  choice.value === 'custom' && customHex.value.trim() && !customValid.value
    ? 'Use a 6-digit hex code like #0B2545.'
    : '',
)

async function saveColor(color) {
  colorError.value = ''
  colorStatus.value = ''
  try {
    await colorResource.submit({ company: props.company, color })
    emit('changed')
    colorStatus.value = color ? 'Header colour saved.' : 'Header reset to white.'
  } catch (error) {
    colorError.value = error?.messages?.[0] || 'Could not save the header colour. Please try again.'
  }
}

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

    <section
      class="w-full border-t border-outline-gray-1 pt-3"
      aria-labelledby="email-header-color-label"
      data-testid="email-header-color"
    >
      <p
        id="email-header-color-label"
        class="font-medium text-ink-gray-9"
      >
        Email header colour
      </p>
      <div
        class="mt-2 flex h-12 max-w-sm items-center rounded border border-outline-gray-1 px-4"
        :style="{ background: previewBg, color: previewFg }"
        data-testid="email-header-preview"
        :data-bg="previewBg"
        :data-fg="previewFg"
        aria-hidden="true"
      >
        <img
          v-if="props.logoUrl"
          :src="props.logoUrl"
          alt=""
          class="max-h-8 max-w-full"
        >
        <strong
          v-else
          class="text-base"
        >{{ props.company }}</strong>
      </div>
      <p
        v-if="props.logoUrl && previewDark"
        class="mt-1 text-xs text-ink-gray-6"
      >
        On a dark header, a logo with a transparent background needs a light (white) version to stay readable.
      </p>
      <template v-if="props.editable">
        <div
          class="mt-3 flex flex-wrap gap-2"
          role="radiogroup"
          aria-labelledby="email-header-color-label"
        >
          <label
            v-for="preset in HEADER_PRESETS"
            :key="preset.hex"
            class="flex cursor-pointer items-center gap-2 rounded border border-outline-gray-2 px-2 py-1 text-sm text-ink-gray-8 has-[:checked]:border-outline-gray-5 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-outline-gray-3"
          >
            <input
              v-model="choice"
              type="radio"
              name="email-header-color"
              class="sr-only"
              :value="preset.hex"
            >
            <span
              class="size-4 rounded-full border border-outline-gray-2"
              :style="{ background: preset.hex }"
              aria-hidden="true"
            />
            {{ preset.name }}
          </label>
          <label class="flex cursor-pointer items-center gap-2 rounded border border-outline-gray-2 px-2 py-1 text-sm text-ink-gray-8 has-[:checked]:border-outline-gray-5 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-outline-gray-3">
            <input
              v-model="choice"
              type="radio"
              name="email-header-color"
              class="sr-only"
              value="custom"
            >
            Custom
          </label>
        </div>
        <div
          v-if="choice === 'custom'"
          class="mt-2 flex items-center gap-2"
        >
          <span
            class="size-7 shrink-0 rounded border border-outline-gray-2"
            :style="{ background: customValid ? customHex.trim() : 'transparent' }"
            aria-hidden="true"
          />
          <label
            for="email-header-custom-hex"
            class="sr-only"
          >Custom hex colour</label>
          <input
            id="email-header-custom-hex"
            v-model="customHex"
            type="text"
            maxlength="7"
            placeholder="#0B2545"
            spellcheck="false"
            autocomplete="off"
            class="w-32 rounded-md border border-outline-gray-2 bg-surface-white px-2 py-1.5 font-mono text-sm text-ink-gray-9 focus:border-outline-gray-4"
            :aria-invalid="customHint ? 'true' : 'false'"
            aria-describedby="email-header-custom-hint"
            data-testid="email-header-custom"
          >
          <span
            id="email-header-custom-hint"
            class="text-xs"
            :class="customHint ? 'text-ink-red-4' : 'text-ink-gray-6'"
          >{{ customHint || '#RRGGBB' }}</span>
        </div>
        <div class="mt-3 flex gap-2">
          <Button
            variant="subtle"
            :disabled="!pendingColor || pendingColor === savedColor"
            :loading="colorResource.loading"
            @click="saveColor(pendingColor)"
          >
            Save colour
          </Button>
          <Button
            v-if="savedColor !== '#FFFFFF'"
            variant="ghost"
            @click="saveColor('')"
          >
            Reset to white
          </Button>
        </div>
      </template>
      <p
        v-if="colorError"
        class="surface-alert mt-2 p-3 text-sm"
        role="alert"
      >
        {{ colorError }}
      </p>
      <p
        v-if="colorStatus"
        class="mt-2 text-sm text-ink-gray-6"
        role="status"
        data-testid="email-header-status"
      >
        {{ colorStatus }}
      </p>
    </section>
  </div>
</template>
