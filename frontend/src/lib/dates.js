// Frappe hands back dates as "2026-09-03" and datetimes as
// "2026-09-03 18:47:46.417663". Rendered straight into the page that reads
// as machine output -- the notifications list was showing microseconds --
// and it contradicts the design system's plain-words copy rule. These
// format to the viewer's own locale via Intl, which is also what makes the
// day and month order correct for the India and US offices both.

const DATE = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})
const DATE_NO_YEAR = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' })
const TIME = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

/** Frappe's space-separated datetime is not valid ISO 8601 in Safari, which
 * returns Invalid Date for it. Normalise before parsing. */
function parse(value) {
  if (!value) return null
  const d = value instanceof Date ? value : new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d
}

function isSameDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

/** "3 Sep 2026" — falls back to the raw value rather than showing nothing. */
export function formatDate(value) {
  const d = parse(value)
  return d ? DATE.format(d) : (value ?? '')
}

/** "Today, 18:47" / "Yesterday, 18:47" / "3 Sep, 18:47". Recent items are
 * the ones people scan, and a weekday-relative label reads faster there. */
export function formatDateTime(value) {
  const d = parse(value)
  if (!d) return value ?? ''
  const now = new Date()
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (isSameDay(d, now)) return `Today, ${TIME.format(d)}`
  if (isSameDay(d, yesterday)) return `Yesterday, ${TIME.format(d)}`
  if (d.getFullYear() === now.getFullYear()) return `${DATE_NO_YEAR.format(d)}, ${TIME.format(d)}`
  return `${DATE.format(d)}, ${TIME.format(d)}`
}

/** "18:47" — for a list of check-ins that already sits under a date heading. */
export function formatTime(value) {
  const d = parse(value)
  return d ? TIME.format(d) : (value ?? '')
}

/** "1 Sep – 7 Sep 2026", collapsing the repeated month and year. */
export function formatDateRange(from, to) {
  const a = parse(from)
  const b = parse(to)
  if (!a || !b) return [from, to].filter(Boolean).join(' – ')
  if (isSameDay(a, b)) return DATE.format(a)
  const sameYear = a.getFullYear() === b.getFullYear()
  return `${sameYear ? DATE_NO_YEAR.format(a) : DATE.format(a)} – ${DATE.format(b)}`
}
