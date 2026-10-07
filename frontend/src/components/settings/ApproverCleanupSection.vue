<script setup>
import { computed, ref } from 'vue'
import { createResource, Button } from 'frappe-ui'
import { call } from '@/lib/api'

// Plan 2026-10-07-001 U4. Leave, expense and shift approvers follow Reports
// to on every save; this screen fixes people saved before that rule (or
// written around it). The server is the gate (`_assert_portal_admin`, plus a
// scope re-check per person) and recomputes everything on apply -- this
// screen only previews what `apply_approver_cleanup` would do.
const PROBLEMS = {
  no_manager: 'No manager set',
  manager_not_active: 'Manager not active',
  manager_no_login: 'Manager has no login',
}
const KINDS = {
  'Leave Application': 'leave',
  'Shift Request': 'shift',
  'Expense Claim': 'expense',
}
// The server's per-call cap (`_APPROVER_CLEANUP_MAX`).
const MAX_PER_APPLY = 200

const preview = createResource({ url: 'helixhr.api.get_approver_cleanup', auto: true })
const willChange = computed(() => preview.data?.will_change || [])
const needsAttention = computed(() => preview.data?.needs_attention || [])

const selected = ref(new Set())
const confirming = ref(false)
const applying = ref(false)
const results = ref({})
const error = ref('')
const summary = ref('')

const allSelected = computed(
  () => willChange.value.length > 0 && willChange.value.every((row) => selected.value.has(row.employee)),
)
const selectedCount = computed(() => selected.value.size)
const movingTotal = computed(() =>
  willChange.value
    .filter((row) => selected.value.has(row.employee))
    .reduce((sum, row) => sum + Object.values(row.moving).reduce((a, b) => a + b, 0), 0),
)

function toggle(employee, on) {
  const next = new Set(selected.value)
  if (on) next.add(employee)
  else next.delete(employee)
  selected.value = next
  confirming.value = false
}

function toggleAll(on) {
  selected.value = new Set(on ? willChange.value.slice(0, MAX_PER_APPLY).map((row) => row.employee) : [])
  confirming.value = false
}

function currentApprovers(row) {
  const values = [...new Set(Object.values(row.current).map((value) => value || '—'))]
  return values.join(', ')
}

function becomes(row) {
  if (row.derived) return row.manager_name ? `${row.manager_name} (${row.derived})` : row.derived
  return 'Nobody'
}

function movingText(row) {
  const parts = Object.entries(row.moving).map(([doctype, count]) => `${count} ${KINDS[doctype] || doctype}`)
  return parts.length ? parts.join(', ') : 'None'
}

async function apply() {
  applying.value = true
  error.value = ''
  summary.value = ''
  const employees = [...selected.value]
  try {
    const rows = await call('helixhr.api.apply_approver_cleanup', { employees })
    const next = {}
    for (const row of rows) next[row.employee] = row
    results.value = next
    const failed = rows.filter((row) => !row.ok)
    summary.value = failed.length
      ? `${rows.length - failed.length} fixed, ${failed.length} not. The ones that failed are still selected -- try them again.`
      : `${rows.length} fixed.`
    selected.value = new Set(failed.map((row) => row.employee))
    // Re-read: fixed people leave "Will change", and anyone with no usable
    // manager moves to "Needs HR attention".
    await preview.reload()
  } catch (failure) {
    error.value = failure?.messages?.[0] || "That didn't apply. Nothing was changed for the people still listed -- try again."
  } finally {
    applying.value = false
    confirming.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="label">
        Approvers
      </h2>
      <p class="mt-1 text-sm text-ink-gray-6">
        Leave, expense and shift requests go to the person's manager (Reports to). These people are out of
        line: fixing them points their approvers, and anything they have waiting, at their manager. Only the
        manager is notified.
      </p>
    </div>

    <p
      v-if="preview.error"
      class="surface-alert p-3 text-sm"
      role="alert"
    >
      The approvers check didn't load. Reload the page to try again.
    </p>

    <p
      v-else-if="preview.loading && !preview.data"
      class="text-sm text-ink-gray-6"
      role="status"
    >
      Checking everyone's approvers…
    </p>

    <template v-else-if="preview.data">
      <!-- Will change -->
      <section
        aria-labelledby="approvers-will-change"
        class="space-y-3"
      >
        <h3
          id="approvers-will-change"
          class="font-heading text-base font-semibold text-ink-gray-9"
        >
          Will change ({{ willChange.length }})
        </h3>

        <p
          v-if="!willChange.length"
          class="surface-card p-4 text-sm text-ink-gray-6"
          data-testid="approvers-in-line"
        >
          Everyone's approvers already follow their manager.
        </p>

        <template v-else>
          <p
            v-if="willChange.length > MAX_PER_APPLY"
            class="text-sm text-ink-gray-6"
          >
            Up to {{ MAX_PER_APPLY }} people are fixed at a time. Apply, and the rest stay listed for the next round.
          </p>

          <!-- Wide screens. -->
          <div class="surface-card elev-1 hidden overflow-x-auto sm:block">
            <table class="w-full text-sm">
              <thead class="text-left text-ink-gray-6">
                <tr class="border-b border-outline-gray-1">
                  <th
                    scope="col"
                    class="w-10 px-4 py-2"
                  >
                    <input
                      type="checkbox"
                      class="size-5 cursor-pointer"
                      aria-label="Select everyone listed"
                      :checked="allSelected"
                      :disabled="applying"
                      @change="toggleAll($event.target.checked)"
                    >
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-2 font-medium"
                  >
                    Employee
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-2 font-medium"
                  >
                    Approver now
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-2 font-medium"
                  >
                    Becomes
                  </th>
                  <th
                    scope="col"
                    class="px-3 py-2 font-medium"
                  >
                    Waiting requests that move
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-outline-gray-1">
                <tr
                  v-for="row in willChange"
                  :key="row.employee"
                  :data-testid="`approvers-row-${row.employee}`"
                >
                  <td class="px-4 py-2">
                    <input
                      type="checkbox"
                      class="size-5 cursor-pointer"
                      :aria-label="`Fix ${row.employee_name}`"
                      :checked="selected.has(row.employee)"
                      :disabled="applying"
                      @change="toggle(row.employee, $event.target.checked)"
                    >
                  </td>
                  <th
                    scope="row"
                    class="px-3 py-2 text-left font-medium text-ink-gray-9"
                  >
                    {{ row.employee_name }}
                    <span class="block text-xs font-normal text-ink-gray-5">{{ row.employee }}</span>
                    <span
                      v-if="results[row.employee] && !results[row.employee].ok"
                      class="block text-xs font-normal text-red-700"
                      role="alert"
                    >{{ results[row.employee].message }}</span>
                  </th>
                  <td class="break-all px-3 py-2 text-ink-gray-7">
                    {{ currentApprovers(row) }}
                  </td>
                  <td class="px-3 py-2 text-ink-gray-9">
                    {{ becomes(row) }}
                    <span
                      v-if="row.problem"
                      class="block text-xs text-ink-gray-6"
                    >{{ PROBLEMS[row.problem] }}</span>
                  </td>
                  <td class="px-3 py-2 text-ink-gray-7">
                    {{ movingText(row) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Phones. -->
          <div class="sm:hidden">
            <label class="flex min-h-11 items-center gap-2 text-sm text-ink-gray-8">
              <input
                type="checkbox"
                class="size-5"
                :checked="allSelected"
                :disabled="applying"
                @change="toggleAll($event.target.checked)"
              >
              Select everyone listed
            </label>
            <ul class="mt-2 space-y-2">
              <li
                v-for="row in willChange"
                :key="row.employee"
                class="surface-card elev-1 p-3"
              >
                <label class="flex min-h-11 items-start gap-2">
                  <input
                    type="checkbox"
                    class="mt-0.5 size-5"
                    :checked="selected.has(row.employee)"
                    :disabled="applying"
                    @change="toggle(row.employee, $event.target.checked)"
                  >
                  <span class="min-w-0">
                    <span class="block font-medium text-ink-gray-9">{{ row.employee_name }}</span>
                    <span class="block text-xs text-ink-gray-5">{{ row.employee }}</span>
                  </span>
                </label>
                <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt class="text-ink-gray-6">
                    Now
                  </dt>
                  <dd class="break-all text-ink-gray-7">
                    {{ currentApprovers(row) }}
                  </dd>
                  <dt class="text-ink-gray-6">
                    Becomes
                  </dt>
                  <dd class="text-ink-gray-9">
                    {{ becomes(row) }}<template v-if="row.problem">
                      · {{ PROBLEMS[row.problem] }}
                    </template>
                  </dd>
                  <dt class="text-ink-gray-6">
                    Moves
                  </dt>
                  <dd class="text-ink-gray-7">
                    {{ movingText(row) }}
                  </dd>
                </dl>
                <p
                  v-if="results[row.employee] && !results[row.employee].ok"
                  class="mt-1 text-xs text-red-700"
                  role="alert"
                >
                  {{ results[row.employee].message }}
                </p>
              </li>
            </ul>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <template v-if="!confirming">
              <Button
                variant="solid"
                theme="blue"
                :disabled="!selectedCount || applying"
                data-testid="approvers-fix"
                @click="confirming = true"
              >
                Fix {{ selectedCount }} {{ selectedCount === 1 ? 'person' : 'people' }}
              </Button>
            </template>
            <template v-else>
              <p
                class="text-sm text-ink-gray-8"
                role="status"
              >
                Fix {{ selectedCount }} {{ selectedCount === 1 ? 'person' : 'people' }} and move
                {{ movingTotal }} waiting {{ movingTotal === 1 ? 'request' : 'requests' }}?
              </p>
              <Button
                variant="solid"
                theme="blue"
                :loading="applying"
                data-testid="approvers-confirm"
                @click="apply"
              >
                Yes, fix them
              </Button>
              <Button
                variant="ghost"
                :disabled="applying"
                @click="confirming = false"
              >
                Cancel
              </Button>
            </template>
          </div>
        </template>

        <p
          v-if="error"
          class="text-sm text-red-700"
          role="alert"
        >
          {{ error }}
        </p>
        <p
          v-else-if="summary"
          class="text-sm text-ink-gray-6"
          role="status"
          data-testid="approvers-summary"
        >
          {{ summary }}
        </p>
      </section>

      <!-- Needs HR attention -->
      <section
        aria-labelledby="approvers-needs-attention"
        class="space-y-3"
      >
        <h3
          id="approvers-needs-attention"
          class="font-heading text-base font-semibold text-ink-gray-9"
        >
          Needs HR attention ({{ needsAttention.length }})
        </h3>
        <p class="text-sm text-ink-gray-6">
          These people have no usable manager, so nobody approves their leave, expense or shift requests here.
          Only fixing their reporting line in People or Desk helps.
        </p>
        <p
          v-if="!needsAttention.length"
          class="surface-card p-4 text-sm text-ink-gray-6"
        >
          Everyone has a usable manager.
        </p>
        <ul
          v-else
          class="surface-card elev-1 divide-y divide-outline-gray-1"
          data-testid="approvers-needs-attention"
        >
          <li
            v-for="row in needsAttention"
            :key="row.employee"
            class="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2 text-sm"
          >
            <span>
              <span class="font-medium text-ink-gray-9">{{ row.employee_name }}</span>
              <span class="ml-2 text-xs text-ink-gray-5">{{ row.employee }}</span>
            </span>
            <span class="text-ink-gray-7">
              {{ PROBLEMS[row.problem] }}<template v-if="row.manager_name">
                ({{ row.manager_name }})
              </template>
            </span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>
