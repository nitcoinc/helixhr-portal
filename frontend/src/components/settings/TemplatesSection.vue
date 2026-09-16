<script setup>
import { computed, reactive, ref } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

const props = defineProps({
  templates: { type: Array, required: true },
  templateTokens: { type: Object, required: true },
})
const emit = defineEmits(['saved'])

const SUBJECT_MAX = 140

// P5-KTD11: one row per message key the portal knows how to send, whether or
// not HR has edited it yet -- a template that has never been saved reads as
// "using the default wording", not as absent.
const rows = computed(() =>
  Object.keys(props.templateTokens).map((templateKey) => {
    const saved = props.templates.find((t) => t.template_key === templateKey)
    return {
      template_key: templateKey,
      tokens: props.templateTokens[templateKey],
      subject: saved?.subject || '',
      body: saved?.body || '',
      is_enabled: saved ? !!saved.is_enabled : false,
    }
  }),
)

const form = reactive({ template_key: '', subject: '', body: '', is_enabled: false })
const editing = ref('')
const formError = ref('')

function edit(row) {
  editing.value = row.template_key
  Object.assign(form, row)
  formError.value = ''
}

function cancel() {
  editing.value = ''
  formError.value = ''
}

const save = createResource({ url: 'helixhr.api.save_message_template', method: 'POST' })

async function submit() {
  formError.value = ''
  try {
    await save.submit({
      template_key: form.template_key,
      subject: form.subject,
      body: form.body,
      is_enabled: form.is_enabled ? 1 : 0,
    })
    editing.value = ''
    emit('saved')
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div class="space-y-4">
    <h2 class="label">
      Message text
    </h2>
    <p class="text-sm text-ink-gray-6">
      Plain text only -- a token like <code>{category}</code> is filled in when the message is
      sent. Nothing typed here is ever run as code.
    </p>

    <div class="space-y-3">
      <div
        v-for="row in rows"
        :key="row.template_key"
        class="surface-card elev-1 p-4"
        data-testid="settings-template-row"
      >
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <p class="truncate font-medium text-ink-gray-9">
              {{ row.template_key }}
            </p>
            <p class="text-sm text-ink-gray-6">
              {{ row.is_enabled ? 'Using this wording' : 'Using the default wording' }}
            </p>
          </div>
          <Button
            variant="ghost"
            @click="edit(row)"
          >
            Edit
          </Button>
        </div>

        <div
          v-if="editing === row.template_key"
          class="mt-4 space-y-4 border-t border-outline-gray-1 pt-4"
          data-testid="settings-template-form"
        >
          <p class="text-xs text-ink-gray-6">
            Tokens for this message:
            <code
              v-for="token in row.tokens"
              :key="token"
              class="ml-1 rounded bg-surface-gray-2 px-1.5 py-0.5"
            >{{ '{' + token + '}' }}</code>
          </p>
          <FormControl
            v-model="form.subject"
            label="Subject"
            :description="`${form.subject.length}/${SUBJECT_MAX} characters`"
          />
          <FormControl
            v-model="form.body"
            type="textarea"
            label="Body"
          />
          <FormControl
            v-model="form.is_enabled"
            type="checkbox"
            label="Use this wording instead of the default"
          />
          <p
            v-if="formError"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            {{ formError }}
          </p>
          <div class="flex items-center gap-2">
            <Button
              variant="solid"
              theme="blue"
              :loading="save.loading"
              @click="submit"
            >
              Save
            </Button>
            <Button
              variant="ghost"
              @click="cancel"
            >
              Cancel
            </Button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
