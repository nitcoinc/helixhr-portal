// Plan 2026-10-31-001 U7 / KTD9. The browser half of the roster request
// simulator, for the draft preview overlay. It mirrors `simulate` in
// `helixhr/roster_ops.py` rule for rule and sentence for sentence; both
// test suites carry the same literal case table. The server re-runs its own
// copy at save, submit and approval, so this only ever previews.
//
// Dates are ISO `YYYY-MM-DD` strings, which compare correctly as strings.
// Coverage is `{ employee: [{ key, shift_type, start_date, end_date }] }`
// (`end_date` null = open-ended); operations are roster change rows.

export const ASSIGN = 'Assign'
export const CHANGE = 'Change'
export const END = 'End'
const ACTIONS = [ASSIGN, CHANGE, END]

export const OVERLAP =
  'This person already has a shift on some of those dates. End or change that one first.'
export const END_BEFORE_START = "The end date can't be before the shift starts."
export const CHANGE_ON_START =
  'That is the day this shift starts. Cancel it and assign the new shift instead.'
export const AFTER_END = 'That date is after this shift ends.'
export const NO_SHIFT_TYPE = 'Choose a shift.'
export const BAD_DATE = 'Give a valid date.'
export const NO_SHIFT_ON_DATE = 'This person has no shift on that date to change or end.'
export const BAD_ACTION = 'Choose assign, change or end.'

/** `value` when it is a real ISO calendar date, otherwise null. */
function isoDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
    ? value
    : null
}

function dayBefore(iso) {
  const parsed = new Date(`${iso}T00:00:00Z`)
  parsed.setUTCDate(parsed.getUTCDate() - 1)
  return parsed.toISOString().slice(0, 10)
}

/** The date an operation acts on: `to_date` for End, `from_date` otherwise. */
export function opDate(op) {
  return isoDate(op.action === END ? op.to_date : op.from_date)
}

function overlaps(assignment, start, end) {
  return (
    (end === null || assignment.start_date <= end) &&
    (assignment.end_date === null || assignment.end_date >= start)
  )
}

// The assignment `key` names while it is still in `rows`, else the first one
// covering `day` -- a key HR's edit has since replaced falls back, as the
// server's apply does (`roster_ops._target`).
function findTarget(rows, key, day) {
  const named = key ? rows.find((a) => a.key === key) : null
  return named ?? rows.find((a) => overlaps(a, day, day)) ?? null
}

function apply(rows, op, index) {
  if (!ACTIONS.includes(op.action)) return BAD_ACTION
  if (op.action !== END && !op.shift_type) return NO_SHIFT_TYPE
  const day = opDate(op)
  if (!day) return BAD_DATE

  if (op.action === ASSIGN) {
    const end = op.to_date ? isoDate(op.to_date) : null
    if (op.to_date && !end) return BAD_DATE
    if (end && end < day) return END_BEFORE_START
    if (rows.some((a) => overlaps(a, day, end))) return OVERLAP
    rows.push({ key: `new-${index}`, shift_type: op.shift_type, start_date: day, end_date: end })
    return null
  }

  const target = findTarget(rows, op.assignment, day)
  if (!target) return NO_SHIFT_ON_DATE
  if (op.action === CHANGE) {
    if (day <= target.start_date) return CHANGE_ON_START
    if (target.end_date && day > target.end_date) return AFTER_END
    const oldEnd = target.end_date
    target.end_date = dayBefore(day)
    rows.push({ key: `new-${index}`, shift_type: op.shift_type, start_date: day, end_date: oldEnd })
    return null
  }
  if (day < target.start_date) return END_BEFORE_START
  if (target.end_date && day > target.end_date) return AFTER_END
  target.end_date = day
  return null
}

/** Apply `ops` in order to a copy of `coverage`. Returns `{ coverage,
 * problems }`, each problem `{ index, employee, message }`. A refused
 * operation changes nothing and the rest still run. */
export function simulate(coverage, ops) {
  const result = {}
  for (const [employee, rows] of Object.entries(coverage ?? {})) {
    result[employee] = rows.map((a) => ({
      key: a.key,
      shift_type: a.shift_type,
      start_date: a.start_date,
      end_date: a.end_date ?? null,
    }))
  }
  const problems = []
  ops.forEach((op, index) => {
    result[op.employee] ??= []
    const message = apply(result[op.employee], op, index)
    if (message) problems.push({ index, employee: op.employee, message })
  })
  return { coverage: result, problems }
}
