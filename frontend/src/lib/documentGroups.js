import { addCalendarDays, formatDate } from './dates'

// Documents page: the buckets one tab's list is cut into, newest first.
// Within the last 30 days a document sits under its day ("Today",
// "Yesterday", "28 Sep 2026"); earlier this year under its month
// ("September 2026"); before this year under its year ("2025"). Pure: the
// caller passes `today` (`lib/dates.today()`), so the boundaries are the
// user's calendar, not the browser's clock.

const RECENT_DAYS = 30

const monthLabel = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })

function bucketOf(date, today) {
  if (date === today) return { key: date, label: 'Today' }
  if (date === addCalendarDays(today, -1)) return { key: date, label: 'Yesterday' }
  if (date > addCalendarDays(today, -RECENT_DAYS)) return { key: date, label: formatDate(date) }
  const [year, month] = date.split('-')
  if (year === today.slice(0, 4)) {
    return { key: `${year}-${month}`, label: monthLabel.format(new Date(Date.UTC(+year, +month - 1, 1))) }
  }
  return { key: year, label: year }
}

/**
 * `rows` (each with `published_on` as `YYYY-MM-DD`) -> `[{ key, label, rows }]`,
 * groups and rows both newest first. A row with no date sorts last, under
 * "Undated", rather than disappearing.
 */
export function groupDocuments(rows, today) {
  const sorted = [...(rows || [])].sort((a, b) =>
    String(b.published_on || '').localeCompare(String(a.published_on || '')),
  )
  const groups = []
  for (const row of sorted) {
    const date = String(row.published_on || '').slice(0, 10)
    const bucket = date ? bucketOf(date, today) : { key: 'undated', label: 'Undated' }
    const last = groups.at(-1)
    if (last?.key === bucket.key) last.rows.push(row)
    else groups.push({ ...bucket, rows: [row] })
  }
  return groups
}
