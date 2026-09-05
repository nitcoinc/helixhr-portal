// The day/week vocabulary three screens share.
//
// The Dashboard spine, the Timesheet spine and the Attendance grid all draw
// the same week. Each used to carry its own copy of these -- character
// identical, and free to drift into three answers to "how tall is a full day"
// or "what do we call a Half Day".

/** A normal working day. Hour bars are read against this, never against the
 * week's own maximum: a week whose biggest day was 2h should look like a thin
 * week, not a full one. */
export const FULL_DAY_HOURS = 8

/** A full week, when the server has not said otherwise. `get_my_week` and
 * `get_my_timesheet_history` both return `full_week_hours`; this is only the
 * fallback for a response that predates it. */
export const FULL_WEEK_HOURS = 40

/** A day bar's height as a percentage, floored so a booked day is always
 * visible and capped so a 12h day does not overflow the track. */
export function barHeight(hours) {
  if (!hours) return 0
  return Math.max(8, Math.min(100, (hours / FULL_DAY_HOURS) * 100))
}

/** Attendance statuses in this product's words. The tint is a second channel
 * and belongs to whichever surface is drawing it -- the week spine sits on the
 * field and needs different dots from the grid on paper -- but the word is the
 * meaning and there is only one of it. */
export const ATTENDANCE_LABEL = {
  Present: 'Present',
  Absent: 'Absent',
  'Half Day': 'Half day',
  'On Leave': 'On leave',
  Holiday: 'Holiday',
}
