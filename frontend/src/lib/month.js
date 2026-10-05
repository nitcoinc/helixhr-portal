// Plan 2026-10-05-001 U5. The month overview's calendar arithmetic, kept as
// pure functions so the server's "every Monday overlapping the month" rule
// (`helixhr.api.get_my_month`) has a client twin a unit test can hold.

import { addCalendarDays, mondayOf } from '@/lib/dates'
import { TONE, resolveStatus } from '@/lib/statusBadge'

const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/

/** `"2026-10"` is a month; anything else is not. Same rule as the server. */
export function isMonth(value) {
  return typeof value === 'string' && MONTH.test(value)
}

/** The month (`YYYY-MM`) a calendar date falls in. */
export function monthOf(isoDate) {
  return String(isoDate).slice(0, 7)
}

/** `"2026-12"` + 1 -> `"2027-01"`. */
export function addMonths(month, delta) {
  const [, y, m] = MONTH.exec(month)
  const index = Number(y) * 12 + Number(m) - 1 + delta
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`
}

/** Every Monday whose Monday..Sunday week overlaps the month, in order. */
export function mondaysOfMonth(month) {
  const first = `${month}-01`
  const next = `${addMonths(month, 1)}-01`
  const mondays = []
  for (let monday = mondayOf(first); monday < next; monday = addCalendarDays(monday, 7)) {
    mondays.push(monday)
  }
  return mondays
}

/** "October 2026" -- the month control's heading. */
export function formatMonth(month) {
  const [, y, m] = MONTH.exec(month)
  return new Date(Date.UTC(Number(y), Number(m) - 1, 1)).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

/** The word and tone pair for one week card. "Not started" and "Change
 * requested" are the two states a week has that its Timesheet's
 * workflow_state cannot say on its own; every other word is the badge's. */
export function weekCardStatus(week) {
  if (week.change_open) return { label: 'Change requested', toneClass: TONE.waiting }
  if (!week.state) return { label: 'Not started', toneClass: TONE.resting }
  return resolveStatus({ kind: 'timesheet', status: week.state })
}
