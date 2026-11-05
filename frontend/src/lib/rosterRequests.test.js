import { describe, expect, it } from 'vitest'
import { formatDate } from './dates'
import { OVERLAP } from './rosterDraft'
import {
  blocksLabel,
  cellProposal,
  changeAndBlockCount,
  checkProposal,
  coverageFromRows,
  draftOps,
  dayOpsOnLine,
  draftSummary,
  isOneDayOp,
  lineCovers,
  opIndexForCell,
  opLabel,
  reasonLines,
  returnedNotice,
  skipFlags,
  skipLabel,
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
  it('keeps the six operation fields, blanks as null, and both skip flags as 0 / 1', () => {
    expect(toOp({ employee: 'E1', action: 'End', to_date: '2026-11-05', employee_name: 'Asha', extra: 1 })).toEqual({
      employee: 'E1',
      action: 'End',
      shift_type: null,
      from_date: null,
      to_date: '2026-11-05',
      assignment: null,
      skip_weekends: 0,
      skip_holidays: 0,
    })
  })
  // Plan 2026-11-06-002 U4 / KTD5.
  it('reads a legacy skip_off_days as both skips, and new keys as given', () => {
    const ranged = assign('E1', '2026-11-02', '2026-11-27')
    const flags = (row) => [toOp(row).skip_weekends, toOp(row).skip_holidays]
    expect(flags({ ...ranged, skip_off_days: 1 })).toEqual([1, 1])
    expect(flags({ ...ranged, skip_off_days: true })).toEqual([1, 1])
    expect(flags({ ...ranged, skip_weekends: 1 })).toEqual([1, 0])
    expect(flags({ ...ranged, skip_holidays: '1' })).toEqual([0, 1])
    // A new key present wins over the legacy one.
    expect(flags({ ...ranged, skip_off_days: 1, skip_weekends: 0, skip_holidays: 0 })).toEqual([0, 0])
    expect(flags({ ...change('E1', '2026-11-05'), skip_weekends: 1, skip_holidays: 1 })).toEqual([0, 0])
    expect(flags({ ...assign('E1', '2026-11-02', null), skip_off_days: 1 })).toEqual([0, 0])
  })
  it('keeps a weekends-only row weekends-only through a re-save', () => {
    // The server's row echoes skip_off_days = 0 for weekends only.
    const saved = { ...assign('E1', '2026-11-02', '2026-11-27'), skip_weekends: 1, skip_holidays: 0, skip_off_days: 0 }
    expect(draftOps({ rows: [saved] })[0]).toMatchObject({ skip_weekends: 1, skip_holidays: 0 })
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

// Plan 2026-11-03-001 U4: a skip row and the blocks the server expanded.
describe('skip rows', () => {
  const skip = (from, to) => ({ ...assign('E1', from, to), skip_off_days: 1 })

  it('only an Assign with an end date skips', () => {
    expect(skipFlags(skip('2026-11-02', '2026-11-13'))).toEqual({ weekends: true, holidays: true })
    expect(skipFlags({ ...skip('2026-11-02', null) })).toEqual({ weekends: false, holidays: false })
    expect(skipFlags({ ...change('E1', '2026-11-05'), skip_off_days: 1 })).toEqual({ weekends: false, holidays: false })
    expect(skipFlags(assign('E1', '2026-11-02', '2026-11-13'))).toEqual({ weekends: false, holidays: false })
  })

  // Plan 2026-11-06-002 U4: the label names which skip.
  it('words each skip combination, and none when neither', () => {
    const ranged = assign('E1', '2026-11-02', '2026-11-13')
    expect(skipLabel({ ...ranged, skip_weekends: 1, skip_holidays: 1 })).toBe('skips weekends and holidays')
    expect(skipLabel({ ...ranged, skip_weekends: 1, skip_holidays: 0 })).toBe('skips weekends')
    expect(skipLabel({ ...ranged, skip_weekends: 0, skip_holidays: 1 })).toBe('skips holidays')
    expect(skipLabel({ ...ranged, skip_weekends: 0, skip_holidays: 0 })).toBe('')
    expect(skipLabel(skip('2026-11-02', '2026-11-13'))).toBe('skips weekends and holidays')
    expect(skipLabel(ranged)).toBe('')
  })

  it('words one block and several', () => {
    expect(blocksLabel([])).toBe('')
    expect(blocksLabel(null)).toBe('')
    const one = [{ shift_type: 'Night', start_date: '2026-11-02', end_date: '2026-11-06' }]
    expect(blocksLabel(one)).toMatch(/^1 shift: /)
    const two = [...one, { shift_type: 'Night', start_date: '2026-11-09', end_date: '2026-11-13' }]
    const label = blocksLabel(two)
    expect(label).toMatch(/^2 shifts: /)
    expect(label.split('; ')).toHaveLength(2)
    expect(blocksLabel([{ shift_type: 'Night', start_date: '2026-11-02', end_date: null }])).toBe(
      `1 shift: from ${formatDate('2026-11-02')}`,
    )
  })

  it('counts changes and shifts', () => {
    expect(changeAndBlockCount(1, 1)).toBe('1 change → 1 shift')
    expect(changeAndBlockCount(3, 7)).toBe('3 changes → 7 shifts')
  })

  it('resolves every block of a skip row to its stored index', () => {
    const ops = [assign('E2', '2026-11-03', null), skip('2026-11-02', '2026-11-13')]
    for (const date of ['2026-11-02', '2026-11-06', '2026-11-09', '2026-11-13']) {
      expect(opIndexForCell(ops, 'E1', { date, assignment: null })).toBe(1)
    }
  })

  it('leaves the local check of a skip row to the server', () => {
    const coverage = { E1: [{ key: 'SA-1', shift_type: 'Day', start_date: '2026-11-07', end_date: '2026-11-08' }] }
    const ops = [skip('2026-11-02', '2026-11-13')]
    expect(checkProposal({ ops, index: 0, coverage, today: '2026-11-01', name: 'Asha' })).toBe('')
    expect(checkProposal({ ops, index: 0, coverage, today: '2026-11-02', name: 'Asha' })).toMatch(/tomorrow/)
  })
})

// Plan 2026-11-05-001 U5 / A3: one-day changes on an assigned cell.
describe('one-day rows', () => {
  const swap = (employee, date, shift = 'Night', assignment = 'SA-1') => ({
    employee,
    action: 'Swap Day',
    shift_type: shift,
    from_date: date,
    to_date: null,
    assignment,
  })
  const clear = (employee, date, assignment = 'SA-1') => ({
    employee,
    action: 'Clear Day',
    shift_type: null,
    from_date: date,
    to_date: null,
    assignment,
  })
  const live = (date) => ({ date, shift_type: 'Day', assignment: 'SA-1' })

  it('words a swap and a clear, and neither skips', () => {
    expect(opLabel(swap('E1', '2026-11-10'))).toBe(`Night on ${formatDate('2026-11-10')} only`)
    expect(opLabel(clear('E1', '2026-11-10'))).toBe(`No shift on ${formatDate('2026-11-10')}`)
    expect(skipLabel({ ...swap('E1', '2026-11-10'), skip_off_days: 1 })).toBe('')
    expect(skipLabel({ ...clear('E1', '2026-11-10'), skip_off_days: 1 })).toBe('')
  })

  it('a one-day row writes its own day only', () => {
    const ops = [clear('E1', '2026-11-04'), swap('E1', '2026-11-06')]
    expect(opIndexForCell(ops, 'E1', live('2026-11-04'))).toBe(0)
    expect(opIndexForCell(ops, 'E1', live('2026-11-06'))).toBe(1)
    expect(opIndexForCell(ops, 'E1', live('2026-11-05'))).toBe(-1)
    expect(opIndexForCell(ops, 'E1', live('2026-11-07'))).toBe(-1)
  })

  it('marks the changed days, not the recreated tail with the same shift', () => {
    // Clear Wed 4 and Swap Fri 6 on an open-ended Day shift: the simulated
    // coverage has new blocks of Day for Thu 5 and from Sat 7.
    const ops = [clear('E1', '2026-11-04'), swap('E1', '2026-11-06')]
    const preview = {
      E1: [
        { key: 'SA-1', shift_type: 'Day', start_date: '2026-10-01', end_date: '2026-11-03' },
        { key: 'new-0-1', shift_type: 'Day', start_date: '2026-11-05', end_date: '2026-11-05' },
        { key: 'new-1-2', shift_type: 'Night', start_date: '2026-11-06', end_date: '2026-11-06' },
        { key: 'new-1-3', shift_type: 'Day', start_date: '2026-11-07', end_date: null },
      ],
    }
    expect(cellProposal(ops, preview, 'E1', live('2026-11-04'))).toEqual({
      index: 0,
      shift_type: null,
      label: 'Proposed: no shift',
    })
    expect(cellProposal(ops, preview, 'E1', live('2026-11-05'))).toBeNull()
    expect(cellProposal(ops, preview, 'E1', live('2026-11-06'))).toEqual({
      index: 1,
      shift_type: 'Night',
      label: 'Proposed: Night',
    })
    expect(cellProposal(ops, preview, 'E1', live('2026-11-08'))).toBeNull()
    // An empty day getting a new block is still a proposal.
    expect(cellProposal(ops, preview, 'E1', { date: '2026-11-08', shift_type: null, assignment: null })).toMatchObject({
      label: 'Proposed: Day',
    })
  })
})

// Plan 2026-11-06-002 U5: one-day changes on days only the draft gives a
// shift. Assign Morning Mon 3 - Fri 28 Mar 2031, both skips; no live shift.
describe('one-day changes on a draft line', () => {
  const line = { ...assign('E1', '2031-03-03', '2031-03-28', 'Morning'), skip_weekends: 1, skip_holidays: 1 }
  const day = (action, date, shift = null) => ({
    employee: 'E1',
    action,
    shift_type: shift,
    from_date: date,
    to_date: null,
    assignment: null,
  })
  const empty = (date) => ({ date, shift_type: null, assignment: null })
  // The server's simulated weeks: Mon-Fri blocks, Fri 14 cut out.
  const preview = {
    E1: [
      { key: 'new-0-0', shift_type: 'Morning', start_date: '2031-03-03', end_date: '2031-03-07' },
      { key: 'new-0-1', shift_type: 'Morning', start_date: '2031-03-10', end_date: '2031-03-13' },
      { key: 'new-0-3', shift_type: 'Morning', start_date: '2031-03-17', end_date: '2031-03-21' },
      { key: 'new-0-4', shift_type: 'Morning', start_date: '2031-03-24', end_date: '2031-03-28' },
    ],
  }

  it('a cleared day reads as proposed with its own index, with no live or covering shift', () => {
    const ops = [line, day('Clear Day', '2031-03-14')]
    expect(cellProposal(ops, preview, 'E1', empty('2031-03-14'))).toEqual({
      index: 1,
      shift_type: null,
      label: 'Proposed: no shift',
    })
    expect(cellProposal(ops, preview, 'E1', empty('2031-03-13'))).toMatchObject({ index: 0, shift_type: 'Morning' })
    // A skipped weekend stays a plain day.
    expect(cellProposal(ops, preview, 'E1', empty('2031-03-15'))).toBeNull()
  })

  it('a swapped day names the swap and its index; the line goes on', () => {
    const ops = [line, day('Swap Day', '2031-03-14', 'Night')]
    const swapped = {
      E1: [...preview.E1, { key: 'new-1-0', shift_type: 'Night', start_date: '2031-03-14', end_date: '2031-03-14' }],
    }
    expect(cellProposal(ops, swapped, 'E1', empty('2031-03-14'))).toEqual({
      index: 1,
      shift_type: 'Night',
      label: 'Proposed: Night',
    })
    expect(cellProposal(ops, swapped, 'E1', empty('2031-03-17'))).toMatchObject({ index: 0, shift_type: 'Morning' })
  })

  it('finds the whole line under a one-day change', () => {
    const ops = [line, day('Clear Day', '2031-03-14')]
    expect(opIndexForCell(ops, 'E1', empty('2031-03-14'))).toBe(1)
    expect(opIndexForCell(ops, 'E1', empty('2031-03-14'), { lines: true })).toBe(0)
    expect(opIndexForCell([day('Clear Day', '2031-03-14')], 'E1', empty('2031-03-14'), { lines: true })).toBe(-1)
  })

  it('a day a draft End empties is the End\'s, never a one-day change (whole-line actions only)', () => {
    const live = (date) => ({ date, shift_type: 'Day', assignment: 'SA-1' })
    const ops = [end('E1', '2031-03-20')]
    const ended = { E1: [{ key: 'SA-1', shift_type: 'Day', start_date: '2031-01-01', end_date: '2031-03-20' }] }
    expect(cellProposal(ops, ended, 'E1', live('2031-03-21'))).toEqual({
      index: 0,
      shift_type: null,
      label: 'Proposed: no shift',
    })
    expect(isOneDayOp(ops[0])).toBe(false)
    expect(isOneDayOp(day('Swap Day', '2031-03-14', 'Night'))).toBe(true)
  })

  it('lists the one-day changes a line carries, not another line\'s', () => {
    const other = { ...assign('E1', '2031-03-31', '2031-04-04', 'Morning') }
    const ops = [
      line,
      day('Clear Day', '2031-03-14'),
      day('Swap Day', '2031-03-21', 'Night'),
      other,
      day('Clear Day', '2031-03-31'),
      { ...day('Clear Day', '2031-03-17'), employee: 'E2' },
    ]
    expect(dayOpsOnLine(ops, 0)).toEqual([1, 2])
    expect(dayOpsOnLine(ops, 3)).toEqual([4])
    expect(dayOpsOnLine(ops, 1)).toEqual([])
    expect(dayOpsOnLine(ops, 9)).toEqual([])
  })

  it('a Change carries no one-day changes: the live shift still holds those days', () => {
    const change = { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2031-03-10', to_date: null, assignment: 'HR-SA-1' }
    expect(dayOpsOnLine([change, day('Clear Day', '2031-03-14')], 0)).toEqual([])
  })

  it('a line covers its own From..To; a Change from its date on', () => {
    expect(lineCovers(line, '2031-03-03')).toBe(true)
    expect(lineCovers(line, '2031-03-28')).toBe(true)
    expect(lineCovers(line, '2031-03-29')).toBe(false)
    expect(lineCovers({ ...line, to_date: '2031-03-10' }, '2031-03-14')).toBe(false)
    expect(lineCovers(assign('E1', '2031-03-03', null), '2031-12-31')).toBe(true)
    expect(lineCovers(change('E1', '2031-03-10', null), '2031-03-14')).toBe(true)
    expect(lineCovers(end('E1', '2031-03-10'), '2031-03-14')).toBe(false)
  })
})
