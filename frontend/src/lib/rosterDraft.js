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
// Plan 2026-11-05-001 KTD4: one-day operations, built from the same steps.
export const SWAP_DAY = 'Swap Day'
export const CLEAR_DAY = 'Clear Day'
const ACTIONS = [ASSIGN, CHANGE, END, SWAP_DAY, CLEAR_DAY]

export const OVERLAP =
  'This person already has a shift on some of those dates. End or change that one first.'
export const END_BEFORE_START = "The end date can't be before the shift starts."
export const CHANGE_ON_START =
  'That is the day this shift starts. Cancel it and assign the new shift instead.'
export const AFTER_END = 'That date is after this shift ends.'
export const NO_SHIFT_TYPE = 'Choose a shift.'
export const BAD_DATE = 'Give a valid date.'
export const NO_SHIFT_ON_DATE = 'This person has no shift on that date to change or end.'
export const BAD_ACTION = 'Choose assign, change, end, swap day or clear day.'
// Plan 2026-11-05-001 A2: a Swap Day to the shift the day already has.
export const SAME_SHIFT = 'They already have that shift on that day.'
// Plan 2026-11-03-001 U1: skipping a person's weekly offs and holidays.
export const SKIP_MAX_DAYS = 92
export const NO_WORKING_DAYS = 'No working days in that range.'
export const SKIP_SPAN = 'Skipping weekly offs and holidays works on at most 92 days at a time.'
// Plan 2026-11-04-001 U2 / KTD5: non-blocking notes on a run-aware Change / End.
export const RUN_STOPPED_UNKNOWN =
  "Later shifts were left as they are: their weekly offs and holidays can't be worked out " +
  'past the last shift changed. Check the rest of the run.'
export const RUN_OFF_DAYS_UNKNOWN =
  "Only the shift on that date was updated: their weekly offs and holidays can't be worked out, " +
  'so later shifts were left as they are. Check the rest of the run.'
// Plan 2026-11-06-002 U3: each skip refuses on its own when its days can't be told.
export const WEEKENDS_UNKNOWN =
  "Their weekends can't be worked out for those dates. " +
  'Untick Skip weekends, or ask HR to set up their holiday list.'
export const HOLIDAYS_UNKNOWN =
  "Their holidays can't be worked out for those dates. " +
  'Untick Skip holidays, or ask HR to set up their holiday list.'
// Plan 2026-11-05-001 A2: a run-aware Change / End stopping at a one-day gap.
export const RUN_SPLIT =
  'Later shifts were left as they are: the same shift starts again after a one-day change. ' +
  'Check the rest of the run.'

/** `value` when it is a real ISO calendar date, otherwise null. */
function isoDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
    ? value
    : null
}

function addDays(iso, days) {
  const parsed = new Date(`${iso}T00:00:00Z`)
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return parsed.toISOString().slice(0, 10)
}

const dayBefore = (iso) => addDays(iso, -1)

function daysBetween(start, end) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000)
}

/** The date an operation acts on: `to_date` for End, `from_date` otherwise
 * (Swap Day and Clear Day included). */
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

// `{ from, to, dates: Set }`, or null (cannot tell). A day outside
// `from`..`to` is unknown too (`roster_ops._known_set`).
function knownSet(value) {
  if (!value) return null
  const from = isoDate(value.from)
  const to = isoDate(value.to)
  if (!from || !to) return null
  return { from, to, dates: new Set((value.dates ?? []).map(isoDate).filter(Boolean)) }
}

// `{ weekends, holidays }`, each a known set or null (plan 2026-11-06-002
// KTD2); null when neither can tell (`roster_ops._off_days`).
function normalizeOff(value) {
  if (!value) return null
  const off = { weekends: knownSet(value.weekends), holidays: knownSet(value.holidays) }
  return off.weekends || off.holidays ? off : null
}

const covers = (known, start, end) => !!known && known.from <= start && end <= known.to

/** true / false, or null when `off` cannot tell for `day` (plan
 * 2026-11-06-002 KTD3): off when either known set has it, working only when
 * both are known there and neither has it. */
function isOff(off, day) {
  if (!off) return null
  const states = [off.weekends, off.holidays].map((known) =>
    covers(known, day, day) ? known.dates.has(day) : null,
  )
  if (states.includes(true)) return true
  return states.includes(null) ? null : false
}

// The block right after `block` in its run: the next one by date, same
// shift type, only known off days between (`roster_ops._next_in_run`).
function nextInRun(rows, block, off) {
  if (!off || block.end_date === null) return null
  const later = rows.filter((a) => a.start_date > block.end_date)
  if (!later.length) return null
  const following = later.reduce((min, a) => (a.start_date < min.start_date ? a : min))
  if (following.shift_type !== block.shift_type) return null
  for (let day = addDays(block.end_date, 1); day < following.start_date; day = addDays(day, 1)) {
    if (isOff(off, day) !== true) return null
  }
  return following
}

// The first block starting after `day`, or null.
function followingAfter(rows, day) {
  const later = rows.filter((a) => a.start_date > day)
  return later.length ? later.reduce((min, a) => (a.start_date < min.start_date ? a : min)) : null
}

// Why the run ending at `block` may stop short, or null; a working day or
// another shift type is a normal break -- except the one-day split (plan
// 2026-11-05-001 A2): the same shift resumes after exactly one working day,
// empty or one one-day block of another shift (`roster_ops._run_note`).
function runNote(rows, block, off) {
  if (block.end_date === null) return null
  let following = followingAfter(rows, block.end_date)
  let swapped = null
  if (following && following.shift_type !== block.shift_type) {
    swapped = following.start_date
    if (swapped !== following.end_date) return null
    // Only known off days may sit between the run and the one-day block,
    // so a swap on the Monday after a weekend still counts.
    for (let day = addDays(block.end_date, 1); day < swapped; day = addDays(day, 1)) {
      if (isOff(off, day) !== true) return null
    }
    following = followingAfter(rows, swapped)
  }
  if (!following || following.shift_type !== block.shift_type) return null
  if (!off) return RUN_OFF_DAYS_UNKNOWN
  let working = false
  for (let day = addDays(block.end_date, 1); day < following.start_date; day = addDays(day, 1)) {
    const state = day === swapped ? false : isOff(off, day)
    if (state === null && !working) return RUN_STOPPED_UNKNOWN
    if (state !== true) {
      // A second working (or unknown) day is a normal break.
      if (working) return null
      working = true
    }
  }
  return working ? RUN_SPLIT : null
}

const hasPreviousInRun = (rows, block, off) =>
  rows.some((a) => a !== block && nextInRun(rows, a, off) === block)

function resolve(rows, key, day, off) {
  const named = key ? (rows.find((a) => a.key === key) ?? null) : null
  if (named && overlaps(named, day, day)) return [named, false]
  const covering = rows.find((a) => overlaps(a, day, day))
  if (covering) return [covering, false]
  for (const block of rows) {
    if (block.end_date !== null && block.end_date < day) {
      const following = nextInRun(rows, block, off)
      if (following && following.start_date > day) return [following, true]
    }
  }
  return [named, false]
}

// The contiguous runs of days in `day`..`end` not in `skipped`.
function workingSpans(day, end, skipped) {
  const spans = []
  let start = null
  for (; day <= end; day = addDays(day, 1)) {
    if (skipped.has(day)) {
      if (start) spans.push([start, dayBefore(day)])
      start = null
    } else if (!start) start = day
  }
  if (start) spans.push([start, end])
  return spans
}

function step(action, employee, block, endDate = null) {
  return {
    action,
    employee,
    key: block.key,
    shift_type: block.shift_type,
    start_date: block.start_date,
    end_date: action === 'end' ? endDate : block.end_date,
  }
}

const fail = (message) => [message, []]

function apply(rows, op, index, off, runAware, notes) {
  const employee = op.employee
  if (!ACTIONS.includes(op.action)) return fail(BAD_ACTION)
  if (op.action !== END && op.action !== CLEAR_DAY && !op.shift_type) return fail(NO_SHIFT_TYPE)
  const day = opDate(op)
  if (!day) return fail(BAD_DATE)
  let counter = 0
  const newBlock = (shiftType, start, end) => {
    const block = { key: `new-${index}-${counter++}`, shift_type: shiftType, start_date: start, end_date: end }
    rows.push(block)
    return step('create', employee, block)
  }
  const remove = (block) => rows.splice(rows.indexOf(block), 1)

  if (op.action === ASSIGN) {
    const end = op.to_date ? isoDate(op.to_date) : null
    if (op.to_date && !end) return fail(BAD_DATE)
    if (end && end < day) return fail(END_BEFORE_START)
    let spans = [[day, end]]
    // Plan 2026-11-06-002 U3: each skip skips its own set and refuses when
    // that set can't be told for any day of the range. The flags are 0 / 1
    // from the server or `rosterRequests.toOp`; not parsed again here.
    const ticked = [
      ['weekends', op.skip_weekends, WEEKENDS_UNKNOWN],
      ['holidays', op.skip_holidays, HOLIDAYS_UNKNOWN],
    ].filter(([, flag]) => flag)
    if (runAware && end && ticked.length) {
      if (daysBetween(day, end) + 1 > SKIP_MAX_DAYS) return fail(SKIP_SPAN)
      const skipped = new Set()
      for (const [part, , refusal] of ticked) {
        const known = off ? off[part] : null
        if (!covers(known, day, end)) return fail(refusal)
        known.dates.forEach((date) => skipped.add(date))
      }
      spans = workingSpans(day, end, skipped)
      if (!spans.length) return fail(NO_WORKING_DAYS)
    }
    if (rows.some((a) => spans.some(([start, last]) => overlaps(a, start, last)))) return fail(OVERLAP)
    return [null, spans.map(([start, last]) => newBlock(op.shift_type, start, last))]
  }

  if (op.action === SWAP_DAY || op.action === CLEAR_DAY) {
    // Plan 2026-11-05-001 KTD4 / A2: the block covering `day` only, never a
    // run; cancel / end first, then the creates.
    const named = op.assignment ? rows.find((a) => a.key === op.assignment) : null
    const target =
      (named && overlaps(named, day, day) ? named : null) ?? rows.find((a) => overlaps(a, day, day))
    if (!target) return fail(NO_SHIFT_ON_DATE)
    if (op.action === SWAP_DAY && op.shift_type === target.shift_type) return fail(SAME_SHIFT)
    const oldEnd = target.end_date
    const steps = []
    if (day === target.start_date) {
      remove(target)
      steps.push(step('cancel', employee, target))
    } else {
      target.end_date = dayBefore(day)
      steps.push(step('end', employee, target, target.end_date))
    }
    if (op.action === SWAP_DAY) steps.push(newBlock(op.shift_type, day, day))
    if (oldEnd === null || oldEnd > day) steps.push(newBlock(target.shift_type, addDays(day, 1), oldEnd))
    return [null, steps]
  }

  const [target, viaGap] = off
    ? resolve(rows, op.assignment, day, off)
    : [findTarget(rows, op.assignment, day), false]
  if (!target) return fail(NO_SHIFT_ON_DATE)
  const later = []
  for (let next = nextInRun(rows, target, off); next; next = nextInRun(rows, next, off)) later.push(next)
  const note = runAware ? runNote(rows, later.at(-1) ?? target, off) : null
  // Pushed before a refusal check; `simulate` drops it if refused.
  if (note) notes.push(note)

  const steps = []
  if (op.action === CHANGE) {
    const recreate = [...later]
    let oldEnd = null
    if (viaGap || (day === target.start_date && hasPreviousInRun(rows, target, off))) {
      recreate.unshift(target)
    } else if (day <= target.start_date) {
      return fail(CHANGE_ON_START)
    } else if (target.end_date && day > target.end_date) {
      return fail(AFTER_END)
    } else {
      oldEnd = target.end_date
      target.end_date = dayBefore(day)
      steps.push(step('end', employee, target, target.end_date))
    }
    for (const block of recreate) {
      remove(block)
      steps.push(step('cancel', employee, block))
    }
    if (!recreate.includes(target)) steps.push(newBlock(op.shift_type, day, oldEnd))
    for (const block of recreate) steps.push(newBlock(op.shift_type, block.start_date, block.end_date))
    return [null, steps]
  }

  if (viaGap) {
    later.unshift(target)
  } else {
    if (day < target.start_date) return fail(END_BEFORE_START)
    if (target.end_date && day > target.end_date) return fail(AFTER_END)
    target.end_date = day
    steps.push(step('end', employee, target, day))
  }
  for (const block of later) {
    remove(block)
    steps.push(step('cancel', employee, block))
  }
  return [null, steps]
}

/** Apply `ops` in order to a copy of `coverage`. Returns `{ coverage,
 * problems, steps }`, each problem `{ index, employee, message }` and
 * `steps[index]` the create / end / cancel steps that operation took. A
 * refused operation changes nothing and the rest still run. `offDays`
 * (`{ employee: { weekends, holidays } | null }`, each set `{ from, to,
 * dates } | null`, plan 2026-11-06-002 KTD2) turns on skip expansion and
 * run-aware Change / End; omitted, the legacy single-block rules apply
 * (`roster_ops.simulate`). `notes`, when an array, collects `{ index,
 * employee, message }` for a run-aware Change / End that may have stopped
 * short on unknown off days or at a one-day change (plan 2026-11-05-001 A2);
 * notes never refuse and are not steps. */
export function simulate(coverage, ops, offDays = null, notes = null) {
  const result = {}
  for (const [employee, rows] of Object.entries(coverage ?? {})) {
    result[employee] = rows.map((a) => ({
      key: a.key,
      shift_type: a.shift_type,
      start_date: a.start_date,
      end_date: a.end_date ?? null,
    }))
  }
  const runAware = offDays !== null && offDays !== undefined
  const offs = {}
  const problems = []
  const steps = []
  ops.forEach((op, index) => {
    result[op.employee] ??= []
    if (!(op.employee in offs)) offs[op.employee] = runAware ? normalizeOff(offDays[op.employee]) : null
    const found = []
    const [message, taken] = apply(result[op.employee], op, index, offs[op.employee], runAware, found)
    steps.push(taken)
    if (message) problems.push({ index, employee: op.employee, message })
    else if (notes) notes.push(...found.map((note) => ({ index, employee: op.employee, message: note })))
  })
  return { coverage: result, problems, steps }
}
