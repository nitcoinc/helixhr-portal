<script setup>
// Plan 2026-10-15-001 U6: one Settings section for people's access -- the
// "Who has what" list, one person's managed portal roles, HR roles and
// cross-company grant, quick-fill profiles, the saved-access summary, and
// one Save. Replaces the Portal roles and Company access sections.
//
// The server decides everything (U5): which roles and companies this caller
// may change (`can_edit_roles`, `can_edit_hr_roles`, `can_edit_grant`,
// `grantable`) and whether the person changed since the panel loaded.
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue'
import { call } from '@/lib/api'
import { getProfiles, matchProfiles } from '@/lib/accessProfiles'
import GrantEditor from '@/components/settings/GrantEditor.vue'

const MANAGED_ROLES = ['HelixHR Report Manager', 'HelixHR Delivery Manager', 'HelixHR Notification Manager', 'IT Team']
const HR_ROLES = ['HR User', 'HR Manager']
const ROLE_LABELS = {
  'HelixHR Report Manager': 'Report Manager',
  'HelixHR Delivery Manager': 'Delivery Manager',
  'HelixHR Notification Manager': 'Notification Manager',
  'IT Team': 'IT Team',
  'HR User': 'HR User',
  'HR Manager': 'HR Manager',
}
const GRANT_LEVELS = ['All data', 'Reports only']
const PAGE = 50
const STALE_REFUSAL = 'changed while you were editing'

const id = useId()

// --- "Who has what" ------------------------------------------------------

const query = ref('')
const roleFilter = ref('')
const grantFilter = ref('')
const rows = ref([])
const hasMore = ref(false)
const listLoading = ref(false)
const listError = ref('')
let listSeq = 0
let timer = null

async function loadList(more = false) {
  const seq = ++listSeq
  listLoading.value = true
  listError.value = ''
  try {
    const page = await call('helixhr.api.list_people_access', {
      query: query.value.trim(),
      roles: roleFilter.value ? JSON.stringify([roleFilter.value]) : undefined,
      grant_level: grantFilter.value || undefined,
      start: more ? rows.value.length : 0,
      page_length: PAGE,
    })
    if (seq !== listSeq) return
    rows.value = more ? [...rows.value, ...page.rows] : page.rows
    hasMore.value = page.has_more
  } catch (failure) {
    if (seq === listSeq) listError.value = failure?.messages?.[0] || "Couldn't load people."
  } finally {
    if (seq === listSeq) listLoading.value = false
  }
}

watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(() => loadList(), 250)
})
watch([roleFilter, grantFilter], () => loadList())
onBeforeUnmount(() => clearTimeout(timer))
loadList()

const filtered = computed(() => !!(query.value.trim() || roleFilter.value || grantFilter.value))

function rowChips(row) {
  return matchProfiles(row.roles, row.grant_level)
}

// --- one person ----------------------------------------------------------

const selected = ref(null)
const person = ref(null)
const loading = ref(false)
const loadError = ref('')
const busy = ref(false)
const saveError = ref('')
const savedMessage = ref('')

const editRoles = ref({})
const editLevel = ref(null)
const editCompanies = ref([])
const editExport = ref(false)

function reset(data) {
  editRoles.value = { ...data.managed_roles, ...data.hr_roles }
  const grant = data.grant?.enabled ? data.grant : null
  editLevel.value = grant?.level || null
  editCompanies.value = [...(grant?.companies || [])]
  editExport.value = !!grant?.allow_export
}

const dirty = computed(() => {
  if (!person.value) return false
  const saved = { ...person.value.managed_roles, ...person.value.hr_roles }
  if (Object.keys(saved).some((role) => !!saved[role] !== !!editRoles.value[role])) return true
  return JSON.stringify(grantState()) !== JSON.stringify(savedGrantState())
})

function savedGrantState() {
  const grant = person.value?.grant?.enabled ? person.value.grant : null
  return grant ? { level: grant.level, companies: [...grant.companies].sort(), allow_export: !!grant.allow_export } : null
}

function grantState() {
  return editLevel.value
    ? { level: editLevel.value, companies: [...editCompanies.value].sort(), allow_export: !!editExport.value }
    : null
}

async function loadPerson(employee) {
  loading.value = true
  loadError.value = ''
  try {
    const data = await call('helixhr.api.get_person_access', { employee })
    person.value = data
    reset(data)
  } catch (failure) {
    loadError.value = failure?.messages?.[0] || "Couldn't load this person's access."
    person.value = null
  } finally {
    loading.value = false
  }
}

function open(row) {
  if (selected.value?.employee === row.employee) return
  if (dirty.value && !window.confirm('Discard your unsaved changes to this person?')) return
  selected.value = row
  saveError.value = ''
  savedMessage.value = ''
  loadPerson(row.employee)
}

function back() {
  if (dirty.value && !window.confirm('Discard your unsaved changes to this person?')) return
  selected.value = null
  person.value = null
}

// --- profiles ------------------------------------------------------------

const heldRoles = computed(() => Object.keys(editRoles.value).filter((role) => editRoles.value[role]))
const chips = computed(() => matchProfiles(heldRoles.value, editLevel.value))
const profiles = computed(() => getProfiles(!!person.value?.can_edit_hr_roles))

function profileActive(profile) {
  return chips.value.some((chip) => chip.key === profile.key)
}

function profileLocked(profile) {
  if (!person.value || busy.value) return true
  if (profile.grant) return !person.value.can_edit_grant
  return profile.roles.some((role) => !canEditRole(role))
}

/** A role chip adds its roles, and un-toggling it removes only those. The
 * two grant chips both set the single grant, so picking one replaces the
 * other. Employee clears every role and the grant this caller may edit. */
function toggleProfile(profile) {
  if (profileLocked(profile)) return
  const on = !profileActive(profile)
  if (profile.key === 'employee') {
    const next = { ...editRoles.value }
    for (const role of Object.keys(next)) if (canEditRole(role)) next[role] = false
    editRoles.value = next
    if (person.value.can_edit_grant) editLevel.value = null
  } else if (profile.grant) {
    editLevel.value = on ? profile.grant.level : null
    if (on && !editCompanies.value.length) editCompanies.value = [...person.value.grantable]
  } else {
    const next = { ...editRoles.value }
    for (const role of profile.roles) next[role] = on
    editRoles.value = next
  }
  touched()
}

function canEditRole(role) {
  if (!person.value) return false
  return HR_ROLES.includes(role) ? person.value.can_edit_hr_roles : person.value.can_edit_roles
}

function toggleRole(role) {
  editRoles.value = { ...editRoles.value, [role]: !editRoles.value[role] }
  touched()
}

function touched() {
  saveError.value = ''
  savedMessage.value = ''
}

const grantable = computed(() => [...new Set([...(person.value?.grantable || []), ...editCompanies.value])])

// --- save ----------------------------------------------------------------

function pick(roles, editable) {
  return editable ? Object.fromEntries(roles.map((role) => [role, !!editRoles.value[role]])) : undefined
}

async function save() {
  busy.value = true
  touched()
  const before = savedGrantState()
  const after = grantState()
  let grant
  if (JSON.stringify(before) !== JSON.stringify(after)) grant = after || { revoke: true }
  try {
    const data = await call('helixhr.api.save_person_access', {
      employee: person.value.employee,
      managed_roles: JSON.stringify(pick(MANAGED_ROLES, person.value.can_edit_roles) || {}),
      hr_roles: JSON.stringify(pick(HR_ROLES, person.value.can_edit_hr_roles) || {}),
      grant: grant ? JSON.stringify(grant) : undefined,
      user_modified: person.value.user_modified,
      grant_modified: person.value.grant_modified,
    })
    person.value = data
    reset(data)
    savedMessage.value = 'Saved. They see the change the next time they load the portal.'
    loadList()
  } catch (failure) {
    const message = failure?.messages?.[0] || ''
    if (message.includes(STALE_REFUSAL)) {
      await loadPerson(person.value.employee)
      saveError.value = 'This person changed while you were editing. Your edits were discarded.'
    } else {
      saveError.value = message || "That change didn't save. Try again."
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section
    aria-label="People &amp; access"
    class="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
  >
    <!-- Who has what. Below the tablet breakpoint it gives way to the
         person panel once someone is open (list, then detail). -->
    <div :class="selected ? 'hidden lg:block' : ''">
      <h2 class="text-base font-heading text-ink-gray-9">
        Who has what
      </h2>
      <div class="mt-3 grid gap-2 sm:grid-cols-3">
        <label class="sm:col-span-3">
          <span class="mb-1 block text-sm text-ink-gray-7">Find a person</span>
          <input
            v-model="query"
            type="search"
            class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
            placeholder="Name or employee ID"
            autocomplete="off"
            data-testid="people-access-search"
          >
        </label>
        <label>
          <span class="mb-1 block text-sm text-ink-gray-7">Role</span>
          <select
            v-model="roleFilter"
            class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2 text-sm"
          >
            <option value="">Any</option>
            <option
              v-for="role in [...MANAGED_ROLES, ...HR_ROLES]"
              :key="role"
              :value="role"
            >{{ ROLE_LABELS[role] }}</option>
          </select>
        </label>
        <label>
          <span class="mb-1 block text-sm text-ink-gray-7">Grant</span>
          <select
            v-model="grantFilter"
            class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2 text-sm"
          >
            <option value="">Any</option>
            <option
              v-for="level in GRANT_LEVELS"
              :key="level"
              :value="level"
            >{{ level }}</option>
          </select>
        </label>
      </div>

      <div
        v-if="listError"
        class="surface-alert mt-3 p-3 text-sm"
        role="alert"
      >
        {{ listError }}
        <button
          type="button"
          class="ml-2 min-h-11 cursor-pointer text-blue-700 underline underline-offset-2"
          @click="loadList()"
        >
          Retry
        </button>
      </div>
      <div
        v-else-if="listLoading && !rows.length"
        class="mt-3 space-y-2"
        aria-hidden="true"
      >
        <div
          v-for="n in 4"
          :key="n"
          class="h-12 animate-pulse rounded-md bg-surface-gray-2"
        />
      </div>
      <p
        v-else-if="!rows.length"
        class="surface-inset mt-3 p-3 text-sm text-ink-gray-7"
        data-testid="people-access-empty"
      >
        {{ filtered ? 'Nobody matches these filters.' : 'Nobody in your companies has a portal login yet.' }}
      </p>
      <ul
        v-else
        class="mt-3 divide-y divide-outline-gray-1 rounded-md border border-outline-gray-2"
        data-testid="people-access-list"
      >
        <li
          v-for="row in rows"
          :key="row.employee"
        >
          <button
            type="button"
            class="flex min-h-11 w-full cursor-pointer flex-col items-start gap-1 px-3 py-2 text-left hover:bg-surface-gray-2"
            :aria-current="selected?.employee === row.employee ? 'true' : undefined"
            :data-testid="`people-access-row-${row.user}`"
            @click="open(row)"
          >
            <span class="text-sm font-medium text-ink-gray-9">{{ row.employee_name }}
              <span class="font-normal text-ink-gray-6">· {{ row.company }}</span></span>
            <span class="flex flex-wrap gap-1">
              <span
                v-for="chip in rowChips(row)"
                :key="chip.key"
                class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-7"
              >{{ chip.label }}</span>
            </span>
          </button>
        </li>
      </ul>
      <button
        v-if="hasMore"
        type="button"
        class="mt-2 min-h-11 cursor-pointer text-sm text-blue-700 underline underline-offset-2"
        :disabled="listLoading"
        @click="loadList(true)"
      >
        {{ listLoading ? 'Loading…' : 'Show more' }}
      </button>
    </div>

    <!-- One person -->
    <div
      :class="selected ? '' : 'hidden lg:block'"
      data-testid="people-access-panel"
    >
      <button
        v-if="selected"
        type="button"
        class="mb-2 min-h-11 cursor-pointer text-sm text-blue-700 underline underline-offset-2 lg:hidden"
        @click="back"
      >
        Back to everyone
      </button>
      <div
        v-if="!selected"
        class="surface-inset p-4 text-sm text-ink-gray-7"
      >
        Pick a person to see and change their access.
      </div>
      <div
        v-else-if="loading"
        class="space-y-3"
        aria-hidden="true"
      >
        <div class="h-6 w-1/2 animate-pulse rounded bg-surface-gray-2" />
        <div class="h-40 animate-pulse rounded bg-surface-gray-2" />
      </div>
      <div
        v-else-if="loadError"
        class="surface-alert p-3 text-sm"
        role="alert"
      >
        {{ loadError }}
        <button
          type="button"
          class="ml-2 min-h-11 cursor-pointer text-blue-700 underline underline-offset-2"
          @click="loadPerson(selected.employee)"
        >
          Retry
        </button>
      </div>
      <div v-else-if="person">
        <h2 class="text-base font-heading text-ink-gray-9">
          {{ person.employee_name }}
        </h2>
        <p class="text-sm text-ink-gray-6">
          {{ person.user }} · {{ person.company }}
          <template v-if="person.direct_reports">
            · {{ person.direct_reports }} {{ person.direct_reports === 1 ? 'person reports' : 'people report' }} to them
          </template>
        </p>

        <div
          class="mt-3 rounded-md border border-outline-gray-2 bg-surface-gray-1 p-3"
          :class="dirty ? 'opacity-60' : ''"
        >
          <h3 class="text-xs font-medium text-ink-gray-7">
            Current access
          </h3>
          <p
            class="mt-1 text-sm text-ink-gray-9"
            data-testid="people-access-summary"
          >
            {{ person.summary }}
          </p>
        </div>

        <fieldset
          class="mt-4"
          :disabled="busy"
        >
          <legend class="text-sm font-medium text-ink-gray-9">
            Profiles
          </legend>
          <div class="mt-1 flex flex-wrap gap-2">
            <button
              v-for="profile in profiles"
              :key="profile.key"
              type="button"
              class="min-h-11 cursor-pointer rounded-full border px-3 text-sm aria-disabled:cursor-not-allowed aria-disabled:opacity-50 sm:min-h-9"
              :class="profileActive(profile) ? 'border-signal bg-surface-gray-2 text-ink-gray-9' : 'border-outline-gray-2 text-ink-gray-7'"
              :aria-pressed="profileActive(profile) ? 'true' : 'false'"
              :aria-disabled="profileLocked(profile) ? 'true' : undefined"
              @click="toggleProfile(profile)"
            >
              {{ profile.label }}
            </button>
            <span
              v-if="chips[0]?.key === 'custom'"
              class="inline-flex min-h-9 items-center rounded-full border border-dashed border-outline-gray-3 px-3 text-sm text-ink-gray-7"
            >Custom</span>
          </div>
        </fieldset>

        <fieldset
          class="mt-4"
          :disabled="busy"
        >
          <legend class="text-sm font-medium text-ink-gray-9">
            Portal roles
          </legend>
          <label
            v-for="role in MANAGED_ROLES"
            :key="role"
            class="flex min-h-11 items-center gap-2 text-sm sm:min-h-9"
          >
            <input
              type="checkbox"
              class="accent-signal-3"
              :checked="!!editRoles[role]"
              :aria-disabled="person.can_edit_roles ? undefined : 'true'"
              :aria-describedby="person.can_edit_roles ? undefined : `${id}-roles-locked`"
              @click="person.can_edit_roles ? toggleRole(role) : $event.preventDefault()"
            >
            {{ ROLE_LABELS[role] }}
          </label>
          <p
            v-if="!person.can_edit_roles"
            :id="`${id}-roles-locked`"
            class="mt-1 text-xs text-ink-gray-6"
            data-testid="roles-locked"
          >
            Home company only: portal roles can be changed only for people in your own company.
          </p>
        </fieldset>

        <fieldset
          class="mt-4"
          :disabled="busy"
        >
          <legend class="text-sm font-medium text-ink-gray-9">
            HR roles
          </legend>
          <label
            v-for="role in HR_ROLES"
            :key="role"
            class="flex min-h-11 items-center gap-2 text-sm sm:min-h-9"
          >
            <input
              type="checkbox"
              class="accent-signal-3"
              :checked="!!editRoles[role]"
              :aria-disabled="person.can_edit_hr_roles ? undefined : 'true'"
              :aria-describedby="person.can_edit_hr_roles ? undefined : `${id}-hr-locked`"
              @click="person.can_edit_hr_roles ? toggleRole(role) : $event.preventDefault()"
            >
            {{ ROLE_LABELS[role] }}
          </label>
          <p
            v-if="!person.can_edit_hr_roles"
            :id="`${id}-hr-locked`"
            class="mt-1 text-xs text-ink-gray-6"
            data-testid="hr-roles-locked"
          >
            Only a System Manager can change HR roles.
          </p>
        </fieldset>

        <GrantEditor
          class="mt-4"
          :grantable="grantable"
          :level="editLevel"
          :companies="editCompanies"
          :allow-export="editExport"
          :disabled="busy || !person.can_edit_grant"
          @update:level="(level) => { editLevel = level; touched() }"
          @update:companies="(companies) => { editCompanies = companies; touched() }"
          @update:allow-export="(value) => { editExport = value; touched() }"
        />
        <p
          v-if="!person.can_edit_grant"
          class="mt-1 text-xs text-ink-gray-6"
        >
          This grant names a company outside yours, so you can't change it.
        </p>

        <p
          v-if="saveError"
          class="surface-alert mt-3 p-3 text-sm"
          role="alert"
        >
          {{ saveError }}
        </p>
        <p
          v-if="savedMessage"
          class="surface-card mt-3 p-3 text-sm"
          role="status"
        >
          {{ savedMessage }}
        </p>

        <div class="sticky bottom-0 mt-4 flex items-center gap-3 border-t border-outline-gray-2 bg-surface-white py-3">
          <button
            type="button"
            class="inline-flex min-h-11 cursor-pointer items-center justify-center rounded-lg bg-signal px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!dirty || busy"
            data-testid="people-access-save"
            @click="save"
          >
            {{ busy ? 'Saving…' : 'Save changes' }}
          </button>
          <button
            v-if="dirty && !busy"
            type="button"
            class="min-h-11 cursor-pointer text-sm text-ink-gray-7 underline underline-offset-2"
            @click="reset(person)"
          >
            Discard
          </button>
          <span
            v-if="dirty"
            class="text-xs text-ink-gray-6"
          >Unsaved changes</span>
        </div>
      </div>
    </div>
  </section>
</template>
