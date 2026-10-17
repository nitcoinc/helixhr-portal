import { calendarDateOf, mondayOf, addCalendarDays } from './dates'

// Plan 2026-10-17-001 U10 (ED-04 / EP-07). The Notifications list, cut two
// ways: every log about one record collapses into one row (the newest, plus
// "+N earlier"), and the rows sit under Today / Yesterday / This week / Older.
//
// Record grouping is worth its weight because one record really does write
// several logs: an HR request conversation writes one per reply and one per
// status change, and a leave or timesheet writes one per transition, so a
// send-back loop is three or four logs about one thing. Logs with no record
// (a plain alert) stay one row each.
//
// Pure: the caller passes `today` (`lib/dates.today()`), so the boundaries
// are the user's calendar. `dayOf` is the seam a test pins.

const BUCKETS = [
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: 'week', label: 'This week' },
  { key: 'older', label: 'Older' },
]

/** Which bucket a `YYYY-MM-DD` falls in. "This week" is the portal's one
 * week, Monday..Sunday (KTD10), so on a Monday it is empty -- the label says
 * what it means. A day the server dated in the future reads as Today. */
export function bucketOf(date, today) {
  if (!date) return 'older'
  if (date >= today) return 'today'
  if (date === addCalendarDays(today, -1)) return 'yesterday'
  if (date >= mondayOf(today)) return 'week'
  return 'older'
}

function recordKey(row) {
  return row.document_type && row.document_name
    ? `${row.document_type}\u0000${row.document_name}`
    : `log\u0000${row.name}`
}

/**
 * `rows`, newest first as the server sends them -> one group per record,
 * placed where its newest log is: `{ key, latest, earlier, unread }`.
 * `unread` counts the group's unread logs, which is what opening it clears.
 */
export function groupByRecord(rows) {
  const byKey = new Map()
  for (const row of rows || []) {
    const key = recordKey(row)
    let group = byKey.get(key)
    if (!group) {
      group = { key, latest: row, earlier: [], unread: 0 }
      byKey.set(key, group)
    } else {
      group.earlier.push(row)
    }
    if (!row.read) group.unread += 1
  }
  return [...byKey.values()]
}

/** The record groups under their day buckets, empty buckets dropped:
 * `[{ key, label, groups }]` in BUCKETS order. */
export function bucketGroups(rows, today, dayOf = (row) => calendarDateOf(row.creation)) {
  const buckets = BUCKETS.map((bucket) => ({ ...bucket, groups: [] }))
  const index = Object.fromEntries(BUCKETS.map((bucket, i) => [bucket.key, i]))
  for (const group of groupByRecord(rows)) {
    buckets[index[bucketOf(dayOf(group.latest), today)]].groups.push(group)
  }
  return buckets.filter((bucket) => bucket.groups.length)
}
