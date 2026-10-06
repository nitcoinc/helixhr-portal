<script setup>
import { reactive, ref, watch } from 'vue'
import { createResource, FormControl, Button } from 'frappe-ui'

// Plan 2026-10-06-001 U2 (R1, R2). The backdated leave rule used to live in
// site config, where HR could not find it; this tab is the screen. The
// server holds the real gate (`_is_hr`) and the real bounds (the Single's
// own validate()), so this form is only the surface.
const rules = createResource({ url: 'helixhr.api.get_leave_rules', auto: true })
const form = reactive({ backdated_grace_days: 1, backdated_exempt_role: '' })
const formError = ref('')
const saved = ref(false)

watch(
  () => rules.data,
  (data) => {
    if (!data) return
    form.backdated_grace_days = data.backdated_grace_days
    form.backdated_exempt_role = data.backdated_exempt_role || ''
  },
  { immediate: true },
)

const save = createResource({ url: 'helixhr.api.save_leave_rules', method: 'POST' })

async function submit() {
  formError.value = ''
  saved.value = false
  try {
    const data = await save.submit({
      backdated_grace_days: form.backdated_grace_days,
      backdated_exempt_role: form.backdated_exempt_role || '',
    })
    rules.setData(data)
    saved.value = true
  } catch (error) {
    formError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div
    class="space-y-4"
    data-testid="settings-leave-rules"
  >
    <h2 class="label">
      Leave rules
    </h2>

    <!-- R2: the plain-words explanation, including the two things people
         get wrong -- HR Manager is always unlimited, and HR Settings'
         HRMS check must stay off. -->
    <div class="surface-card elev-1 space-y-3 p-4 text-sm text-ink-gray-7">
      <p>
        An employee can normally start leave only a few working days in the past. Older
        dates are refused with a message telling them to ask HR.
      </p>
      <p>
        <span class="font-medium text-ink-gray-9">HR Manager is always unlimited</span>, whatever
        the setting below says. You can also name one more role that is unlimited.
      </p>
      <p>
        Keep HR Settings'
        <span class="font-medium text-ink-gray-9">Restrict Backdated Leave Application</span>
        off. HRMS's own check looks at who is submitting and would block approvers too; this
        rule is what replaces it.
      </p>
    </div>

    <div class="surface-card elev-1 space-y-4 p-4">
      <FormControl
        v-model="form.backdated_grace_days"
        type="number"
        label="Working days back"
        description="0 to 365. 0 means no backdating at all."
      />
      <div class="space-y-1.5">
        <label
          for="settings-leave-rules-role"
          class="text-sm text-ink-gray-7"
        >
          Exempt role (optional)
        </label>
        <!-- A native select, matching CategoriesSection's role picker. Empty
             means only HR Manager is unlimited. -->
        <select
          id="settings-leave-rules-role"
          v-model="form.backdated_exempt_role"
          data-testid="settings-leave-rules-role"
          class="block w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
        >
          <option value="">
            None
          </option>
          <option
            v-for="role in rules.data?.roles || []"
            :key="role"
            :value="role"
          >
            {{ role }}
          </option>
        </select>
      </div>

      <p
        v-if="formError"
        class="surface-alert p-3 text-sm"
        role="alert"
      >
        {{ formError }}
      </p>
      <p
        v-else-if="saved"
        class="text-sm text-ink-gray-6"
        role="status"
      >
        Saved.
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
      </div>
    </div>
  </div>
</template>
