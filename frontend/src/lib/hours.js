// Timesheet hours, quantised the one way this feature counts them.
//
// Entry is in quarter-hour steps (`WeekGrid`'s stepper) but the field also
// takes anything an employee types, so 7.1 and 4.25 are both ordinary
// values. Two rules follow, and the grid used to break both:
//
//   * A quantised figure renders **as it is**. `toFixed(0)` turned a 7.1
//     day into "7" and a 7.9 day into "8" while the row and week totals
//     beside it still read 23.0 -- three cells disagreeing about the same
//     week. `toFixed(1)` is the same defect one step along: it reports a
//     4.25 day as "4.3".
//   * A figure that was **summed** needs re-quantising before it is shown,
//     because adding floats drifts (0.1 + 0.2 is not 0.3). Only the totals
//     that sum need this; a value that arrived quantised is already right.
//
// A module rather than an inline expression for the reason `lib/money.js`
// gives: two components render these same figures, getting it wrong is a
// visible defect, and a pure function is testable where a template
// expression is not.
export function roundHours(value) {
  const hours = Number(value)
  if (!Number.isFinite(hours)) return 0
  return Math.round(hours * 100) / 100
}
