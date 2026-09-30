<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { createResource, FormControl, Button, Dialog } from 'frappe-ui'
import Avatar from '@/components/Avatar.vue'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import ProfileFields from '@/components/profile/ProfileFields.vue'
import ProfileTable from '@/components/profile/ProfileTable.vue'
import CorrectionDialog from '@/components/profile/CorrectionDialog.vue'
import { uploadMyPhoto } from '@/lib/api'
import { PHOTO_ACCEPT, PHOTO_MAX_MB, photoRefusal } from '@/lib/profilePhoto'
import { setMyPhoto } from '@/lib/session'

// Plan 2026-09-29-001 U5. Everything HR holds about the employee, in five
// addressable tabs, so a wrong date of birth is found here rather than in
// Desk -- and one tap files a correction.
//
// One read feeds every tab: `helixhr.api.get_my_profile`, resolved from the
// session, allow-listed and masked on the server. It replaced
// `frappe.client.get` (which silently drops permission-locked fields) plus
// `get_dashboard` (read only for the manager's name).
const props = defineProps({
  section: { type: String, default: 'personal' },
})

const profile = createResource({ url: 'helixhr.api.get_my_profile', auto: true })

const SECTIONS = [
  { key: 'personal', label: 'Personal' },
  { key: 'job', label: 'Job' },
  { key: 'contact', label: 'Contact & emergency' },
  { key: 'history', label: 'History' },
  { key: 'bank', label: 'Bank & IDs' },
]
const active = computed(() => SECTIONS.find((tab) => tab.key === props.section) || SECTIONS[0])

// Headed runs inside the two long tabs (design review: a flat 17-row list
// is the generic fallback the design system exists to avoid).
const GROUPS = {
  job: [
    {
      label: 'Role',
      fields: ['company', 'department', 'designation', 'grade', 'employment_type', 'branch', 'reports_to'],
    },
    {
      label: 'Dates',
      fields: ['final_confirmation_date', 'contract_end_date', 'notice_number_of_days', 'date_of_retirement'],
    },
    { label: 'Schedule', fields: ['default_shift', 'holiday_list'] },
    { label: 'Approvers', fields: ['leave_approver', 'expense_approver', 'shift_request_approver'] },
  ],
  bank: [
    { label: 'Bank', fields: ['salary_mode', 'bank_name', 'bank_ac_no', 'ifsc_code', 'micr_code', 'iban'] },
    { label: 'Tax and IDs', fields: ['pan_number', 'provident_fund_account'] },
    { label: 'Passport', fields: ['passport_number', 'date_of_issue', 'valid_upto', 'place_of_issue'] },
    { label: 'Health insurance', fields: ['health_insurance_provider', 'health_insurance_no'] },
  ],
}

const sectionData = computed(() => profile.data?.sections?.[active.value.key] || null)
const sectionFailed = computed(() => (profile.data?.failed_sections || []).includes(active.value.key))
const correctionCategory = computed(() => profile.data?.correction_category || null)
// `helixhr_hr_contact`, rendered as a window global by the portal shell.
const hrContactEmail = window.helixhr_hr_contact || ''

function tabRoute(key) {
  return key === 'personal' ? '/profile' : `/profile/${key}`
}

// ── The identity band ──────────────────────────────────────────────────
// Who you are, on the deep field, with the initials monogram in signal
// yellow -- the one place on this screen the accent is legal.
function valueOf(sectionKey, fieldname) {
  return profile.data?.sections?.[sectionKey]?.fields?.find((f) => f.fieldname === fieldname)?.value || ''
}
const employeeName = computed(() => profile.data?.employee_name || '')
const initials = computed(() =>
  employeeName.value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join(''),
)
const roleLine = computed(() =>
  [valueOf('job', 'designation'), valueOf('job', 'department')].filter(Boolean).join(' · '),
)
const placeLine = computed(() => {
  const manager = valueOf('job', 'reports_to')
  return [manager ? `Reports to ${manager}` : null, valueOf('job', 'branch')].filter(Boolean).join(' · ')
})

// ── Photo (plan 2026-09-30-001 U5) ─────────────────────────────────────
// Only the caller's own: both methods resolve the employee from the session.
// The server re-checks and re-encodes; `photoRefusal` just saves a doomed
// upload. A success updates the band and the shell in place.
const photoUrl = ref(null)
watch(
  () => profile.data?.photo_url,
  (url) => {
    photoUrl.value = url || null
  },
  { immediate: true },
)
const photoInput = ref(null)
const photoBusy = ref(false)
const photoError = ref('')
const confirmingRemove = ref(false)
const removePhoto = createResource({ url: 'helixhr.api.remove_my_photo', method: 'POST' })

// The live region stays mounted so a new sentence is announced; while the
// remove dialog is open its own alert says it instead.
const bandError = computed(() => (confirmingRemove.value ? '' : photoError.value))

function setPhoto(url) {
  photoUrl.value = url || null
  setMyPhoto(url)
}

function pickPhoto() {
  photoError.value = ''
  photoInput.value?.click()
}

async function onPhotoChange(event) {
  const file = event.target.files?.[0] || null
  // Cleared so picking the same file again after a refusal still fires.
  event.target.value = ''
  if (!file) return
  photoError.value = photoRefusal(file)
  if (photoError.value) return
  photoBusy.value = true
  try {
    const result = await uploadMyPhoto(file)
    setPhoto(result?.photo_url)
  } catch (error) {
    photoError.value = error?.messages?.[0] || 'Your photo could not be saved. Try again.'
  } finally {
    photoBusy.value = false
  }
}

function askRemovePhoto() {
  photoError.value = ''
  confirmingRemove.value = true
}

async function doRemovePhoto() {
  photoError.value = ''
  try {
    await removePhoto.submit()
    setPhoto(null)
    confirmingRemove.value = false
  } catch (error) {
    photoError.value = error?.messages?.[0] || 'Your photo could not be removed. Try again.'
  }
}

// ── Corrections ────────────────────────────────────────────────────────
const correcting = ref(null)
const showCorrection = ref(false)
function requestCorrection(field) {
  correcting.value = field
  showCorrection.value = true
}

// ── Editable contact fields, one Save bar ──────────────────────────────
// `update_my_profile` takes several fields at once, so one bar saves
// whatever actually changed and says how much that is.
const EDITABLE_FIELDS = [
  { field: 'cell_number', label: 'Mobile', type: 'text' },
  { field: 'personal_email', label: 'Personal email', type: 'email' },
  { field: 'current_address', label: 'Current address', type: 'textarea' },
  { field: 'permanent_address', label: 'Permanent address', type: 'textarea' },
  { field: 'person_to_be_contacted', label: 'Emergency contact name', type: 'text' },
  { field: 'relation', label: 'Relation', type: 'text' },
  { field: 'emergency_phone_number', label: 'Emergency contact phone', type: 'text' },
]

const form = reactive({})
const saved = reactive({})

function resetForm(data) {
  const fields = data?.sections?.contact?.fields
  if (!fields) return
  const values = Object.fromEntries(fields.map((f) => [f.fieldname, f.value]))
  for (const { field } of EDITABLE_FIELDS) {
    // A refetch never throws away what the person is still typing: a field
    // that differs from the *previous* baseline is theirs, and stays.
    const typing = form[field] !== undefined && form[field] !== saved[field]
    saved[field] = values[field] || ''
    if (!typing) form[field] = saved[field]
  }
}
watch(() => profile.data, resetForm, { immediate: true })

const changedFields = computed(() =>
  EDITABLE_FIELDS.map(({ field }) => field).filter((field) => form[field] !== saved[field]),
)
const dirty = computed(() => changedFields.value.length > 0)

const save = createResource({ url: 'helixhr.api.update_my_profile', method: 'POST' })
const saveError = ref('')
const justSaved = ref(false)

function discard() {
  for (const field of changedFields.value) form[field] = saved[field]
  saveError.value = ''
}

async function saveChanges() {
  saveError.value = ''
  const payload = Object.fromEntries(changedFields.value.map((field) => [field, form[field]]))
  try {
    // The server answers with the persisted values, so the baseline is what
    // the record now holds rather than what the browser hoped it sent.
    const persisted = await save.submit(payload)
    for (const { field } of EDITABLE_FIELDS) {
      saved[field] = persisted?.[field] ?? form[field]
      form[field] = saved[field]
    }
    justSaved.value = true
    setTimeout(() => (justSaved.value = false), 3000)
    // Refetch so every tab agrees with the record; the data on screen stays
    // put while it loads (`loading` below ignores a refetch).
    profile.reload()
  } catch (error) {
    // P2-R25: a failed save keeps every value the person typed.
    saveError.value = error?.messages?.[0] || 'Could not save that. Please try again.'
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="Your profile"
      subtitle="Everything HR holds about you. Spot something wrong? Request a correction."
    >
      <template #actions>
        <!-- HR and administrators only: the server sends `desk_url` for
             those roles and nobody else (get_my_profile). -->
        <a
          v-if="profile.data?.desk_url"
          :href="profile.data.desk_url"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex min-h-11 items-center rounded-lg border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2"
          data-testid="profile-desk-link"
        >
          Open in Desk
        </a>
      </template>
    </PageHeader>

    <AsyncState
      section="profile"
      :resource="profile"
      :loading="profile.loading && !profile.data"
      :empty="!profile.data"
      empty-title="We couldn't find your employee record"
      empty-body="Ask HR to link your sign-in to an employee record."
      skeleton="block"
      skeleton-height="h-96"
    >
      <div class="space-y-6">
        <section
          class="surface-field elev-2 flex items-center gap-4 p-5"
          aria-label="Your identity"
          data-testid="profile-identity"
        >
          <Avatar
            class="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-white/10 font-heading text-xl font-bold text-signal"
            :photo-url="photoUrl"
            :initials="initials || '—'"
            :name="employeeName"
            :size="64"
            data-testid="profile-avatar"
          />
          <div class="min-w-0 flex-1">
            <h2 class="type-section font-heading text-white">
              {{ employeeName || '—' }}
            </h2>
            <p
              v-if="roleLine"
              class="mt-0.5 text-sm text-blue-100"
            >
              {{ roleLine }}
            </p>
            <p
              v-if="placeLine"
              class="text-sm text-blue-200"
            >
              {{ placeLine }}
            </p>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <!-- The input is the real control's engine, not a tab stop:
                   the labelled button opens it. -->
              <input
                ref="photoInput"
                type="file"
                class="sr-only"
                tabindex="-1"
                aria-hidden="true"
                :accept="PHOTO_ACCEPT"
                data-testid="profile-photo-input"
                @change="onPhotoChange"
              >
              <button
                type="button"
                class="inline-flex min-h-8 cursor-pointer items-center rounded-md border border-white/25 px-2.5 text-xs font-medium text-blue-100 hover:bg-white/10 hover:text-white disabled:cursor-wait disabled:opacity-60"
                :disabled="photoBusy || removePhoto.loading"
                :aria-busy="photoBusy"
                @click="pickPhoto"
              >
                {{ photoBusy ? 'Saving photo…' : photoUrl ? 'Change photo' : 'Add photo' }}
              </button>
              <button
                v-if="photoUrl"
                type="button"
                class="inline-flex min-h-8 cursor-pointer items-center rounded-md px-2.5 text-xs font-medium text-blue-100 hover:bg-white/10 hover:text-white disabled:opacity-60"
                :disabled="photoBusy || removePhoto.loading"
                @click="askRemovePhoto"
              >
                Remove photo
              </button>
              <span class="text-xs text-blue-200">
                PNG or JPEG · up to <span class="tabular">{{ PHOTO_MAX_MB }}</span> MB
              </span>
            </div>
            <p
              class="text-sm text-signal"
              :class="{ 'mt-2': bandError }"
              role="alert"
              data-testid="profile-photo-error"
            >
              {{ bandError }}
            </p>
          </div>
        </section>

        <!-- One banner, not a dead button per row, when HR has retired the
             correction category. -->
        <p
          v-if="!correctionCategory"
          class="surface-card elev-1 p-3 text-sm text-ink-gray-7"
          data-testid="profile-contact-hr"
        >
          To correct anything on your profile, contact HR<template v-if="hrContactEmail">
            at
            <a
              :href="`mailto:${hrContactEmail}`"
              class="font-medium text-blue-700 hover:underline"
            >{{ hrContactEmail }}</a>
          </template>.
        </p>

        <nav
          class="flex flex-wrap gap-1 border-b border-outline-gray-1"
          aria-label="Profile sections"
        >
          <router-link
            v-for="tab in SECTIONS"
            :key="tab.key"
            :to="tabRoute(tab.key)"
            class="inline-flex min-h-11 cursor-pointer items-center rounded-t-lg px-3 py-2 text-sm font-medium"
            :aria-current="active.key === tab.key ? 'page' : undefined"
            :class="
              active.key === tab.key
                ? 'border-b-2 border-signal text-ink-gray-9'
                : 'text-ink-gray-6 hover:text-ink-gray-9'
            "
            :data-testid="`profile-tab-${tab.key}`"
          >
            {{ tab.label }}
          </router-link>
        </nav>

        <h2 class="type-section font-heading text-ink-gray-9">
          {{ active.label }}
        </h2>

        <div
          v-if="sectionFailed || !sectionData"
          class="surface-alert flex flex-wrap items-center justify-between gap-3 p-3 text-sm"
          role="alert"
        >
          <span>
            Couldn’t load this section.
            <template v-if="active.key === 'contact'">Editing is paused until it loads.</template>
          </span>
          <Button
            variant="subtle"
            @click="profile.reload()"
          >
            Retry
          </Button>
        </div>

        <template v-else>
          <p
            v-if="active.key === 'bank'"
            class="text-sm text-ink-gray-6"
          >
            Account and ID numbers show only their last four characters.
          </p>

          <ProfileFields
            :fields="sectionData.fields"
            :groups="GROUPS[active.key] || []"
            :can-correct="!!correctionCategory"
            @correct="requestCorrection"
          />

          <ProfileTable
            v-for="table in sectionData.tables"
            :key="table.fieldname"
            :table="table"
            :can-correct="!!correctionCategory"
            @correct="requestCorrection"
          />

          <section
            v-if="active.key === 'contact'"
            aria-labelledby="profile-editable-heading"
          >
            <h3
              id="profile-editable-heading"
              class="label mb-2"
            >
              You can update
            </h3>
            <div class="surface-card elev-1 space-y-4 p-4">
              <div
                v-for="row in EDITABLE_FIELDS"
                :key="row.field"
                :data-testid="`profile-editable-${row.field}`"
              >
                <FormControl
                  v-model="form[row.field]"
                  :label="row.label"
                  :type="row.type"
                />
              </div>
            </div>
          </section>
        </template>

        <p
          v-if="saveError"
          class="surface-alert p-3 text-sm"
          role="alert"
        >
          {{ saveError }}
        </p>

        <!-- One bar for the whole form, only once something has changed. -->
        <div
          v-if="dirty || justSaved"
          class="action-bar flex items-center justify-between gap-3"
          data-testid="profile-save-bar"
        >
          <p
            class="text-sm text-ink-gray-6"
            aria-live="polite"
          >
            <template v-if="dirty">
              <span class="tabular">{{ changedFields.length }}</span>
              unsaved change{{ changedFields.length === 1 ? '' : 's' }}
            </template>
            <template v-else>
              Saved
            </template>
          </p>
          <div
            v-if="dirty"
            class="flex shrink-0 items-center gap-2"
          >
            <Button
              variant="ghost"
              @click="discard"
            >
              Discard
            </Button>
            <Button
              variant="solid"
              theme="blue"
              :loading="save.loading"
              @click="saveChanges"
            >
              Save
            </Button>
          </div>
        </div>
      </div>
    </AsyncState>

    <CorrectionDialog
      v-if="correctionCategory"
      v-model="showCorrection"
      :field="correcting"
      :category="correctionCategory"
    />
    <!-- Removing is confirmed, never a single tap (the withdraw pattern). -->
    <Dialog
      v-model="confirmingRemove"
      :options="{ title: 'Remove your photo?', size: 'sm' }"
    >
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Colleagues will see your initials instead. You can add a photo again at any time.
        </p>
        <p
          v-if="photoError"
          class="mt-3 text-sm text-ink-red-4"
          role="alert"
        >
          {{ photoError }}
        </p>
        <div class="mt-4 flex items-center gap-2">
          <Button
            variant="solid"
            theme="red"
            :loading="removePhoto.loading"
            @click="doRemovePhoto"
          >
            Remove photo
          </Button>
          <Button
            variant="subtle"
            @click="confirmingRemove = false"
          >
            Keep it
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
