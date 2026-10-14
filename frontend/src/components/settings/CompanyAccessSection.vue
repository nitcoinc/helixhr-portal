<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { createResource, Button } from 'frappe-ui'
import { call } from '@/lib/api'
import { session } from '@/lib/session'
import AsyncState from '@/components/AsyncState.vue'

// Plan 2026-10-14-001 U3 (R2, R6): who may read other companies' data in the
// portal. The server is the gate (`save_company_access` re-checks the target,
// every company against the caller's reach, and the `modified` this screen
// holds); this screen only offers what it would accept. A grant is one
// decision on one person, so each action saves at once.
const LEVEL_HINTS = {
  'All data': 'People, organisation, projects and reports.',
  'Reports only': 'Reports, nothing else.',
}
// `_editable_grant`'s refusal when someone else changed the grant first.
const STALE = 'changed since you opened it'

const resource = createResource({ url: 'helixhr.api.get_company_access', auto: true })
const rows = computed(() => resource.data?.rows || [])
const grantable = computed(() => resource.data?.grantable || [])
const levels = computed(() => resource.data?.levels || Object.keys(LEVEL_HINTS))

const busy = ref('')
const saved = ref('')
// Where the last refusal belongs: the form, or one row (by user).
const error = ref({ at: '', message: '' })
const stale = computed(() => error.value.message.includes(STALE))
const confirming = ref({ user: '', action: '' })

function clearNotes() {
  error.value = { at: '', message: '' }
  saved.value = ''
  confirming.value = { user: '', action: '' }
}

function fail(at, failure) {
  error.value = { at, message: failure?.messages?.[0] || "That change didn't save. Try again." }
}

function putRow(row) {
  const others = rows.value.filter((item) => item.user !== row.user)
  resource.setData({ ...resource.data, rows: [row, ...others] })
}

// --- Add / edit form ---------------------------------------------------------

// null when closed; `editing` holds the row's `modified` for the stale check.
const form = ref(null)
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
      const found = await call('helixhr.api.search_company_access_targets', { query: text })
      if (seq === searchSeq) matches.value = found || []
    } catch (failure) {
      if (seq === searchSeq) fail('form', failure)
    }
  }, 250)
})
onBeforeUnmount(() => clearTimeout(timer))

function startAdd() {
  clearNotes()
  query.value = ''
  matches.value = []
  form.value = { user: '', employee_name: '', level: levels.value[0], companies: [], allow_export: false, modified: null }
}

function startEdit(row) {
  clearNotes()
  form.value = {
    user: row.user,
    employee_name: row.employee_name,
    level: row.level,
    companies: [...row.companies],
    allow_export: row.allow_export,
    modified: row.modified,
  }
}

function pick(target) {
  // Someone who already holds a grant is edited, not added twice: the
  // server needs that grant's `modified` to replace it.
  const existing = rows.value.find((row) => row.user === target.user)
  if (existing?.can_edit) {
    startEdit(existing)
    return
  }
  form.value = { ...form.value, user: target.user, employee_name: target.employee_name }
  query.value = ''
  matches.value = []
}

function cancel() {
  form.value = null
  clearNotes()
}

async function save() {
  const draft = form.value
  busy.value = 'form'
  error.value = { at: '', message: '' }
  saved.value = ''
  try {
    const row = await call('helixhr.api.save_company_access', {
      user: draft.user,
      level: draft.level,
      companies: JSON.stringify(draft.companies),
      allow_export: draft.allow_export ? 1 : 0,
      modified: draft.modified,
    })
    putRow(row)
    form.value = null
    saved.value = `Saved. ${row.employee_name} needs to reload the portal to see the change.`
  } catch (failure) {
    fail('form', failure)
  } finally {
    busy.value = ''
  }
}

// --- Row actions -------------------------------------------------------------

function ask(row, action) {
  clearNotes()
  confirming.value = { user: row.user, action }
}

async function reenable(row) {
  clearNotes()
  busy.value = row.user
  try {
    const updated = await call('helixhr.api.save_company_access', {
      user: row.user,
      level: row.level,
      companies: JSON.stringify(row.companies),
      allow_export: row.allow_export ? 1 : 0,
      modified: row.modified,
    })
    putRow(updated)
    saved.value = `Switched on. ${row.employee_name} needs to reload the portal to see the change.`
  } catch (failure) {
    fail(row.user, failure)
  } finally {
    busy.value = ''
  }
}

async function confirmAction(row) {
  const action = confirming.value.action
  busy.value = row.user
  error.value = { at: '', message: '' }
  saved.value = ''
  try {
    if (action === 'disable') {
      putRow(await call('helixhr.api.revoke_company_access', { user: row.user, modified: row.modified }))
      saved.value = `Switched off. ${row.employee_name} loses the access now; their menu updates when they reload.`
    } else {
      await call('helixhr.api.delete_company_access', { user: row.user, modified: row.modified })
      resource.setData({ ...resource.data, rows: rows.value.filter((item) => item.user !== row.user) })
      saved.value = `Removed. ${row.employee_name} loses the access now; their menu updates when they reload.`
    }
  } catch (failure) {
    fail(row.user, failure)
  } finally {
    busy.value = ''
    confirming.value = { user: '', action: '' }
  }
}

// After a stale refusal: fetch what the server holds now. An open edit form
// re-opens on the fresh values, so the change is decided again, not forced.
async function reload() {
  const draft = form.value
  clearNotes()
  await resource.reload()
  if (!draft?.user) return
  const fresh = rows.value.find((row) => row.user === draft.user)
  if (fresh?.can_edit) startEdit(fresh)
  else if (draft.modified) form.value = null
}

function readOnlyNote(row) {
  return row.user === session.user ? 'Your own access' : 'Outside your companies'
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="label">
        Company access
      </h2>
      <p class="mt-1 text-sm text-ink-gray-6">
        Let someone read another company's data in the portal. It is read-only: approvals and edits stay with
        each company's HR. The person reloads the portal once to see the new menu entries.
      </p>
    </div>

    <AsyncState
      section="company-access"
      :resource="resource"
      :loading="resource.loading && !resource.data"
      :empty="false"
      skeleton="block"
      skeleton-height="h-64"
    >
      <div
        v-if="resource.data"
        class="space-y-4"
      >
        <Button
          v-if="!form"
          variant="solid"
          theme="blue"
          :disabled="!!busy"
          data-testid="company-access-add"
          @click="startAdd"
        >
          Give company access
        </Button>

        <!-- Add / edit form -->
        <form
          v-if="form"
          class="surface-card elev-1 max-w-xl space-y-4 p-4"
          :aria-label="form.modified ? `Edit company access for ${form.employee_name}` : 'Give company access'"
          data-testid="company-access-form"
          @submit.prevent="save"
        >
          <div v-if="!form.user">
            <label
              for="company-access-search"
              class="text-sm font-medium text-ink-gray-7"
            >Find an employee</label>
            <input
              id="company-access-search"
              v-model="query"
              type="search"
              autocomplete="off"
              class="mt-1 block min-h-11 w-full rounded-md border border-outline-gray-2 px-3 text-sm"
              placeholder="Name or employee ID"
            >
            <p
              v-if="query.trim().length === 1"
              class="mt-1 text-sm text-ink-gray-6"
            >
              Type at least two characters.
            </p>
            <ul
              v-else-if="matches.length"
              class="mt-2 divide-y divide-outline-gray-1 rounded-md border border-outline-gray-1"
              aria-label="Matching employees"
            >
              <li
                v-for="target in matches"
                :key="target.user"
              >
                <button
                  type="button"
                  class="flex min-h-11 w-full cursor-pointer flex-col items-start px-3 py-2 text-left text-sm hover:bg-surface-gray-2"
                  @click="pick(target)"
                >
                  <span class="font-medium text-ink-gray-9">{{ target.employee_name }}</span>
                  <span class="text-xs text-ink-gray-5">{{ target.employee }} · {{ target.company }}</span>
                </button>
              </li>
            </ul>
            <p
              v-else-if="query.trim().length >= 2"
              class="mt-1 text-sm text-ink-gray-6"
            >
              No active employee in your companies matches that.
            </p>
          </div>
          <div
            v-else
            class="flex flex-wrap items-center gap-2"
          >
            <p class="text-sm text-ink-gray-7">
              For <span class="font-medium text-ink-gray-9">{{ form.employee_name }}</span>
            </p>
            <button
              v-if="!form.modified"
              type="button"
              class="inline-flex min-h-11 cursor-pointer items-center rounded-md px-3 text-sm text-ink-gray-7 hover:bg-surface-gray-2"
              @click="form.user = ''"
            >
              Change person
            </button>
          </div>

          <fieldset>
            <legend class="text-sm font-medium text-ink-gray-7">
              What they can see
            </legend>
            <div class="mt-1 space-y-1">
              <label
                v-for="level in levels"
                :key="level"
                class="flex min-h-11 cursor-pointer items-start gap-2 py-1 text-sm text-ink-gray-8"
              >
                <input
                  v-model="form.level"
                  type="radio"
                  name="company-access-level"
                  class="mt-0.5 size-5"
                  :value="level"
                >
                <span>
                  <span class="font-medium">{{ level }}</span>
                  <span class="block text-xs text-ink-gray-5">{{ LEVEL_HINTS[level] || '' }}</span>
                </span>
              </label>
            </div>
          </fieldset>

          <fieldset>
            <legend class="text-sm font-medium text-ink-gray-7">
              Companies
            </legend>
            <div class="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
              <label
                v-for="company in grantable"
                :key="company"
                class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8"
              >
                <input
                  v-model="form.companies"
                  type="checkbox"
                  class="size-5"
                  :value="company"
                >
                {{ company }}
              </label>
            </div>
          </fieldset>

          <label class="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink-gray-8">
            <input
              v-model="form.allow_export"
              type="checkbox"
              class="size-5"
            >
            May export reports
          </label>

          <div
            v-if="error.at === 'form'"
            class="flex flex-wrap items-center gap-2"
          >
            <p
              class="text-sm text-red-700"
              role="alert"
            >
              {{ error.message }}
            </p>
            <Button
              v-if="stale"
              variant="outline"
              @click="reload"
            >
              Reload
            </Button>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <Button
              type="submit"
              variant="solid"
              theme="blue"
              :loading="busy === 'form'"
              :disabled="!form.user || !!busy"
              data-testid="company-access-save"
            >
              Save
            </Button>
            <Button
              variant="ghost"
              :disabled="busy === 'form'"
              @click="cancel"
            >
              Cancel
            </Button>
          </div>
        </form>

        <p
          v-if="!rows.length"
          class="surface-card p-4 text-sm text-ink-gray-6"
          data-testid="company-access-empty"
        >
          No one has company access yet.
        </p>

        <!-- One card per grant; the details stack on phones and sit in a row
             on wider screens. -->
        <ul
          v-else
          class="space-y-2"
          aria-label="Company access grants"
        >
          <li
            v-for="row in rows"
            :key="row.user"
            class="surface-card elev-1 p-3"
            :class="error.at === row.user ? 'ring-2 ring-red-400' : ''"
            :data-testid="`company-access-row-${row.user}`"
          >
            <div class="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p class="font-medium text-ink-gray-9">
                  {{ row.employee_name }}
                </p>
                <p class="text-xs text-ink-gray-5">
                  {{ row.employee || row.user }}{{ row.home_company ? ` · ${row.home_company}` : '' }}
                </p>
              </div>
              <p
                v-if="!row.can_edit"
                class="text-xs text-ink-gray-5"
              >
                {{ readOnlyNote(row) }}
              </p>
            </div>

            <dl class="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
              <div>
                <dt class="text-xs text-ink-gray-5">
                  Level
                </dt>
                <dd class="text-ink-gray-8">
                  {{ row.level }}
                </dd>
              </div>
              <div>
                <dt class="text-xs text-ink-gray-5">
                  Companies
                </dt>
                <dd class="text-ink-gray-8">
                  {{ row.companies.join(', ') }}
                </dd>
              </div>
              <div>
                <dt class="text-xs text-ink-gray-5">
                  Export
                </dt>
                <dd class="text-ink-gray-8">
                  {{ row.allow_export ? 'Yes' : 'No' }}
                </dd>
              </div>
              <div>
                <dt class="text-xs text-ink-gray-5">
                  Status
                </dt>
                <dd
                  class="text-ink-gray-8"
                  data-testid="company-access-status"
                >
                  <span class="font-medium">{{ row.enabled ? 'Enabled' : 'Disabled' }}</span>
                  <span
                    v-if="!row.enabled && row.disabled_reason"
                    class="block text-xs text-ink-gray-5"
                  >{{ row.disabled_reason }}</span>
                </dd>
              </div>
            </dl>

            <div
              v-if="row.can_edit"
              class="mt-2 flex flex-wrap items-center gap-2"
            >
              <template v-if="confirming.user !== row.user">
                <Button
                  variant="outline"
                  :disabled="!!busy"
                  @click="startEdit(row)"
                >
                  Edit
                </Button>
                <Button
                  v-if="row.enabled"
                  variant="outline"
                  :disabled="!!busy"
                  @click="ask(row, 'disable')"
                >
                  Disable
                </Button>
                <Button
                  v-else
                  variant="outline"
                  :loading="busy === row.user"
                  :disabled="!!busy"
                  @click="reenable(row)"
                >
                  Re-enable
                </Button>
                <Button
                  variant="ghost"
                  theme="red"
                  :disabled="!!busy"
                  @click="ask(row, 'delete')"
                >
                  Delete
                </Button>
              </template>
              <template v-else>
                <p
                  class="text-sm text-ink-gray-8"
                  role="status"
                >
                  {{
                    confirming.action === 'disable'
                      ? `Switch off ${row.employee_name}'s company access?`
                      : `Delete ${row.employee_name}'s company access? This can't be undone.`
                  }}
                </p>
                <Button
                  variant="solid"
                  :theme="confirming.action === 'delete' ? 'red' : 'blue'"
                  :loading="busy === row.user"
                  data-testid="company-access-confirm"
                  @click="confirmAction(row)"
                >
                  {{ confirming.action === 'disable' ? 'Yes, disable' : 'Yes, delete' }}
                </Button>
                <Button
                  variant="ghost"
                  :disabled="busy === row.user"
                  @click="confirming = { user: '', action: '' }"
                >
                  Cancel
                </Button>
              </template>
            </div>

            <div
              v-if="error.at === row.user"
              class="mt-2 flex flex-wrap items-center gap-2"
            >
              <p
                class="text-sm text-red-700"
                role="alert"
              >
                {{ error.message }}
              </p>
              <Button
                v-if="stale"
                variant="outline"
                @click="reload"
              >
                Reload
              </Button>
            </div>
          </li>
        </ul>
      </div>
    </AsyncState>

    <p
      v-if="saved"
      class="text-sm text-ink-gray-6"
      role="status"
      aria-live="polite"
    >
      {{ saved }}
    </p>
  </div>
</template>
