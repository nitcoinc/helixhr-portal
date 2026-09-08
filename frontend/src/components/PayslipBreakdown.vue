<script setup>
import { computed } from 'vue'
import { formatDate, formatDateRange } from '@/lib/dates'
import { formatMoney } from '@/lib/money'

// P3-U2 step 4 / P3-R2. One payslip's breakdown, written once and shaped
// twice: it is the body of the phone sheet and the body of the desktop
// panel, so the two can never drift into two readings of the same slip.
const props = defineProps({
  /** The `helixhr.api.get_my_payslip` payload. */
  payslip: { type: Object, required: true },
  /** Where this slip's PDF lives, or '' when it has none (P3-R4). */
  pdfHref: { type: String, default: '' },
})

/** Every figure on this card is in the slip's own currency, and none of
 * them is ever added to a figure from another slip (P3-U2 step 5). */
function money(amount) {
  return formatMoney(amount, props.payslip.currency)
}

/** "22 of 30 days" -- both numbers, because either alone is a figure an
 * employee cannot check. */
const daysPaid = computed(() => {
  const { payment_days: paid, total_working_days: total } = props.payslip
  if (!total) return ''
  return `${paid} of ${total} days`
})
</script>

<template>
  <div>
    <div class="flex flex-wrap items-start justify-between gap-2">
      <div class="min-w-0">
        <h2 class="font-heading text-lg font-bold text-ink-gray-9">
          {{ formatDateRange(payslip.start_date, payslip.end_date) }}
        </h2>
        <p
          v-if="payslip.posting_date"
          class="mt-0.5 text-sm text-ink-gray-6"
        >
          Paid on {{ formatDate(payslip.posting_date) }}
        </p>
      </div>
      <span
        v-if="payslip.withheld"
        class="rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
      >Withheld</span>
      <span
        v-else-if="payslip.revised"
        class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
      >Revised</span>
    </div>

    <dl class="mt-4 grid grid-cols-2 gap-4">
      <div v-if="daysPaid">
        <dt class="label">
          Days paid
        </dt>
        <dd class="tabular mt-0.5 text-sm text-ink-gray-9">
          {{ daysPaid }}
        </dd>
      </div>
      <div>
        <dt class="label">
          Unpaid leave
        </dt>
        <dd class="tabular mt-0.5 text-sm text-ink-gray-9">
          {{ payslip.leave_without_pay }}
          {{ payslip.leave_without_pay === 1 ? 'day' : 'days' }}
        </dd>
      </div>
    </dl>

    <section
      v-if="payslip.earnings?.length"
      class="mt-4"
      aria-label="Earnings"
    >
      <h3 class="label">
        Earnings
      </h3>
      <ul class="mt-1 divide-y divide-outline-gray-1">
        <li
          v-for="row in payslip.earnings"
          :key="`earning-${row.salary_component}`"
          class="flex items-baseline justify-between gap-3 py-1.5 text-sm"
        >
          <span class="min-w-0 text-ink-gray-7">{{ row.salary_component }}</span>
          <span class="tabular shrink-0 text-ink-gray-9">{{ money(row.amount) }}</span>
        </li>
      </ul>
      <p class="mt-1.5 flex items-baseline justify-between gap-3 text-sm font-medium">
        <span class="text-ink-gray-8">Gross pay</span>
        <span class="tabular text-ink-gray-9">{{ money(payslip.gross_pay) }}</span>
      </p>
    </section>

    <section
      v-if="payslip.deductions?.length"
      class="mt-4"
      aria-label="Deductions"
    >
      <h3 class="label">
        Deductions
      </h3>
      <ul class="mt-1 divide-y divide-outline-gray-1">
        <li
          v-for="row in payslip.deductions"
          :key="`deduction-${row.salary_component}`"
          class="flex items-baseline justify-between gap-3 py-1.5 text-sm"
        >
          <span class="min-w-0 text-ink-gray-7">{{ row.salary_component }}</span>
          <span class="tabular shrink-0 text-ink-gray-9">−{{ money(row.amount) }}</span>
        </li>
      </ul>
      <p class="mt-1.5 flex items-baseline justify-between gap-3 text-sm font-medium">
        <span class="text-ink-gray-8">Total deductions</span>
        <span class="tabular text-ink-gray-9">−{{ money(payslip.total_deduction) }}</span>
      </p>
    </section>

    <p
      class="mt-4 flex items-baseline justify-between gap-3 border-t border-outline-gray-1 pt-3"
    >
      <span class="font-medium text-ink-gray-9">Net pay</span>
      <span class="tabular font-heading text-xl font-bold text-ink-gray-9">
        {{ money(payslip.net_pay) }}
      </span>
    </p>

    <div class="mt-4">
      <!-- P3-KTD2. A real navigation to a GET endpoint, not a fetch and a
           blob: that is what lets a phone browser save or open the file
           itself, and what makes the download survive the tab going away. -->
      <a
        v-if="pdfHref"
        class="flex min-h-11 w-full items-center justify-center rounded-md border border-outline-gray-2 px-4 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
        :href="pdfHref"
      >
        Download PDF
      </a>
      <p
        v-else
        class="surface-alert p-3 text-sm"
      >
        This payslip is on hold, so there's no PDF for it yet. Ask HR about it.
      </p>
    </div>
  </div>
</template>
