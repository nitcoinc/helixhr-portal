<script setup>
// The shared email theme (`HelixHR Email Theme`): one logo, brand colour and
// footer -- or admin-written theme code -- around every portal, celebration
// and holiday email. Every endpoint here gates itself (Portal Admin or System
// Manager) and the doctype's own validate() is the real check on each value.
import { computed, reactive, ref, watch } from 'vue'
import { createResource, Button } from 'frappe-ui'
import AsyncState from '@/components/AsyncState.vue'
import { uploadEmailThemeLogo } from '@/lib/api'

const PRESETS = [
  { name: 'White', hex: '#FFFFFF' },
  { name: 'Navy', hex: '#0B2545' },
  { name: 'Charcoal', hex: '#2B2D33' },
  { name: 'Deep teal', hex: '#0F4C5C' },
  { name: 'Burgundy', hex: '#6D1A36' },
  { name: 'Slate', hex: '#3E4C59' },
]
const HEX_RE = /^#[0-9A-Fa-f]{6}$/
// Checked here for a quick answer; the server checks the signature.
const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const LOGO_MAX_BYTES = 2 * 1024 * 1024

const theme = createResource({ url: 'helixhr.api.get_email_theme', auto: true })
const saveResource = createResource({ url: 'helixhr.api.save_email_theme', method: 'POST' })
const resetResource = createResource({ url: 'helixhr.api.reset_email_theme', method: 'POST' })
const previewResource = createResource({ url: 'helixhr.api.preview_email_theme', method: 'POST' })
const testResource = createResource({ url: 'helixhr.api.send_email_theme_test', method: 'POST' })
const removeLogoResource = createResource({ url: 'helixhr.api.upload_email_theme_logo', method: 'POST' })

const draft = reactive({ choice: '#FFFFFF', customHex: '', footer_text: '', use_custom_code: false, theme_code: '' })
const saved = ref(null)
const previewHtml = ref('')
const error = ref('')
const status = ref('')
const logoBusy = ref(false)
const logoInput = ref(null)

function load(data) {
  if (!data) return
  saved.value = data
  const color = (data.brand_color || '#FFFFFF').toUpperCase()
  const preset = PRESETS.find((p) => p.hex === color)
  draft.choice = preset ? preset.hex : 'custom'
  draft.customHex = preset ? '' : color
  draft.footer_text = data.footer_text || ''
  draft.use_custom_code = !!data.use_custom_code
  draft.theme_code = data.theme_code || ''
  previewHtml.value = data.preview_html || ''
}
watch(() => theme.data, load, { immediate: true })

const customValid = computed(() => HEX_RE.test(draft.customHex.trim()))
const brandColor = computed(() =>
  draft.choice === 'custom' ? (customValid.value ? draft.customHex.trim().toUpperCase() : '') : draft.choice,
)
const customHint = computed(() =>
  draft.choice === 'custom' && draft.customHex.trim() && !customValid.value
    ? 'Use a 6-digit hex code like #0B2545.'
    : '',
)

function payload() {
  return {
    // White is the default: saved as empty.
    brand_color: brandColor.value === '#FFFFFF' ? '' : brandColor.value,
    footer_text: draft.footer_text,
    use_custom_code: draft.use_custom_code ? 1 : 0,
    theme_code: draft.theme_code,
  }
}

const dirty = computed(() => {
  const data = saved.value
  if (!data) return false
  const p = payload()
  return (
    p.brand_color !== (data.brand_color === '#FFFFFF' ? '' : data.brand_color || '') ||
    p.footer_text !== (data.footer_text || '') ||
    p.use_custom_code !== (data.use_custom_code ? 1 : 0) ||
    p.theme_code !== (data.theme_code || '')
  )
})

function messageOf(err, fallback) {
  return String(err?.messages?.[0] || err?.message || fallback).replace(/<[^>]*>/g, '')
}

async function refreshPreview() {
  if (draft.choice === 'custom' && !customValid.value) return
  error.value = ''
  try {
    previewHtml.value = (await previewResource.submit(payload())).html
  } catch (err) {
    error.value = messageOf(err, 'Preview failed.')
  }
}
// Colour and the custom-code switch update the preview at once; text fields
// on "Update preview", so a half-typed template is not refused per keystroke.
watch(() => [brandColor.value, draft.use_custom_code], () => saved.value && refreshPreview())

async function save() {
  error.value = ''
  status.value = ''
  try {
    load(await saveResource.submit(payload()))
    status.value = 'Theme saved. Every email now uses it.'
  } catch (err) {
    error.value = messageOf(err, 'Could not save the theme. Please try again.')
  }
}

async function reset() {
  error.value = ''
  status.value = ''
  try {
    load(await resetResource.submit())
    status.value = 'Back to the default theme.'
  } catch (err) {
    error.value = messageOf(err, 'Could not reset the theme. Please try again.')
  }
}

async function sendTest() {
  error.value = ''
  status.value = ''
  try {
    const result = await testResource.submit(payload())
    status.value = `Test sent to ${result.sent_to}.`
  } catch (err) {
    status.value =
      err?.exc_type === 'RateLimitExceededError'
        ? 'Too many test emails. Wait a few minutes and try again.'
        : `Test not sent: ${messageOf(err, 'something went wrong.')}`
  }
}

async function afterLogoChange(data, message) {
  const pending = dirty.value ? { ...draft } : null
  load(data)
  status.value = message
  // Keep unsaved edits in the form and the preview.
  if (pending) {
    Object.assign(draft, pending)
    await refreshPreview()
  }
}

async function onLogoPicked(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  error.value = ''
  status.value = ''
  if (!file) return
  if (!LOGO_TYPES.includes(file.type)) {
    error.value = 'The logo must be a PNG, JPEG or WebP image.'
    return
  }
  if (file.size > LOGO_MAX_BYTES) {
    error.value = 'That file is bigger than 2 MB. Pick a smaller one.'
    return
  }
  logoBusy.value = true
  try {
    await afterLogoChange(await uploadEmailThemeLogo(file), 'Logo saved. Every email now carries it.')
  } catch (err) {
    error.value = messageOf(err, 'Could not save the logo. Please try again.')
  } finally {
    logoBusy.value = false
  }
}

async function removeLogo() {
  error.value = ''
  status.value = ''
  try {
    await afterLogoChange(await removeLogoResource.submit({ remove: 1 }), 'Logo removed.')
  } catch (err) {
    error.value = messageOf(err, 'Could not remove the logo. Please try again.')
  }
}

const showCode = ref(false)
watch(
  () => draft.use_custom_code,
  (on) => {
    if (on) showCode.value = true
  },
  { immediate: true },
)
const confirmReset = ref(false)
</script>

<template>
  <AsyncState
    section="email-theme"
    :resource="theme"
    skeleton="block"
    skeleton-height="h-96"
  >
    <div
      class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      data-testid="email-theme"
    >
      <section
        class="space-y-5"
        aria-labelledby="email-theme-title"
      >
        <div>
          <h2
            id="email-theme-title"
            class="type-section"
          >
            Email theme
          </h2>
          <p class="text-sm text-ink-gray-6">
            One look for every email the portal sends: message templates, celebrations and holidays.
          </p>
        </div>

        <div>
          <p class="mb-1 text-sm font-medium text-ink-gray-8">
            Logo
          </p>
          <div class="flex flex-wrap items-center gap-3">
            <div class="flex h-12 w-32 shrink-0 items-center justify-center rounded border border-outline-gray-1 bg-surface-white">
              <img
                v-if="saved?.logo"
                :src="saved.logo"
                alt="Email logo"
                class="max-h-10 max-w-full"
                data-testid="email-theme-logo"
              >
              <span
                v-else
                class="text-xs text-ink-gray-6"
              >No logo</span>
            </div>
            <input
              ref="logoInput"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              class="hidden"
              data-testid="email-theme-logo-input"
              @change="onLogoPicked"
            >
            <Button
              variant="subtle"
              :loading="logoBusy"
              @click="logoInput?.click()"
            >
              {{ saved?.logo ? 'Replace' : 'Upload' }}
            </Button>
            <Button
              v-if="saved?.logo"
              variant="ghost"
              :loading="removeLogoResource.loading"
              @click="removeLogo"
            >
              Remove
            </Button>
          </div>
          <p class="mt-1 text-xs text-ink-gray-6">
            PNG, JPEG or WebP, up to 2 MB. Sent inside each email, so it shows without loading anything.
          </p>
        </div>

        <fieldset>
          <legend
            id="email-theme-color-label"
            class="mb-1 text-sm font-medium text-ink-gray-8"
          >
            Brand colour (email header)
          </legend>
          <div class="flex flex-wrap gap-2">
            <label
              v-for="preset in PRESETS"
              :key="preset.hex"
              class="flex cursor-pointer items-center gap-2 rounded border border-outline-gray-2 px-2 py-1 text-sm text-ink-gray-8 has-[:checked]:border-outline-gray-5 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-outline-gray-3"
            >
              <input
                v-model="draft.choice"
                type="radio"
                name="email-theme-color"
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
                v-model="draft.choice"
                type="radio"
                name="email-theme-color"
                class="sr-only"
                value="custom"
              >
              Custom
            </label>
          </div>
          <div
            v-if="draft.choice === 'custom'"
            class="mt-2 flex items-center gap-2"
          >
            <span
              class="size-7 shrink-0 rounded border border-outline-gray-2"
              :style="{ background: customValid ? draft.customHex.trim() : 'transparent' }"
              aria-hidden="true"
            />
            <label
              for="email-theme-custom-hex"
              class="sr-only"
            >Custom hex colour</label>
            <input
              id="email-theme-custom-hex"
              v-model="draft.customHex"
              type="text"
              maxlength="7"
              placeholder="#0B2545"
              spellcheck="false"
              autocomplete="off"
              class="w-32 rounded-md border border-outline-gray-2 bg-surface-white px-2 py-1.5 font-mono text-sm text-ink-gray-9 focus:border-outline-gray-4"
              :aria-invalid="customHint ? 'true' : 'false'"
              aria-describedby="email-theme-custom-hint"
              data-testid="email-theme-custom-hex"
            >
            <span
              id="email-theme-custom-hint"
              class="text-xs"
              :class="customHint ? 'text-ink-red-4' : 'text-ink-gray-6'"
            >{{ customHint || '#RRGGBB' }}</span>
          </div>
          <p class="mt-1 text-xs text-ink-gray-6">
            Header text turns white or dark automatically, whichever reads better.
          </p>
        </fieldset>

        <div>
          <label
            for="email-theme-footer"
            class="mb-1 block text-sm font-medium text-ink-gray-8"
          >Footer text</label>
          <textarea
            id="email-theme-footer"
            v-model="draft.footer_text"
            rows="2"
            placeholder="Sent by {{ company }} through the HelixHR portal."
            class="w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8 focus:border-outline-gray-4"
          />
          <p class="mt-1 text-xs text-ink-gray-6">
            Empty keeps the default line. May use <code v-text="'{{ company }}'" /> and <code v-text="'{{ portal_url }}'" />.
          </p>
        </div>

        <div class="rounded-lg border border-outline-gray-1">
          <button
            type="button"
            class="flex min-h-11 w-full items-center justify-between px-3 text-left text-sm font-medium text-ink-gray-8"
            :aria-expanded="showCode ? 'true' : 'false'"
            aria-controls="email-theme-advanced"
            @click="showCode = !showCode"
          >
            Advanced: theme code
            <span aria-hidden="true">{{ showCode ? '−' : '+' }}</span>
          </button>
          <div
            v-show="showCode"
            id="email-theme-advanced"
            class="space-y-2 border-t border-outline-gray-1 p-3"
          >
            <label class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8">
              <input
                v-model="draft.use_custom_code"
                type="checkbox"
                class="size-4"
                data-testid="email-theme-use-code"
              >
              Use my own HTML instead of the theme above
            </label>
            <label
              for="email-theme-code"
              class="block text-sm font-medium text-ink-gray-8"
            >Theme code (HTML)</label>
            <textarea
              id="email-theme-code"
              v-model="draft.theme_code"
              rows="10"
              spellcheck="false"
              class="w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 font-mono text-xs text-ink-gray-8 focus:border-outline-gray-4"
              data-testid="email-theme-code"
            />
            <ul class="space-y-0.5 text-xs text-ink-gray-7">
              <li><code v-text="'{{ content }}'" /> the email's message (required)</li>
              <li><code v-text="'{{ logo }}'" /> the logo image, or the company name when a template leaves the logo out</li>
              <li><code v-text="'{{ company }}'" /> the company name</li>
              <li><code v-text="'{{ subject }}'" /> the email's subject</li>
              <li><code v-text="'{{ portal_url }}'" /> the portal's address</li>
              <li><code v-text="'{{ brand_color }}'" /> the brand colour, as #rrggbb</li>
            </ul>
            <p class="text-xs text-ink-gray-6">
              Inline styles only: mail clients drop &lt;style&gt; blocks. If the code fails when an email is sent,
              the default theme is used and the error is logged.
            </p>
          </div>
        </div>

        <p
          v-if="error"
          class="surface-alert p-3 text-sm"
          role="alert"
          data-testid="email-theme-error"
        >
          {{ error }}
        </p>
        <p
          class="text-sm text-ink-gray-7"
          role="status"
          data-testid="email-theme-status"
        >
          {{ status }}
        </p>

        <div class="flex flex-wrap items-center gap-2">
          <Button
            variant="solid"
            :loading="saveResource.loading"
            :disabled="draft.choice === 'custom' && !customValid"
            @click="save"
          >
            Save theme
          </Button>
          <Button
            variant="subtle"
            :loading="testResource.loading"
            @click="sendTest"
          >
            Send test email
          </Button>
          <Button
            variant="ghost"
            @click="confirmReset = true"
          >
            Reset to default
          </Button>
        </div>
        <div
          v-if="confirmReset"
          class="surface-inset flex flex-wrap items-center gap-2 p-3 text-sm"
          role="group"
          aria-label="Confirm reset"
        >
          <span class="text-ink-gray-7">Clear the colour, footer and theme code? The logo stays.</span>
          <Button
            variant="solid"
            theme="red"
            :loading="resetResource.loading"
            @click="reset().then(() => (confirmReset = false))"
          >
            Reset
          </Button>
          <Button
            variant="subtle"
            @click="confirmReset = false"
          >
            Cancel
          </Button>
        </div>
      </section>

      <section
        class="space-y-2"
        aria-label="Theme preview"
        data-testid="email-theme-preview"
      >
        <div class="flex items-center justify-between gap-2">
          <h2 class="label">
            Preview, with a sample message
          </h2>
          <Button
            variant="ghost"
            size="sm"
            :loading="previewResource.loading"
            @click="refreshPreview"
          >
            Update preview
          </Button>
        </div>
        <!-- KTD11: srcdoc + an empty sandbox, never v-html. -->
        <iframe
          v-if="previewHtml"
          title="Preview of the email theme"
          sandbox=""
          :srcdoc="previewHtml"
          class="h-[32rem] w-full rounded-lg border border-outline-gray-1 bg-white"
        />
      </section>
    </div>
  </AsyncState>
</template>
