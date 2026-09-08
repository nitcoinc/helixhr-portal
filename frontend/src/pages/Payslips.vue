<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createResource, Button, Dialog } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import PayslipBreakdown from '@/components/PayslipBreakdown.vue'
import Icon from '@/components/Icon.vue'
import { formatDateRange, isCalendarDate } from '@/lib/dates'
import { formatMoney } from '@/lib/money'
import { useIsDesktop } from '@/lib/useIsDesktop'

// P3-U2 / P3-R1 to P3-R4. `/payslips` and `/payslips/:name` are one
// component: the open slip is a route parameter, so refresh and browser Back
// land on the same payslip, and the phone sheet and the desktop panel are two
// shapes of one state rather than two screens (the P2-KTD5 rule).
const props = defineProps({
  name: { type: String, default: '' },
})

const router = useRouter()
const isDesktop = useIsDesktop()

const pageLimit = ref(20)
// null means "every year", which is also the first thing the page shows.
const year = ref(null)

const payslips = createResource({
  url: 'helixhr.api.get_my_payslips',
  makeParams: () => ({ limit: pageLimit.value, year: year.value || undefined }),
  auto: true,
})

const rows = computed(() => payslips.data?.payslips || [])
const total = computed(() => payslips.data?.total || 0)
const years = computed(() => payslips.data?.years || [])
const latest = computed(() => rows.value[0] || null)

const moreCount = computed(() => Math.max(0, total.value - rows.value.length))
function showMore() {
  pageLimit.value = Math.min(200, pageLimit.value + 20)
  payslips.reload()
}

function filterByYear(value) {
  year.value = value
  pageLimit.value = 20
  payslips.reload()
}

// The tile the canvas puts on every row: 56px, the year over the month
// (`.date-tile` in index.css). Parsed straight off the date-only string
// rather than through a Date object -- `new Date('2026-09-30')` is midnight
// UTC and renders as August west of Greenwich, which is the class of bug
// P2-R5 exists to prevent.
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
function tile(row) {
  if (!isCalendarDate(row.end_date)) return null
  const [tileYear, month] = row.end_date.split('-')
  return { year: tileYear, month: MONTHS[Number(month) - 1] }
}

/** One run of rows per year, newest first -- the server already orders by
 * period, so this only inserts the headings (P3-R1). */
const groups = computed(() => {
  const byYear = new Map()
  for (const row of rows.value) {
    const key = isCalendarDate(row.end_date) ? row.end_date.slice(0, 4) : 'Earlier'
    if (!byYear.has(key)) byYear.set(key, [])
    byYear.get(key).push(row)
  }
  return [...byYear.entries()].map(([label, group]) => ({ label, rows: group }))
})

/** Every amount is formatted in the row's own currency, and no two rows'
 * amounts are ever added together: an employee who moved payrolls has both
 * a USD and an INR slip in this list and the two are not comparable
 * (P3-U2 step 5). */
function money(row, amount, options) {
  return formatMoney(amount, row.currency, options)
}

/** The PDF endpoint, as a URL. `download_my_payslip` is a GET so this can be
 * a plain link: the browser saves the file itself, which is what a phone
 * needs, and a `fetch` into a blob would not (P3-KTD2). '' for a withheld
 * slip, which has no PDF at all. */
function pdfHref(row) {
  if (!row?.can_download) return ''
  return `/api/method/helixhr.api.download_my_payslip?name=${encodeURIComponent(row.name)}`
}

// --- the open payslip ---------------------------------------------------

// The list is bounded (P3-R25), so `/payslips/<name>` has to be answerable on
// its own: a slip reached from a bookmark is not necessarily on the page the
// list returned.
const detail = createResource({
  url: 'helixhr.api.get_my_payslip',
  makeParams: () => ({ name: props.name }),
})

watch(
  () => props.name,
  (name) => {
    if (name) detail.fetch()
  },
  { immediate: true },
)

const selected = computed(() => (props.name ? detail.data : null))

function closeDetail() {
  router.push({ name: 'Payslips' })
}

// The phone shape is frappe-ui's `Dialog`, which index.css gives the bottom
// sheet treatment: reka-ui underneath it is what supplies the focus trap,
// Escape and focus restoration (P2-R6).
const sheetOpen = computed(() => !!props.name && !isDesktop.value)
</script>

<template>
  <div>
    <PageHeader title="Payslips" />

    <!-- The anchored region: what the last payroll run paid, on the field,
         with the PDF beside it. The one place on this page signal yellow is
         legal (P3-R24). -->
    <AsyncState
      section="payslip-latest"
      class="mb-6"
      :resource="payslips"
      :empty="!latest"
      empty-title="No payslips yet"
      empty-body="Your payslips appear here after the first payroll run. Ask HR if you think one is missing."
      skeleton="field"
      skeleton-height="h-36"
    >
      <section
        v-if="latest"
        class="surface-field elev-2 p-4 sm:flex sm:items-end sm:justify-between sm:gap-4"
        aria-label="Latest payslip"
      >
        <div class="min-w-0">
          <p class="label !text-blue-200">
            Latest payslip
          </p>
          <p class="tabular font-heading text-3xl font-bold text-white">
            {{ money(latest, latest.net_pay, { decimals: false }) }}
          </p>
          <p class="mt-0.5 text-sm text-blue-100">
            {{ formatDateRange(latest.start_date, latest.end_date) }}
            <span v-if="latest.withheld"> · withheld, ask HR</span>
          </p>
        </div>
        <a
          v-if="pdfHref(latest)"
          class="mt-3 flex min-h-11 items-center justify-center rounded-full bg-signal px-5 text-sm font-bold text-field hover:brightness-95 sm:mt-0"
          :href="pdfHref(latest)"
        >
          Download PDF
        </a>
      </section>
    </AsyncState>

    <!-- Year filter. Only drawn when there is more than one year to choose
         between; a single-year list needs no control. -->
    <div
      v-if="years.length > 1"
      class="mb-4 flex flex-wrap items-center gap-2"
      role="group"
      aria-label="Filter payslips by year"
    >
      <button
        type="button"
        class="min-h-11 rounded-full border px-4 text-sm font-medium"
        :class="
          year === null
            ? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
            : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2'
        "
        :aria-pressed="year === null"
        @click="filterByYear(null)"
      >
        All years
      </button>
      <button
        v-for="option in years"
        :key="option"
        type="button"
        class="tabular min-h-11 rounded-full border px-4 text-sm font-medium"
        :class="
          year === option
            ? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
            : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-2'
        "
        :aria-pressed="year === option"
        @click="filterByYear(option)"
      >
        {{ option }}
      </button>
    </div>

    <div class="lg:flex lg:items-start lg:gap-6">
      <!-- The list. At lg: it keeps its place while the panel beside it
           changes; on a phone the open slip is a sheet over it. -->
      <div class="min-w-0 lg:flex-1">
        <AsyncState
          section="payslip-list"
          :resource="payslips"
          :empty="rows.length === 0"
          :empty-title="year ? `No payslips in ${year}` : 'No payslips yet'"
          empty-body="Payslips appear here after each payroll run."
          :skeleton-rows="3"
        >
          <div
            v-for="group in groups"
            :key="group.label"
            class="mb-6 last:mb-0"
          >
            <h2 class="label tabular mb-2">
              {{ group.label }}
            </h2>
            <ul class="space-y-2">
              <li
                v-for="row in group.rows"
                :key="row.name"
                class="surface-card elev-1 relative flex gap-3 p-3"
                :class="row.name === name ? 'ring-2 ring-field' : ''"
              >
                <span
                  v-if="tile(row)"
                  class="date-tile mt-0.5 self-start"
                  aria-hidden="true"
                >
                  <span class="date-tile-month">{{ tile(row).year }}</span>
                  <span class="date-tile-day">{{ tile(row).month }}</span>
                </span>

                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-start justify-between gap-2">
                    <!-- One link per row, stretched over the whole card: two
                         nested interactive elements would be neither valid
                         markup nor navigable. The negative margin keeps a
                         real 44px target box without loosening the row. -->
                    <router-link
                      class="-my-2 inline-flex min-h-11 items-center font-medium text-ink-gray-9 after:absolute after:inset-0 after:content-['']"
                      :to="{ name: 'PayslipDetail', params: { name: row.name } }"
                    >
                      {{ formatDateRange(row.start_date, row.end_date) }}
                    </router-link>
                    <span class="flex flex-wrap items-center gap-1.5">
                      <span
                        v-if="row.withheld"
                        class="rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3"
                      >Withheld, ask HR</span>
                      <span
                        v-if="row.revised"
                        class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7"
                      >Revised</span>
                    </span>
                  </div>

                  <p class="mt-0.5 text-sm text-ink-gray-6">
                    Net
                    <span class="tabular font-medium text-ink-gray-9">
                      {{ money(row, row.net_pay) }}
                    </span>
                  </p>
                  <p class="mt-0.5 text-sm text-ink-gray-5">
                    Gross <span class="tabular">{{ money(row, row.gross_pay) }}</span>
                    · Deductions
                    <span class="tabular">{{ money(row, row.total_deduction) }}</span>
                  </p>
                </div>

                <Icon
                  name="chevronRight"
                  class="mt-1 shrink-0 self-start text-ink-gray-4"
                />
              </li>
            </ul>
          </div>

          <div
            v-if="moreCount"
            class="mt-4 text-center"
          >
            <Button
              variant="ghost"
              :loading="payslips.loading"
              @click="showMore"
            >
              Show {{ moreCount }} more
            </Button>
          </div>
        </AsyncState>
      </div>

      <!-- The open payslip, at lg: a column beside the list. Same URL as the
           phone sheet, so refresh and Back agree either way. -->
      <aside
        v-if="name && isDesktop"
        class="min-w-0 lg:w-96 lg:shrink-0"
      >
        <AsyncState
          section="payslip-detail"
          :resource="detail"
          :empty="!detail.data"
          empty-title="That payslip isn't here"
          empty-body="Ask HR if you were expecting it."
          skeleton="block"
          skeleton-height="h-80"
        >
          <template #error-title>
            We couldn't load this payslip
          </template>

          <article
            v-if="selected"
            class="surface-card elev-1 p-4"
            aria-label="Payslip"
          >
            <PayslipBreakdown
              :payslip="selected"
              :pdf-href="pdfHref(selected)"
            />
            <Button
              class="mt-3"
              variant="ghost"
              @click="closeDetail"
            >
              Back to payslips
            </Button>
          </article>
        </AsyncState>
      </aside>
    </div>

    <!-- The phone shape of the very same state. -->
    <Dialog
      :model-value="sheetOpen"
      :options="{ title: 'Payslip', size: 'sm' }"
      @update:model-value="(open) => !open && closeDetail()"
    >
      <template #body-content>
        <AsyncState
          section="payslip-sheet"
          :resource="detail"
          :empty="!detail.data"
          empty-title="That payslip isn't here"
          empty-body="Ask HR if you were expecting it."
          skeleton="block"
          skeleton-height="h-80"
        >
          <template #error-title>
            We couldn't load this payslip
          </template>
          <PayslipBreakdown
            v-if="selected"
            :payslip="selected"
            :pdf-href="pdfHref(selected)"
          />
        </AsyncState>
      </template>
    </Dialog>
  </div>
</template>
