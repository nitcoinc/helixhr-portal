// Plan 2026-10-31-001 U10 / R10-R12, R15, R17. The pure half of a manager's
// roster request: which cell a draft operation touches, how the draft bar
// and the status strip word it, and the instant check run before
// `save_roster_draft`. The server re-runs every rule at save, submit and
// approval (`helixhr.api._review_roster_ops`); nothing here decides.

import { formatDate, formatDateRange } from './dates'
import { ASSIGN, CHANGE, END, opDate, simulate } from './rosterDraft'

const OP_FIELDS = ['employee', 'action', 'shift_type', 'from_date', 'to_date', 'assignment']

const isSkip = (value) => value === 1 || value === true || value === '1'

/** One draft row as `save_roster_draft` takes it: the six operation fields,
 * blanks as null, plus `skip_off_days` as 0 / 1 (plan 2026-11-03-001 R6:
 * an edit keeps it). */
export function toOp(row) {
  return {
    ...Object.fromEntries(OP_FIELDS.map((field) => [field, row?.[field] || null])),
    skip_off_days: isSkip(row?.skip_off_days) ? 1 : 0,
  }
}

/** Whether an operation skips weekly offs and holidays: only an Assign with
 * an end date can (the server clears the flag elsewhere). */
export function skipsOffDays(op) {
  return op?.action === ASSIGN && !!op.to_date && isSkip(op.skip_off_days)
}

/** The shifts the server says one draft row creates, in words: "2 shifts:
 * 3 – 7 Nov 2026; 10 – 14 Nov 2026"; '' with none. The browser never
 * expands a skip itself (plan 2026-11-03-001 A11): `blocks` is the payload's. */
export function blocksLabel(blocks) {
  const list = blocks || []
  if (!list.length) return ''
  const ranges = list.map((block) =>
    block.end_date ? formatDateRange(block.start_date, block.end_date) : `from ${formatDate(block.start_date)}`,
  )
  return `${plural(list.length, 'shift', 'shifts')}: ${ranges.join('; ')}`
}

/** "3 changes → 7 shifts", HR's count of what a request writes (R10). */
export function changeAndBlockCount(changes, blocks) {
  return `${plural(changes || 0, 'change', 'changes')} → ${plural(blocks || 0, 'shift', 'shifts')}`
}

/** The draft's operations, in order; [] with no draft. */
export function draftOps(draft) {
  return (draft?.rows || []).map(toOp)
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`
}

/** "2 changes for 2 people" -- the draft bar's sentence; '' when empty. */
export function draftSummary(ops) {
  const list = ops || []
  if (!list.length) return ''
  const people = new Set(list.map((op) => op.employee)).size
  return `${plural(list.length, 'change', 'changes')} for ${plural(people, 'person', 'people')}`
}

/** One operation in words, without the person: "Assign Night from 2 Nov to
 * 4 Nov", "Change to Night from 5 Nov", "End the shift after 6 Nov". */
export function opLabel(op) {
  if (op?.action === ASSIGN) {
    const until = op.to_date ? ` to ${formatDate(op.to_date)}` : ', no end date'
    return `Assign ${op.shift_type} from ${formatDate(op.from_date)}${until}`
  }
  if (op?.action === CHANGE) return `Change to ${op.shift_type} from ${formatDate(op.from_date)}`
  if (op?.action === END) return `End the shift after ${formatDate(op.to_date)}`
  return op?.action || ''
}

/** The live coverage the visible week shows, as the simulator takes it:
 * `{ employee: [{ key, shift_type, start_date, end_date }] }`, one entry per
 * assignment a cell names. Only what this week can see -- the server checks
 * the rest. */
export function coverageFromRows(rows) {
  const coverage = {}
  for (const row of rows || []) {
    const seen = new Map()
    for (const cell of row.cells || []) {
      if (!cell.assignment || seen.has(cell.assignment)) continue
      seen.set(cell.assignment, {
        key: cell.assignment,
        shift_type: cell.shift_type,
        start_date: cell.assignment_start,
        end_date: cell.assignment_end || null,
      })
    }
    coverage[row.employee] = [...seen.values()]
  }
  return coverage
}

/**
 * The instant check before a save: the refusal for operation `index` of
 * `ops` as the server would word it ("Priya Shah: <sentence>"), or ''.
 * Runs only that person's operations, against `coverage` (the visible week),
 * so an operation elsewhere that this page cannot see is never refused here.
 * `today` is the site's date; a change must start after it (R13).
 */
export function checkProposal({ ops, index, coverage, today, name }) {
  const op = ops?.[index]
  if (!op) return ''
  const who = name || op.employee
  const day = opDate(op)
  if (day && today && day <= today) return `${who}: a change can start tomorrow at the earliest.`
  // A skip is expanded by the server alone (A11): checked as one block here
  // it could refuse an overlap that falls only on an off day.
  if (skipsOffDays(op)) return ''
  const positions = []
  const mine = []
  ops.forEach((candidate, position) => {
    if (candidate.employee !== op.employee) return
    positions.push(position)
    mine.push(candidate)
  })
  const { problems } = simulate({ [op.employee]: coverage?.[op.employee] || [] }, mine)
  const problem = problems.find((item) => positions[item.index] === index)
  return problem ? `${who}: ${problem.message}` : ''
}

function covers(item, date) {
  return item.start_date <= date && (!item.end_date || item.end_date >= date)
}

/** The index of the last operation that writes `cell`'s day for `employee`,
 * or -1. An End writes the days after its date; a Change and an End only
 * touch the assignment they name. */
export function opIndexForCell(ops, employee, cell) {
  const date = cell?.date
  for (let index = (ops || []).length - 1; index >= 0; index--) {
    const op = ops[index]
    if (op.employee !== employee) continue
    const sameAssignment = !op.assignment || op.assignment === cell.assignment
    if (op.action === ASSIGN && op.from_date <= date && (!op.to_date || date <= op.to_date)) return index
    if (op.action === CHANGE && sameAssignment && op.from_date <= date) return index
    if (op.action === END && sameAssignment && op.to_date < date) return index
  }
  return -1
}

/**
 * What the saved draft proposes for one cell, from the server's `preview`
 * (the simulated coverage of everyone the draft names): `{ index, shift_type,
 * label }` -- `shift_type` null when the day loses its shift -- or null when
 * the draft leaves the day as it is. `label` is the visible text the cell
 * carries, so the proposal is never an outline alone.
 */
export function cellProposal(ops, preview, employee, cell) {
  const rows = preview?.[employee]
  if (!rows || !cell?.date) return null
  // The preview reads coverage from the person's first operation date on,
  // so an earlier day is not in it and nothing proposes to change it.
  const first = (ops || [])
    .filter((op) => op.employee === employee)
    .map(opDate)
    .filter(Boolean)
    .sort()[0]
  if (!first || cell.date < first) return null
  const covering = rows.find((item) => covers(item, cell.date))
  let shift
  if (covering && String(covering.key).startsWith('new-')) shift = covering.shift_type
  else if (!covering && cell.assignment) shift = null
  else return null
  return {
    index: opIndexForCell(ops, employee, cell),
    shift_type: shift,
    label: shift ? `Proposed: ${shift}` : 'Proposed: no shift',
  }
}

/** The submit's confirmation: one request per company (KTD12). */
export function submittedMessage(names) {
  const count = (names || []).length
  return count > 1 ? `Sent as ${count} requests (one per company)` : 'Sent to HR for approval'
}

/** A Returned request's reasons, one per line as the server joins them. */
export function reasonLines(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

/** HR's notice when an Approve came back Returned (R16): never a success. */
export function returnedNotice(managerName, reasons) {
  const words = (reasons || []).map((reason) => reason.message).filter(Boolean).join(' ')
  const who = managerName || 'the manager'
  return words ? `Not approved — sent back to ${who}: ${words}` : `Not approved — sent back to ${who}.`
}
