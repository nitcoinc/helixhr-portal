import { describe, expect, it } from 'vitest'
import { formatDate } from './dates'
import { OVERLAP } from './rosterDraft'
import {
  cellProposal,
  checkProposal,
  coverageFromRows,
  draftOps,
  draftSummary,
  opIndexForCell,
  opLabel,
  reasonLines,
  returnedNotice,
  submittedMessage,
  toOp,
} from './rosterRequests'

const assign = (employee, from, to, shift = 'Night') => ({
  employee,
  action: 'Assign',
  shift_type: shift,
  from_date: from,
  to_date: to,
  assignment: null,
})
const end = (employee, to, assignment = 'SA-1') => ({
  employee,
  action: 'End',
  shift_type: null,
  from_date: null,
  to_date: to,
  assignment,
})
const change = (employee, from, assignment = 'SA-1', shift = 'Late') => ({
  employee,
  action: 'Change',
  shift_type: shift,
  from_date: from,
  to_date: null,
  assignment,
})

describe('toOp / draftOps', () => {
  it('keeps the six operation fields, blanks as null', () => {
    expect(toOp({ employee: 'E1', action: 'End', to_date: '2026-11-05', employee_name: 'Asha', extra: 1 })).toEqual({
      employee: 'E1',
      action: 'End',
      shift_type: null,
      from_date: null,
      to_date: '2026-11-05',
      assignment: null,
    })
  })
  it('is empty with no draft', () => {
    expect(draftOps(null)).toEqual([])
    expect(draftOps({ rows: [assign('E1', '2026-11-02', null)] })).toHaveLength(1)
  })
})

describe('draftSummary', () => {
  it('counts changes and distinct people', () => {
    expect(draftSummary([])).toBe('')
    expect(draftSummary([assign('E1', '2026-11-02', null)])).toBe('1 change for 1 person')
    expect(
      draftSummary([assign('E1', '2026-11-02', '2026-11-03'), end('E1', '2026-11-09'), end('E2', '2026-11-05')]),
    ).toBe('3 changes for 2 people')
  })
})

describe('opLabel', () => {
  it('words each action', () => {
    expect(opLabel(assign('E1', '2026-11-02', '2026-11-04'))).toBe(
      `Assign Night from ${formatDate('2026-11-02')} to ${formatDate('2026-11-04')}`,
    )
    expect(opLabel(assign('E1', '2026-11-02', null))).toBe(`Assign Night from ${formatDate('2026-11-02')}, no end date`)
    expect(opLabel(change('E1', '2026-11-05'))).toBe(`Change to Late from ${formatDate('2026-11-05')}`)
    expect(opLabel(end('E1', '2026-11-06'))).toBe(`End the shift after ${formatDate('2026-11-06')}`)
  })
})

const week = [
  {
    employee: 'E1',
    cells: [
      { date: '2026-11-02', shift_type: 'Day', assignment: 'SA-1', assignment_start: '2026-10-01', assignment_end: null },
      { date: '2026-11-03', shift_type: 'Day', assignment: 'SA-1', assignment_start: '2026-10-01', assignment_end: null },
    ],
  },
  { employee: 'E2', cells: [{ date: '2026-11-02', shift_type: null, assignment: null }] },
]

describe('coverageFromRows', () => {
  it('lists each assignment a cell names once', () => {
    expect(coverageFromRows(week)).toEqual({
      E1: [{ key: 'SA-1', shift_type: 'Day', start_date: '2026-10-01', end_date: null }],
      E2: [],
    })
  })
})

describe('checkProposal', () => {
  const coverage = coverageFromRows(week)
  const today = '2026-10-31'

  it('refuses today and earlier, naming the person', () => {
    const ops = [assign('E2', '2026-10-31', null)]
    expect(checkProposal({ ops, index: 0, coverage, today, name: 'Ravi' })).toBe(
      'Ravi: a change can start tomorrow at the earliest.',
    )
  })
  it('refuses an overlap with the visible coverage', () => {
    const ops = [assign('E1', '2026-11-02', '2026-11-03')]
    expect(checkProposal({ ops, index: 0, coverage, today, name: 'Asha' })).toBe(`Asha: ${OVERLAP}`)
  })
  it('passes a clean operation, and judges only the asked index', () => {
    const ops = [assign('E1', '2026-11-02', null), end('E1', '2026-11-05')]
    expect(checkProposal({ ops, index: 1, coverage, today, name: 'Asha' })).toBe('')
  })
  it('ignores other people, whose coverage this page may not show', () => {
    const ops = [end('E9', '2026-11-05', 'SA-9'), assign('E2', '2026-11-02', null)]
    expect(checkProposal({ ops, index: 1, coverage, today, name: 'Ravi' })).toBe('')
  })
})

describe('opIndexForCell / cellProposal', () => {
  const ops = [assign('E2', '2026-11-02', '2026-11-03'), end('E1', '2026-11-02')]
  const preview = {
    E2: [{ key: 'new-0', shift_type: 'Night', start_date: '2026-11-02', end_date: '2026-11-03' }],
    E1: [{ key: 'SA-1', shift_type: 'Day', start_date: '2026-10-01', end_date: '2026-11-02' }],
  }

  it('finds the operation that writes a day', () => {
    expect(opIndexForCell(ops, 'E2', { date: '2026-11-03' })).toBe(0)
    expect(opIndexForCell(ops, 'E2', { date: '2026-11-04' })).toBe(-1)
    expect(opIndexForCell(ops, 'E1', { date: '2026-11-03', assignment: 'SA-1' })).toBe(1)
    expect(opIndexForCell(ops, 'E1', { date: '2026-11-02', assignment: 'SA-1' })).toBe(-1)
  })
  it('the last operation on a day wins', () => {
    const chained = [assign('E2', '2026-11-02', null), change('E2', '2026-11-04', null)]
    expect(opIndexForCell(chained, 'E2', { date: '2026-11-05' })).toBe(1)
    expect(opIndexForCell(chained, 'E2', { date: '2026-11-03' })).toBe(0)
  })
  it('names the proposed shift, in words', () => {
    expect(cellProposal(ops, preview, 'E2', { date: '2026-11-02', assignment: null })).toEqual({
      index: 0,
      shift_type: 'Night',
      label: 'Proposed: Night',
    })
  })
  it('says a day that loses its shift', () => {
    expect(cellProposal(ops, preview, 'E1', { date: '2026-11-03', assignment: 'SA-1' })).toEqual({
      index: 1,
      shift_type: null,
      label: 'Proposed: no shift',
    })
  })
  it('leaves an unchanged day, a day before the first change and a person not in the draft alone', () => {
    expect(cellProposal(ops, preview, 'E1', { date: '2026-11-02', assignment: 'SA-1' })).toBeNull()
    expect(cellProposal(ops, preview, 'E1', { date: '2026-10-30', assignment: 'SA-0' })).toBeNull()
    expect(cellProposal(ops, preview, 'E3', { date: '2026-11-02', assignment: 'SA-3' })).toBeNull()
  })
})

describe('words around a request', () => {
  it('confirms one request or one per company', () => {
    expect(submittedMessage(['HRC-1'])).toBe('Sent to HR for approval')
    expect(submittedMessage(['HRC-1', 'HRC-2'])).toBe('Sent as 2 requests (one per company)')
  })
  it('splits returned reasons one per line', () => {
    expect(reasonLines('Asha: a date passed.\n\n Ravi moved. ')).toEqual(['Asha: a date passed.', 'Ravi moved.'])
    expect(reasonLines(null)).toEqual([])
  })
  it('never words a returned approval as a success', () => {
    expect(returnedNotice('Meera Nair', [{ message: "Asha's shifts changed after this request was sent." }])).toBe(
      "Not approved — sent back to Meera Nair: Asha's shifts changed after this request was sent.",
    )
    expect(returnedNotice('', [])).toBe('Not approved — sent back to the manager.')
  })
})
