// Plan 2026-10-04-001 U3: date-range presets for the report filter bar.
// Computed on calendar strings (YYYY-MM-DD) against the user's own "today"
// (`lib/dates.today()`, the bootstrap's calendar), never a Date in the
// browser's zone -- the same rule every other date on screen follows.

export const DATE_PRESETS = [
  { id: 'this_month', label: 'This month' },
  { id: 'last_month', label: 'Last month' },
  { id: 'this_quarter', label: 'This quarter' },
  { id: 'last_quarter', label: 'Last quarter' },
  { id: 'this_year', label: 'This year' },
  { id: 'last_year', label: 'Last year' },
]

const pad = (n) => String(n).padStart(2, '0')

function lastDay(year, month) {
  // month is 1-12; day 0 of the next month is this month's last day.
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

function span(year, firstMonth, lastMonth) {
  return {
    from_date: `${year}-${pad(firstMonth)}-01`,
    to_date: `${year}-${pad(lastMonth)}-${pad(lastDay(year, lastMonth))}`,
  }
}

/** `{from_date, to_date}` for preset `id` relative to `today`
 * (YYYY-MM-DD), or null for an unknown id. */
export function presetRange(id, today) {
  const match = /^(\d{4})-(\d{2})-\d{2}$/.exec(today || '')
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const quarterStart = month - ((month - 1) % 3)
  switch (id) {
    case 'this_month':
      return span(year, month, month)
    case 'last_month':
      return month === 1 ? span(year - 1, 12, 12) : span(year, month - 1, month - 1)
    case 'this_quarter':
      return span(year, quarterStart, quarterStart + 2)
    case 'last_quarter':
      return quarterStart === 1 ? span(year - 1, 10, 12) : span(year, quarterStart - 3, quarterStart - 1)
    case 'this_year':
      return span(year, 1, 12)
    case 'last_year':
      return span(year - 1, 1, 12)
    default:
      return null
  }
}

/** The preset id `from`..`to` is exactly, or 'custom'. */
export function matchPreset(from, to, today) {
  const found = DATE_PRESETS.find(({ id }) => {
    const range = presetRange(id, today)
    return range && range.from_date === from && range.to_date === to
  })
  return found ? found.id : 'custom'
}
