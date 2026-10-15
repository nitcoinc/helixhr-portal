<script setup>
// Plan 2026-10-15-001 U6: one Settings section for one person: their managed
// portal roles, HR roles, cross-company grant, profile chips, summary, and
// one Save. Replaces the Portal roles and Company access sections.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Button } from 'frappe-ui'
import { call } from '@/lib/api'
import { matchProfiles, getProfiles } from '@/lib/accessProfiles'
import GrantEditor from '@/components/settings/GrantEditor.vue'

const ROLES_LIST = ['HelixHR Report Manager', 'HelixHR Delivery Manager', 'HelixHR Notification Manager', 'IT Team']
const HR_ROLES_LIST = ['HR User', 'HR Manager']
const ROLE_LABELS = {
  'HelixHR Report Manager': 'Report Manager',
  'HelixHR Delivery Manager': 'Delivery Manager',
  'HelixHR Notification Manager': 'Notification Manager',
  'IT Team': 'IT Team',
  'HR User': 'HR User',
  'HR Manager': 'HR Manager',
}

const props = defineProps({
  /** Optional: companies the caller may grant. When empty, fetched from
   * get_company_access on first person load. */
  grantable: { type: Array, default: () => [] },
})

// --- search & person load ------------------------------------------------

const query = ref('')
const matches = ref([])
let timer = null
let searchSeq = 0

watch(query, (value) => {
  clearTimeout(timer)
  const text = value.trim()
  if (text.length < 2) {
    matches.value = []
    return
  }
  timer = setTimeout(async () => {
    const seq = ++searchSeq
    try {
      const rows = await call('helixhr.api.list_people_access', { query: text })
      if (seq !== searchSeq) return
      matches.value = (rows.rows || []).map((r) => ({
        employee: r.employee,
        employee_name: r.employee_name,
        user: r.user,
        company: r.company,
      }))
    } catch {
      if (seq === searchSeq) matches.value = []
    }
  }, 250)
})
onBeforeUnmount(() => clearTimeout(timer))

const selected = ref(null)

function pick(person) {
  selected.value = person
  query.value = person.employee_name
  matches.value = []
  loadPerson(person.employee)
}

// --- person data ---------------------------------------------------------

const personData = ref(null)
const loading = ref(false)
const loadError = ref('')
const isSystemManager = ref(false)

async function loadPerson(employee) {
  loading.value = true
  loadError.value = ''
  try {
    const data = await call('helixhr.api.get_person_access', { employee })
    personData.value = data
    // Initialise the edit copies.
    editManaged.value = { ...data.managed_roles }
    editHrRoles.value = { ...data.hr_roles }
    editGrantLevel.value = data.grant?.level || null
    editGrantCompanies.value = [...(data.grant?.companies || [])]
    editGrantExport.value = data.grant?.allow_export || false
    savedUserModified.value = data.user_modified
    savedGrantModified.value = data.grant_modified
    dirty.value = false
    // System Manager check.
    isSystemManager.value = data.can_edit_hr_roles
  } catch (failure) {
    loadError.value = failure?.messages?.[0] || "Could not load this person's access."
    personData.value = null
  } finally {
    loading.value = false
  }
}

// --- edit state ----------------------------------------------------------

const editManaged = ref({})
const editHrRoles = ref({})
const editGrantLevel = ref(null)
const editGrantCompanies = ref([])
const editGrantExport = ref(false)
const savedUserModified = ref(null)
const savedGrantModified = ref(null)
const dirty = ref(false)
const busy = ref(false)
const saveError = ref('')
const savedMessage = ref('')

// Derive profile chips from current edit state.
const activeRoles = computed(() => {
  const roles = []
  for (const [role, enabled] of Object.entries(editManaged.value)) {
    if (enabled) roles.push(role)
  }
  for (const [role, enabled] of Object.entries(editHrRoles.value)) {
    if (enabled) roles.push(role)
  }
  return roles
})
const chips = computed(() => matchProfiles(activeRoles.value, editGrantLevel.value))
const profiles = computed(() => getProfiles(isSystemManager.value))

function applyProfile(profile) {
  // Apply roles from the profile.
  for (const role of ROLES_LIST) {
    editManaged.value = { ...editManaged.value, [role]: profile.roles.includes(role) }
  }
  for (const role of HR_ROLES_LIST) {
    editHrRoles.value = { ...editHrRoles.value, [role]: profile.roles.includes(role) }
  }
  // Apply grant from the profile.
  if (profile.grant) {
    editGrantLevel.value = profile.grant.level
  }
  // Mutual exclusion: Leadership and cross-company-reports.
  if (profile.key === 'leadership' && editGrantLevel.value !== 'All data') {
    editGrantLevel.value = 'All data'
  } else if (profile.key === 'cross-company-reports' && editGrantLevel.value !== 'Reports only') {
    editGrantLevel.value = 'Reports only'
  }
  markDirty()
}

function toggleManaged(role) {
  editManaged.value = { ...editManaged.value, [role]: !editManaged.value[role] }
  markDirty()
}

function toggleHrRole(role) {
  editHrRoles.value = { ...editHrRoles.value, [role]: !editHrRoles.value[role] }
  markDirty()
}

function onGrantLevel(level) {
  editGrantLevel.value = level
  markDirty()
}

function onGrantCompanies(companies) {
  editGrantCompanies.value = companies
  markDirty()
}

function onGrantExport(val) {
  editGrantExport.value = val
  markDirty()
}

function markDirty() {
  dirty.value = true
  saveError.value = ''
  savedMessage.value = ''
}

/** Whether the list of companies the user can toggle is non-empty. Must be
 * at least as many as the current grant companies. */
const effectiveGrantable = computed(() => {
  const current = editGrantCompanies.value || []
  const all = new Set([...props.grantable, ...current])
  return [...all]
})

// --- save ----------------------------------------------------------------

async function save() {
  busy.value = true
  saveError.value = ''
  savedMessage.value = ''
  try {
    const data = await call('helixhr.api.save_person_access', {
      employee: selected.value.employee,
      managed_roles: JSON.stringify(editManaged.value),
      hr_roles: JSON.stringify(editHrRoles.value),
      grant_data: editGrantLevel.value
        ? JSON.stringify({
            level: editGrantLevel.value,
            companies: editGrantCompanies.value,
            allow_export: editGrantExport.value,
          })
        : null,
      user_modified: savedUserModified.value,
      grant_modified: savedGrantModified.value,
    })
    // Reload with fresh data.
    personData.value = data
    savedUserModified.value = data.user_modified
    savedGrantModified.value = data.grant_modified
    dirty.value = false
    savedMessage.value = 'Changes saved.'
  } catch (failure) {
    // Stale refusal: reload the person.
    if ((failure?.messages?.[0] || '').includes('changed while you were editing')) {
      saveError.value = 'This person changed while you were editing. Your edits were discarded.'
      if (selected.value) loadPerson(selected.value.employee)
    } else {
      saveError.value = failure?.messages?.[0] || "That change didn't save. Try again."
    }
  } finally {
    busy.value = false
  }
}

// --- unsaved-changes guard -----------------------------------------------

function confirmSwitch(person) {
  if (dirty.value) {
    // The caller (template) handles the confirm dialog via v-if check.
  }
  pick(person)
}

const STALE_REFUSAL = 'changed while you were editing'
</script>

<template>
  <section aria-label="People &amp; access">
    <!-- Person search + list (above tablet breakpoint, side-by-side) -->
    <div class="grid gap-4 lg:grid-cols-2">
      <!-- Left: search and results -->
      <div>
        <label
          for="people-access-search"
          class="mb-1 block text-sm font-medium text-ink-gray-9"
        >Search people</label>
        <input
          id="people-access-search"
          v-model="query"
          type="search"
          class="min-h-11 w-full rounded-md border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-8"
          placeholder="Type a name…"
          autocomplete="off"
        >
        <ul
          v-if="matches.length"
          class="mt-1 max-h-48 overflow-y-auto rounded-md border border-outline-gray-2 bg-surface-white shadow-lg"
        >
          <li
            v-for="person in matches"
            :key="person.employee"
            class="cursor-pointer px-3 py-2 text-sm hover:bg-surface-gray-2"
            @click="pick(person)"
          >
            <span class="font-medium text-ink-gray-9">{{ person.employee_name }}</span>
            <span class="text-ink-gray-6"> · {{ person.company }}</span>
          </li>
        </ul>
        <p
          v-if="!selected && !matches.length && query.trim().length >= 2 && !loading"
          class="mt-2 text-sm text-ink-gray-6"
        >
          No one found.
        </p>
      </div>

      <!-- Right: person panel -->
      <div v-if="loading">
        <p class="text-sm text-ink-gray-6">
          Loading…
        </p>
      </div>
      <div
        v-else-if="loadError"
        class="surface-alert p-3 text-sm"
        role="alert"
      >
        {{ loadError }}
        <button
          type="button"
          class="ml-2 underline text-blue-700 underline-offset-2"
          @click="selected ? loadPerson(selected.employee) : null"
        >
          Retry
        </button>
      </div>
      <div
        v-else-if="personData"
      >
        <!-- Person header -->
        <h3 class="text-base font-heading text-ink-gray-9">
          {{ personData.employee_name }}
        </h3>
        <p class="text-sm text-ink-gray-6">
          {{ personData.user }} · {{ personData.company }}
        </p>

        <!-- Profile chips -->
        <div class="mt-3 flex flex-wrap gap-2">
          <span
            v-for="chip in chips"
            :key="chip.key"
            class="inline-flex items-center gap-1 rounded-full bg-surface-gray-2 px-3 py-1 text-xs font-medium"
            :class="chip.key === 'custom' ? 'border border-signal text-ink-gray-6' : 'text-ink-gray-9'"
            :aria-pressed="chip.key !== 'custom' ? 'true' : undefined"
          >
            {{ chip.label }}
          </span>
        </div>

        <!-- Profile quick-fill buttons -->
        <div class="mt-2 flex flex-wrap gap-1">
          <button
            v-for="profile in profiles"
            :key="profile.key"
            type="button"
            class="min-h-9 cursor-pointer rounded-md px-2 py-1 text-xs font-medium text-ink-gray-6 hover:bg-surface-gray-2"
            :aria-label="`Apply ${profile.label} profile`"
            @click="applyProfile(profile)"
          >
            {{ profile.label }}
          </button>
        </div>

        <!-- Managed roles -->
        <fieldset class="mt-4">
          <legend class="text-sm font-medium text-ink-gray-9">
            Portal roles
          </legend>
          <div
            v-for="role in ROLES_LIST"
            :key="role"
            class="flex items-center gap-2 text-sm"
          >
            <input
              type="checkbox"
              :checked="editManaged[role]"
              :disabled="!personData.can_edit_roles || busy"
              :class="'mr-1.5 inline-flex accent-signal-3'"
              :aria-describedby="!personData.can_edit_roles ? 'home-company-only' : undefined"
              @change="toggleManaged(role)"
            >
            <label>{{ ROLE_LABELS[role] || role }}</label>
          </div>
          <p
            v-if="!personData.can_edit_roles"
            id="home-company-only"
            class="mt-1 text-xs text-ink-gray-6"
          >
            Home company only — this person is outside your company.
          </p>
        </fieldset>

        <!-- HR roles (System Manager only) -->
        <fieldset
          v-if="personData.can_edit_hr_roles"
          class="mt-4"
        >
          <legend class="text-sm font-medium text-ink-gray-9">
            HR roles
          </legend>
          <div
            v-for="role in HR_ROLES_LIST"
            :key="role"
            class="flex items-center gap-2 text-sm"
          >
            <input
              type="checkbox"
              :checked="editHrRoles[role]"
              :disabled="busy"
              :class="'mr-1.5 inline-flex accent-signal-3'"
              @change="toggleHrRole(role)"
            >
            <label>{{ ROLE_LABELS[role] || role }}</label>
          </div>
        </fieldset>

        <!-- Grant editor -->
        <GrantEditor
          v-if="personData.grant || effectiveGrantable.length"
          class="mt-4"
          :grantable="effectiveGrantable"
          :level="editGrantLevel"
          :companies="editGrantCompanies"
          :allow-export="editGrantExport"
          :show-level="false"
          :disabled="busy"
          @update:level="onGrantLevel"
          @update:companies="onGrantCompanies"
          @update:allow-export="onGrantExport"
        />

        <!-- Summary (saved state, dims while dirty) -->
        <div
          class="mt-4 rounded-md border border-outline-gray-2 bg-surface-gray-1 p-3"
          :class="dirty ? 'opacity-60' : ''"
        >
          <h4 class="text-xs font-medium text-ink-gray-7">
            Current access
          </h4>
          <p class="mt-1 text-sm text-ink-gray-9">
            {{ personData.summary }}
          </p>
        </div>

        <!-- Messages -->
        <p
          v-if="saveError"
          class="surface-alert mt-3 p-3 text-sm"
          role="alert"
        >
          {{ saveError }}
        </p>
        <p
          v-if="savedMessage"
          class="surface-card mt-3 p-3 text-sm text-ink-green-6"
          role="status"
        >
          {{ savedMessage }}
        </p>

        <!-- Save bar -->
        <div class="mt-4 flex items-center gap-3 border-t border-outline-gray-2 pt-4">
          <button
            type="button"
            class="inline-flex min-h-11 cursor-pointer items-center justify-center rounded-lg bg-signal px-4 py-2 text-sm font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="!dirty || busy"
            @click="save"
          >
            {{ busy ? 'Saving…' : 'Save changes' }}
          </button>
          <span
            v-if="dirty"
            class="text-xs text-ink-amber-6"
          >Unsaved changes</span>
        </div>
      </div>

      <!-- Empty state -->
      <div
        v-else-if="!selected"
        class="surface-inset p-4 text-sm text-ink-gray-7"
      >
        Search for a person to view and edit their access.
      </div>
    </div>
  </section>
</template>