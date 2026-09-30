// Plan 2026-09-30-001 U9 / U10. The Roster page's pure half: which modes
// to offer, and how one cell reads. Nothing here decides who is on the grid
// or what a shift is -- `helixhr.api.get_roster_week` does both, and refuses
// a mode the caller does not hold whatever this offers.

const MODE_LABELS = { mine: 'Mine', team: 'My team', hr: 'Everyone' }

/** The modes this session holds, in order, from the bootstrap flags the nav
 * already gates on: `hasReports` for Team, `canSeePeople` (the
 * `resolve_admin_scope` mirror) for HR. A desk-only session has no Employee,
 * so it has no "mine" and no "team" either. */
export function rosterModes(session) {
  const deskOnly = session?.status === 'desk-only'
  const modes = []
  if (!deskOnly) modes.push('mine')
  if (!deskOnly && session?.hasReports) modes.push('team')
  if (session?.canSeePeople) modes.push('hr')
  return modes.map((value) => ({ value, label: MODE_LABELS[value] }))
}

/** "09:00–17:00", or '' when either end is missing. */
export function shiftHours(shift) {
  if (!shift?.start_time || !shift?.end_time) return ''
  return `${shift.start_time}–${shift.end_time}`
}

/** The leave covering `date` on this row, as a short label, or ''. Same
 * words as Team's bars: the type, `half day`, `waiting`. */
export function leaveLabel(row, date) {
  const leave = (row?.leaves || []).find((item) => item.start <= date && item.end >= date)
  if (!leave) return ''
  const parts = [leave.leave_type]
  if (leave.half_day) parts.push('half day')
  if (leave.waiting) parts.push('waiting')
  return parts.join(' · ')
}

/** True when no row has a shift on any day of the week. The grid still
 * renders -- the week is a real answer -- but it says so in a sentence. */
export function isEmptyWeek(rows) {
  return !(rows || []).some((row) => row.cells.some((cell) => cell.shift_type))
}
