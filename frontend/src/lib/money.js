// P3-U2 / P3-R1. Money, formatted in the currency of the row it came from.
//
// A payslip is the first screen in this portal that renders money at all,
// and it renders it in more than one currency: an employee moved between a
// USD and an INR payroll has both in one list, and the two figures are not
// comparable. So the currency is never a page-level setting and the amounts
// are never summed -- every call site passes the row's own `currency`, and
// this module has no default for it.
//
// `Intl.NumberFormat` with `style: 'currency'` is the whole implementation.
// It is a module rather than an inline template expression (which is what
// the rest of the portal does for its handful of plain integers) because
// three components render the same figures -- the field block, the list row
// and the breakdown -- a wrong or missing currency is a payroll-grade
// defect, and a pure function is testable by vitest where a template
// expression is not.
//
// The locale is the browser's own unless a caller names one, the way
// `lib/dates.js` leaves day/month order to it; the tests pin one.

const formatters = new Map()

function formatter(locale, currency, digits) {
  const key = `${locale || ''}|${currency}|${digits}`
  let existing = formatters.get(key)
  if (!existing) {
    existing = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
    formatters.set(key, existing)
  }
  return existing
}

/**
 * `amount` in `currency`, as one string carrying that currency's own symbol.
 *
 * Returns an empty string when either input is missing, so a row that
 * arrived without a currency renders nothing rather than a number nobody
 * can read -- an unlabelled "4,500" on a payslip is worse than a blank.
 *
 * @param {number|string} amount
 * @param {string} currency ISO 4217 code, from the slip's own `currency`.
 * @param {{ decimals?: boolean, locale?: string }} [options] `decimals: false`
 *   drops the minor unit, for the one big figure in the field block.
 */
export function formatMoney(amount, currency, { decimals = true, locale } = {}) {
  if (amount === null || amount === undefined || amount === '' || !currency) return ''
  const value = Number(amount)
  if (!Number.isFinite(value)) return ''
  try {
    return formatter(locale, currency, decimals ? 2 : 0).format(value)
  } catch {
    // An unknown or malformed currency code makes Intl throw. The number is
    // still true, and the code beside it is still the honest label.
    return `${value.toLocaleString(locale)} ${currency}`
  }
}
