<script setup>
import { computed, nextTick, ref, useId, watch } from 'vue'
import { Button, createResource } from 'frappe-ui'
import { call } from '@/lib/api'
import { FALLBACK_ERROR, toPlainMessage } from '@/lib/errorMap'
import { formatDateTime } from '@/lib/dates'
import { notify } from '@/lib/notify'
import { TONE } from '@/lib/statusBadge'
import {
  ALL_ORGS_ONLY_ROLES,
  EXPORT_ROLES,
  LEADERSHIP_ROLE,
  addOrgs,
  addRole,
  applyLeadership,
  diffDraft,
  draftFromPerson,
  draftRows,
  exportState,
  leadershipApplied,
  removeOrg,
  removeRole,
  restoreRole,
  rolesWithoutOrgs,
  setAllOrgs,
  setExport,
  warningLabel,
} from '@/lib/accessProfiles'
import AsyncState from '@/components/AsyncState.vue'
import AccessReviewDialog from '@/components/settings/AccessReviewDialog.vue'

// Plan 2026-10-19-001 U7 (R6, R22, R23): one person's access. The saved
// summary, one row per role with its org chips (or an All orgs badge), and
// a review step that lists gains and losses before anything is saved.
//
// Three lock states, all the server's (`get_person_access().lock` and each
// row's `editable`): the whole person read-only with one reason (R11, R13,
// R10); only removing allowed (a disabled or Left person); or some rows
// locked and kept while the rest is editable. The save re-checks all of it.
const props = defineProps({
  user: { type: String, required: true },
})
const emit = defineEmits(['close', 'saved', 'dirty'])

const id = useId()
const heading = ref(null)
const draft = ref({ entries: [], sync: [] })

const person = createResource({
  url: 'helixhr.api.get_person_access',
  params: { user: props.user },
  auto: true,
  onSuccess(data) {
    draft.value = draftFromPerson(data)
    resetForms()
  },
})
const data = computed(() => person.data)

// Focus lands on the person's name once, when they first load (the page
// returns it to the opening row on close).
watch(
  () => !!person.data,
  (loaded) => {
    if (loaded) nextTick(() => heading.value?.focus({ preventScroll: false }))
  },
)

const lock = computed(() => data.value?.lock || null)
const readOnly = computed(() => !!lock.value && !lock.value.removal_only)
const removalOnly = computed(() => !!lock.value?.removal_only)
const canAdd = computed(() => !!data.value && !lock.value)

const labels = computed(() => {
  const map = {}
  for (const row of data.value?.rows || []) map[row.role] = row.label
  for (const row of data.value?.held_roles_without_rows || []) map[row.role] = row.label
  for (const row of data.value?.assignable?.roles || []) map[row.role] = row.label
  return map
})
const assignableCompanies = computed(() => data.value?.assignable?.companies || [])

const diff = computed(() => (data.value ? diffDraft(data.value, draft.value, labels.value) : { gains: [], losses: [] }))
const dirty = computed(() => diff.value.gains.length > 0 || diff.value.losses.length > 0)
watch(dirty, (value) => emit('dirty', value), { immediate: true })
const empty = computed(() => rolesWithoutOrgs(draft.value))

// --- add a role ----------------------------------------------------------

const newRole = ref('')
const newOrg = ref('')
const addingOrgsFor = ref('')
const orgPick = ref([])

function resetForms() {
  newRole.value = ''
  newOrg.value = ''
  addingOrgsFor.value = ''
  orgPick.value = []
}

const addableRoles = computed(() =>
  (data.value?.assignable?.roles || []).filter((role) => !draft.value.entries.some((entry) => entry.role === role.role)),
)
// A new role starts on the person's own org when the caller may give it
// there; otherwise (no Employee, or an org outside the caller's) it asks.
const ownOrgUsable = computed(() => !!data.value?.own_company && assignableCompanies.value.includes(data.value.own_company))
const newRoleAllOnly = computed(() => ALL_ORGS_ONLY_ROLES.includes(newRole.value))
const needsOrgPick = computed(() => !!newRole.value && !newRoleAllOnly.value && !ownOrgUsable.value)

function addNewRole() {
  const role = addableRoles.value.find((item) => item.role === newRole.value)
  if (!role) return
  if (needsOrgPick.value && !newOrg.value) return
  const company = newRoleAllOnly.value ? null : ownOrgUsable.value ? data.value.own_company : newOrg.value
  if (company === '__all') {
    draft.value = setAllOrgs(addRole(draft.value, role), role.role, true)
  } else {
    draft.value = addRole(draft.value, role, company)
  }
  newRole.value = ''
  newOrg.value = ''
}

// --- per role ------------------------------------------------------------

function canRemoveRole(entry) {
  if (readOnly.value) return false
  if (entry.outside) return true
  const keys = entry.allOrgs ? ['__all', ...entry.companies] : entry.companies
  return keys.some((key) => !entry.locked.includes(key)) || !keys.length
}
function canToggleAllOrgs(entry) {
  return canAdd.value && !!data.value.assignable?.all_orgs && !ALL_ORGS_ONLY_ROLES.includes(entry.role) && !entry.locked.length
}
function canAddOrg(entry) {
  return canAdd.value && !entry.allOrgs && orgsToAdd(entry).length > 0
}
function orgsToAdd(entry) {
  return assignableCompanies.value.filter((company) => !entry.companies.includes(company))
}
function chipLocked(entry, company) {
  return readOnly.value || entry.locked.includes(company)
}
function exportEditable(entry) {
  if (readOnly.value || entry.locked.length) return false
  // Removal only: export may go off, never on.
  return canAdd.value || exportState(entry) !== 'off'
}
function showRestore(entry) {
  return canAdd.value && entry.missing && !draft.value.sync.includes(entry.role)
}

function openAddOrgs(entry) {
  addingOrgsFor.value = entry.role
  orgPick.value = []
}
function confirmAddOrgs(entry) {
  if (orgPick.value.length) draft.value = addOrgs(draft.value, entry.role, orgPick.value)
  addingOrgsFor.value = ''
  orgPick.value = []
}
function onRemoveRole(entry) {
  draft.value = removeRole(draft.value, entry.role)
}
function onRemoveOrg(entry, company) {
  draft.value = removeOrg(draft.value, entry.role, company)
}
function onAllOrgs(entry, on) {
  draft.value = setAllOrgs(draft.value, entry.role, on)
}
function onExport(entry, on) {
  if (on && !canAdd.value) return
  draft.value = setExport(draft.value, entry.role, on)
}
function onRestore(entry) {
  draft.value = restoreRole(draft.value, entry.role)
}

const showLeadership = computed(
  () => (data.value?.presets || []).includes('Leadership') && !leadershipApplied(draft.value),
)
function onLeadership() {
  draft.value = applyLeadership(draft.value, labels.value[LEADERSHIP_ROLE])
}

function discard() {
  if (data.value) draft.value = draftFromPerson(data.value)
  resetForms()
}
defineExpose({ discard })

// --- review and save -----------------------------------------------------

const reviewing = ref(false)
const saving = ref(false)
const refusal = ref('')
const stale = ref(false)

function review() {
  if (!dirty.value || empty.value.length) return
  refusal.value = ''
  stale.value = false
  reviewing.value = true
}

async function confirm(reason) {
  saving.value = true
  refusal.value = ''
  try {
    await call('helixhr.api.save_person_access', {
      user: props.user,
      rows: draftRows(draft.value),
      token: data.value.token,
      reason: reason || undefined,
      sync_roles: draft.value.sync.length ? draft.value.sync : undefined,
    })
    reviewing.value = false
    notify.success(`Access saved for ${data.value.full_name}.`)
    emit('saved')
    person.reload()
  } catch (failure) {
    const message = toPlainMessage(failure?.messages?.[0] ?? failure?.message ?? '')
    if (failure?.exc_type === 'TimestampMismatchError' || message.startsWith('Someone changed this person')) {
      stale.value = true
    } else {
      refusal.value = message || FALLBACK_ERROR
    }
  } finally {
    saving.value = false
  }
}

function reloadStale() {
  reviewing.value = false
  stale.value = false
  person.reload()
}

function warningTone(kind) {
  return kind === 'problem' ? TONE.sentBack : TONE.resting
}
</script>

<template>
  <section
    class="surface-card p-4"
    :aria-labelledby="`${id}-name`"
    data-testid="person-access-editor"
  >
    <AsyncState
      section="person-access"
      :resource="person"
      :empty="false"
      skeleton="block"
      skeleton-height="h-80"
    >
      <template v-if="data">
        <h2
          :id="`${id}-name`"
          ref="heading"
          tabindex="-1"
          class="type-section text-ink-gray-9 focus:outline-none"
        >
          {{ data.full_name }}
        </h2>
        <p class="text-sm text-ink-gray-6">
          {{ data.own_company || 'No employee record' }} · {{ data.user }}<template v-if="data.employee">
            · {{ data.employee }}
          </template>
        </p>
        <ul
          v-if="data.warnings.length"
          class="mt-2 flex flex-wrap gap-1.5"
          aria-label="Warnings"
        >
          <li
            v-for="warning in data.warnings"
            :key="warning.key"
            class="rounded-full px-2 py-0.5 text-xs font-medium"
            :class="warningTone(warning.kind)"
          >
            {{ warningLabel(warning.key) }}
          </li>
        </ul>

        <p
          v-if="lock"
          class="surface-inset mt-3 p-3 text-sm text-ink-gray-8"
          data-testid="person-access-lock"
        >
          {{ lock.reason }}
        </p>

        <div class="mt-4">
          <h3 class="label">
            Access now
          </h3>
          <ul
            class="mt-1 space-y-1 text-sm text-ink-gray-8"
            data-testid="person-access-summary"
          >
            <li
              v-for="sentence in data.summary"
              :key="sentence"
            >
              {{ sentence }}
            </li>
          </ul>
        </div>

        <div class="mt-5">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="label">
              Roles
            </h3>
            <Button
              v-if="showLeadership"
              variant="outline"
              class="min-h-11"
              data-testid="access-apply-leadership"
              @click="onLeadership"
            >
              Apply Leadership
            </Button>
          </div>
          <p
            v-if="showLeadership"
            class="mt-1 text-xs text-ink-gray-6"
          >
            Leadership acts as HR Manager in all orgs, including new ones. It replaces only the HR Manager role.
          </p>

          <p
            v-if="!draft.entries.length"
            class="mt-2 text-sm text-ink-gray-6"
          >
            Employee access only.
          </p>
          <ul class="mt-2 space-y-3">
            <li
              v-for="entry in draft.entries"
              :key="entry.role"
              class="rounded-lg border border-outline-gray-2 p-3"
              :data-testid="`access-role-${entry.role}`"
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="font-medium text-ink-gray-9">
                  {{ entry.label }}
                </p>
                <div class="flex flex-wrap gap-2">
                  <Button
                    v-if="showRestore(entry)"
                    variant="outline"
                    class="min-h-11"
                    :label="`Restore role: ${entry.label}`"
                    @click="onRestore(entry)"
                  >
                    Restore role
                  </Button>
                  <Button
                    v-if="canRemoveRole(entry)"
                    variant="ghost"
                    class="min-h-11"
                    :label="`Remove role: ${entry.label}`"
                    :data-testid="`access-remove-role-${entry.role}`"
                    @click="onRemoveRole(entry)"
                  >
                    Remove role
                  </Button>
                </div>
              </div>

              <p
                v-if="entry.outside && !entry.companies.length && !entry.allOrgs"
                class="mt-1 text-sm text-ink-gray-7"
              >
                Set outside HelixHR: it gives no portal access until it has orgs.
              </p>
              <p
                v-if="entry.missing && !draft.sync.includes(entry.role)"
                class="mt-1 text-sm text-ink-gray-7"
              >
                Missing in Frappe: it gives no access until restored.
              </p>
              <p
                v-else-if="entry.missing"
                class="mt-1 text-sm text-ink-gray-7"
              >
                Will be restored in Frappe when you save.
              </p>

              <!-- Orgs: one badge for All orgs, else a chip per org. -->
              <div
                class="mt-2 flex flex-wrap items-center gap-2"
                :aria-label="`${entry.label} orgs`"
                role="group"
              >
                <span
                  v-if="entry.allOrgs"
                  class="inline-flex min-h-8 items-center rounded-full bg-heading px-3 text-sm font-medium text-white"
                >All orgs</span>
                <template v-else>
                  <span
                    v-for="company in entry.companies"
                    :key="company"
                    class="inline-flex min-h-8 items-center gap-1 rounded-full border border-outline-gray-2 bg-surface-gray-1 pl-3 text-sm text-ink-gray-8"
                    :class="chipLocked(entry, company) ? 'pr-3' : 'pr-0'"
                  >
                    {{ company }}
                    <button
                      v-if="!chipLocked(entry, company)"
                      type="button"
                      class="-my-1.5 inline-flex size-11 cursor-pointer items-center justify-center rounded-full text-ink-gray-6 hover:bg-surface-gray-3 hover:text-ink-gray-9"
                      :aria-label="`Remove ${company} from ${entry.label}`"
                      @click="onRemoveOrg(entry, company)"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        class="size-4"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                        stroke-linecap="round"
                        aria-hidden="true"
                      ><path d="M18 6 6 18M6 6l12 12" /></svg>
                    </button>
                  </span>
                </template>
                <Button
                  v-if="canAddOrg(entry) && addingOrgsFor !== entry.role"
                  variant="ghost"
                  class="min-h-11"
                  :data-testid="`access-add-org-${entry.role}`"
                  @click="openAddOrgs(entry)"
                >
                  {{ entry.outside && !entry.companies.length ? 'Give orgs' : 'Add org' }}
                </Button>
              </div>
              <p
                v-if="!entry.allOrgs && !entry.companies.length && !entry.outside"
                class="mt-1 text-sm text-ink-red-4"
              >
                Give {{ entry.label }} at least one org, or remove it.
              </p>

              <fieldset
                v-if="addingOrgsFor === entry.role"
                class="mt-2 rounded-md bg-surface-gray-1 p-2"
              >
                <legend class="sr-only">
                  Orgs to add to {{ entry.label }}
                </legend>
                <label
                  v-for="company in orgsToAdd(entry)"
                  :key="company"
                  class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8"
                >
                  <input
                    v-model="orgPick"
                    type="checkbox"
                    class="size-5"
                    :value="company"
                  >
                  {{ company }}
                </label>
                <div class="mt-1 flex gap-2">
                  <Button
                    variant="solid"
                    theme="blue"
                    class="min-h-11"
                    :disabled="!orgPick.length"
                    @click="confirmAddOrgs(entry)"
                  >
                    Add {{ orgPick.length || '' }} {{ orgPick.length === 1 ? 'org' : 'orgs' }}
                  </Button>
                  <Button
                    variant="subtle"
                    class="min-h-11"
                    @click="addingOrgsFor = ''"
                  >
                    Cancel
                  </Button>
                </div>
              </fieldset>

              <div class="mt-2 flex flex-wrap gap-x-5">
                <label
                  v-if="canToggleAllOrgs(entry)"
                  class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8"
                >
                  <input
                    type="checkbox"
                    class="size-5"
                    :checked="entry.allOrgs"
                    :data-testid="`access-all-orgs-${entry.role}`"
                    @change="onAllOrgs(entry, $event.target.checked)"
                  >
                  All orgs, including new ones
                </label>
                <label
                  v-if="EXPORT_ROLES.includes(entry.role)"
                  class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8"
                  :class="exportEditable(entry) ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'"
                >
                  <input
                    type="checkbox"
                    class="size-5"
                    :checked="exportState(entry) === 'on'"
                    :indeterminate="exportState(entry) === 'some'"
                    :disabled="!exportEditable(entry)"
                    @change="onExport(entry, $event.target.checked)"
                  >
                  Can export
                </label>
              </div>
            </li>
          </ul>

          <!-- Add a role: only the roles this caller may give. -->
          <form
            v-if="canAdd && addableRoles.length"
            class="mt-3 flex flex-wrap items-end gap-2"
            data-testid="access-add-role"
            @submit.prevent="addNewRole"
          >
            <label class="min-w-0 flex-1">
              <span class="mb-1 block text-sm text-ink-gray-7">Add role</span>
              <select
                v-model="newRole"
                class="block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                data-testid="access-add-role-select"
              >
                <option value="">
                  Choose a role
                </option>
                <option
                  v-for="role in addableRoles"
                  :key="role.role"
                  :value="role.role"
                >
                  {{ role.label }}
                </option>
              </select>
            </label>
            <label
              v-if="needsOrgPick"
              class="min-w-0 flex-1"
            >
              <span class="mb-1 block text-sm text-ink-gray-7">In org</span>
              <select
                v-model="newOrg"
                class="block min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
                data-testid="access-add-role-org"
              >
                <option value="">
                  Choose an org
                </option>
                <option
                  v-if="data.assignable.all_orgs"
                  value="__all"
                >
                  All orgs
                </option>
                <option
                  v-for="company in assignableCompanies"
                  :key="company"
                  :value="company"
                >
                  {{ company }}
                </option>
              </select>
            </label>
            <Button
              type="submit"
              variant="outline"
              class="min-h-11"
              :disabled="!newRole || (needsOrgPick && !newOrg)"
              data-testid="access-add-role-submit"
            >
              Add
            </Button>
          </form>
          <p
            v-if="newRole && !needsOrgPick && !newRoleAllOnly"
            class="mt-1 text-xs text-ink-gray-6"
          >
            Starts in {{ data.own_company }}, their own org.
          </p>
        </div>

        <!-- Review bar. Below lg the fixed phone tab bar owns the bottom
             edge, so the bar sits above it (the same offset as
             `.action-bar`). -->
        <div
          v-if="!readOnly"
          class="sticky bottom-[calc(56px+env(safe-area-inset-bottom))] z-[5] -mx-4 mt-5 flex flex-wrap items-center gap-3 border-t border-outline-gray-2 bg-surface-white px-4 py-3 lg:bottom-0"
          data-testid="access-review-bar"
        >
          <Button
            variant="solid"
            theme="blue"
            class="min-h-11"
            :disabled="!dirty || empty.length > 0"
            data-testid="access-review"
            @click="review"
          >
            Review changes
          </Button>
          <Button
            v-if="dirty"
            variant="ghost"
            class="min-h-11"
            @click="discard"
          >
            Discard
          </Button>
          <span
            v-if="dirty"
            class="text-xs text-ink-gray-6"
          >Not saved yet</span>
        </div>

        <details
          class="mt-5"
          data-testid="access-history"
        >
          <summary class="flex min-h-11 cursor-pointer items-center text-sm font-medium text-ink-gray-8">
            Recent changes ({{ data.history.length }})
          </summary>
          <p
            v-if="!data.history.length"
            class="text-sm text-ink-gray-6"
          >
            No changes yet.
          </p>
          <ol
            v-else
            class="mt-1 space-y-3"
          >
            <li
              v-for="change in data.history"
              :key="change.name"
              class="text-sm"
            >
              <p class="text-ink-gray-9">
                {{ change.changed_by_name }} · <span class="text-ink-gray-6">{{ formatDateTime(change.at) }}</span>
              </p>
              <p
                v-if="change.gains.length"
                class="text-ink-gray-8"
              >
                Gave: {{ change.gains.join('; ') }}
              </p>
              <p
                v-if="change.losses.length"
                class="text-ink-gray-8"
              >
                Removed: {{ change.losses.join('; ') }}
              </p>
              <p
                v-if="change.reason"
                class="whitespace-pre-line text-ink-gray-7"
              >
                Reason: {{ change.reason }}
              </p>
            </li>
          </ol>
        </details>
      </template>
    </AsyncState>

    <AccessReviewDialog
      v-if="data"
      v-model="reviewing"
      :diff="diff"
      :name="data.full_name"
      :busy="saving"
      :error="refusal"
      :stale="stale"
      @confirm="confirm"
      @reload="reloadStale"
    />
  </section>
</template>
