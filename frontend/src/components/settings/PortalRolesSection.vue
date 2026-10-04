<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { createResource } from 'frappe-ui'
import { call } from '@/lib/api'

// HelixHR Portal Admin: who holds the four portal-only roles. The server is
// the gate (`set_portal_role` allow-lists the role, re-checks the person is an
// Active Employee in the caller's company, refuses the caller's own account);
// this screen only offers what it would accept. Each toggle saves at once --
// one role on one person is one decision, there is no draft to discard.
const LABELS = {
  'HelixHR Report Manager': 'Report Manager',
  'HelixHR Delivery Manager': 'Delivery Manager',
  'HelixHR Notification Manager': 'Notification Manager',
  'IT Team': 'IT Team',
}

const query = ref('')
const resource = createResource({
  url: 'helixhr.api.get_portal_role_holders',
  makeParams: () => ({ query: query.value.trim() }),
  auto: true,
})

let timer = null
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(() => resource.reload(), 250)
})
onBeforeUnmount(() => clearTimeout(timer))

const roles = computed(() => resource.data?.roles || Object.keys(LABELS))
const rows = computed(() => resource.data?.rows || [])
const searching = computed(() => query.value.trim().length > 0)
const tooShort = computed(() => query.value.trim().length === 1)

const busy = ref('')
const error = ref('')
const errorEmployee = ref('')
const saved = ref('')

function cellName(row, role) {
  return `${row.employee_name}: ${LABELS[role] || role}`
}

async function toggle(row, role, input) {
  const on = input.checked
  busy.value = `${row.employee}:${role}`
  error.value = ''
  errorEmployee.value = ''
  saved.value = ''
  try {
    const updated = await call('helixhr.api.set_portal_role', { employee: row.employee, role, enabled: on ? 1 : 0 })
    row.roles = updated.roles
    saved.value = `${row.employee_name}: ${LABELS[role] || role} ${on ? 'granted' : 'removed'}.`
  } catch (failure) {
    error.value = failure?.messages?.[0] || "That change didn't save. Try again."
    errorEmployee.value = row.employee
    // Put the checkbox back to what the server still holds.
    input.checked = !on
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="label">
        Portal roles
      </h2>
      <p class="mt-1 text-sm text-ink-gray-6">
        Who can manage reports, projects, email templates and IT requests in the portal. These roles open
        nothing in Desk. HR Manager and System Manager stay a Desk decision.
      </p>
    </div>

    <div class="max-w-md">
      <label
        for="portal-roles-search"
        class="text-sm font-medium text-ink-gray-7"
      >Find an employee</label>
      <input
        id="portal-roles-search"
        v-model="query"
        type="search"
        autocomplete="off"
        class="mt-1 block min-h-11 w-full rounded-md border border-outline-gray-2 px-3 text-sm"
        placeholder="Name or employee ID"
      >
    </div>

    <p
      v-if="resource.error"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      Portal roles didn't load. Reload the page to try again.
    </p>

    <p
      v-else-if="tooShort"
      class="text-sm text-ink-gray-6"
    >
      Type at least two characters.
    </p>

    <p
      v-else-if="!resource.loading && !rows.length"
      class="surface-card p-4 text-sm text-ink-gray-6"
      data-testid="portal-roles-empty"
    >
      {{ searching ? 'No active employee in your company matches that.' : 'Nobody holds a portal role yet. Find an employee above to grant one.' }}
    </p>

    <template v-else-if="rows.length">
      <p class="text-sm text-ink-gray-6">
        {{ searching ? 'Matching employees' : 'Current holders' }}
      </p>

      <!-- Wide screens: one row per person. -->
      <div class="surface-card elev-1 hidden overflow-x-auto sm:block">
        <table class="w-full text-sm">
          <thead class="text-left text-ink-gray-6">
            <tr class="border-b border-outline-gray-1">
              <th
                scope="col"
                class="px-4 py-2 font-medium"
              >
                Employee
              </th>
              <th
                v-for="role in roles"
                :key="role"
                scope="col"
                class="px-3 py-2 text-center font-medium"
              >
                {{ LABELS[role] || role }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-outline-gray-1">
            <tr
              v-for="row in rows"
              :key="row.employee"
              :class="errorEmployee === row.employee ? 'bg-red-50' : ''"
              :data-testid="`portal-role-row-${row.employee}`"
            >
              <th
                scope="row"
                class="px-4 py-2 text-left font-medium text-ink-gray-9"
              >
                {{ row.employee_name }}
                <span class="block text-xs font-normal text-ink-gray-5">{{ row.employee }}</span>
              </th>
              <td
                v-for="role in roles"
                :key="role"
                class="px-3 py-2 text-center"
              >
                <input
                  type="checkbox"
                  class="size-5 cursor-pointer disabled:cursor-not-allowed"
                  :aria-label="cellName(row, role)"
                  :checked="!!row.roles[role]"
                  :disabled="!!busy"
                  @change="toggle(row, role, $event.target)"
                >
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Phones: one card per person. -->
      <ul class="space-y-2 sm:hidden">
        <li
          v-for="row in rows"
          :key="row.employee"
          class="surface-card elev-1 p-3"
          :class="errorEmployee === row.employee ? 'ring-2 ring-red-400' : ''"
        >
          <p class="font-medium text-ink-gray-9">
            {{ row.employee_name }}
          </p>
          <p class="text-xs text-ink-gray-5">
            {{ row.employee }}
          </p>
          <div class="mt-2 grid grid-cols-1 gap-1 min-[380px]:grid-cols-2">
            <label
              v-for="role in roles"
              :key="role"
              class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8"
            >
              <input
                type="checkbox"
                class="size-5"
                :aria-label="cellName(row, role)"
                :checked="!!row.roles[role]"
                :disabled="!!busy"
                @change="toggle(row, role, $event.target)"
              >
              {{ LABELS[role] || role }}
            </label>
          </div>
        </li>
      </ul>
    </template>

    <p
      v-if="error"
      class="text-sm text-red-700"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="saved"
      class="text-sm text-ink-gray-6"
      role="status"
    >
      {{ saved }}
    </p>
  </div>
</template>
