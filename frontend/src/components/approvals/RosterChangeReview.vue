<script setup>
// Plan 2026-10-31-001 U10 / R15, R16. A manager's roster request as HR
// decides it, the same block on a phone and in the desktop panel: who sent
// it, for which company, then per person the operations and the affected
// days before and after. `conflicts` is the server's R16 re-check run when
// the detail was read, so HR knows before pressing Approve that it would come
// back. Every word about a rule is the server's.
import { formatDate, formatDateRange } from '@/lib/dates'
import { opLabel, reasonLines } from '@/lib/rosterRequests'

defineProps({
  /** `get_approval_detail` for kind `roster`. */
  selected: { type: Object, required: true },
})

function rangeLabel(row) {
  return row.end_date ? formatDateRange(row.start_date, row.end_date) : `From ${formatDate(row.start_date)}, no end date`
}
</script>

<template>
  <div
    class="space-y-3"
    data-testid="roster-review"
  >
    <dl class="grid grid-cols-2 gap-3 text-sm">
      <div>
        <dt class="label">
          Asked by
        </dt>
        <dd class="mt-0.5 text-ink-gray-9">
          {{ selected.manager_name }}
        </dd>
      </div>
      <div>
        <dt class="label">
          Company
        </dt>
        <dd class="mt-0.5 break-words text-ink-gray-9">
          {{ selected.company }}
        </dd>
      </div>
      <div v-if="selected.submitted_on">
        <dt class="label">
          Sent
        </dt>
        <dd class="mt-0.5 text-ink-gray-9">
          {{ formatDate(selected.submitted_on) }}
        </dd>
      </div>
      <div>
        <dt class="label">
          Changes
        </dt>
        <dd class="tabular mt-0.5 text-ink-gray-9">
          {{ selected.change_count }}
        </dd>
      </div>
    </dl>

    <div
      v-if="selected.conflicts?.length"
      class="surface-alert p-3 text-sm"
      data-testid="roster-conflicts"
    >
      <p class="font-medium">
        Approving will send this back:
      </p>
      <ul class="mt-1 list-disc space-y-0.5 pl-5">
        <li
          v-for="conflict in selected.conflicts"
          :key="conflict.employee"
        >
          {{ conflict.message }}
        </li>
      </ul>
    </div>

    <section
      v-for="person in selected.employees"
      :key="person.employee"
      class="border-t border-outline-gray-2 pt-3"
      data-testid="roster-review-person"
      :data-employee="person.employee_name"
    >
      <h3 class="text-sm font-medium text-ink-gray-9">
        {{ person.employee_name }}
      </h3>
      <ol class="mt-1 list-decimal space-y-0.5 pl-5 text-sm text-ink-gray-7">
        <li
          v-for="op in person.operations"
          :key="op.index"
        >
          {{ opLabel(op) }}
        </li>
      </ol>
      <!-- Before and after only while it is waiting: once decided, the
           roster has moved on and the server sends neither. -->
      <div
        v-if="person.before || person.after"
        class="mt-2 grid gap-2 text-sm sm:grid-cols-2"
      >
        <div class="surface-inset p-2">
          <p class="label">
            Now
          </p>
          <ul class="mt-1 space-y-0.5">
            <li
              v-for="row in person.before || []"
              :key="`${row.shift_type}|${row.start_date}`"
              class="text-ink-gray-7"
            >
              {{ row.shift_type }} · {{ rangeLabel(row) }}
            </li>
            <li
              v-if="!person.before?.length"
              class="text-ink-gray-5"
            >
              No shift on these days
            </li>
          </ul>
        </div>
        <div class="surface-inset p-2">
          <p class="label">
            After approval
          </p>
          <ul class="mt-1 space-y-0.5">
            <li
              v-for="row in person.after || []"
              :key="`${row.shift_type}|${row.start_date}`"
              :class="row.new ? 'font-medium text-ink-gray-9' : 'text-ink-gray-7'"
            >
              {{ row.shift_type }} · {{ rangeLabel(row) }}
              <span
                v-if="row.new"
                class="ml-1 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
              >New</span>
            </li>
            <li
              v-if="!person.after?.length"
              class="text-ink-gray-5"
            >
              No shift on these days
            </li>
          </ul>
        </div>
      </div>
    </section>

    <div
      v-if="selected.return_reasons || selected.decision_note"
      class="surface-inset p-3 text-sm text-ink-gray-7"
    >
      <p class="font-medium text-ink-gray-9">
        Decision
      </p>
      <p
        v-for="line in reasonLines(selected.return_reasons || selected.decision_note)"
        :key="line"
      >
        {{ line }}
      </p>
    </div>
  </div>
</template>
