import { describe, expect, it } from 'vitest'
import {
  AFTER_END,
  CHANGE_ON_START,
  END_BEFORE_START,
  NO_SHIFT_ON_DATE,
  NO_SHIFT_TYPE,
  OVERLAP,
  opDate,
  simulate,
} from './rosterDraft'

// --- Shared literal case table (keep identical to helixhr/tests/test_roster_ops.py) ---
// coverage / expect: { employee: [[key, shift_type, start_date, end_date]] }
// problems: [[op index, message]]
const CASES = [
  {
    name: 'assign on an empty range succeeds',
    coverage: {},
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-08' },
    ],
    expect: { E1: [['new-0', 'Day', '2026-11-02', '2026-11-08']] },
    problems: [],
  },
  {
    name: 'assign overlapping existing coverage is refused',
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', null]] },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Night', from_date: '2026-11-05', to_date: '2026-11-06' },
    ],
    expect: { E1: [['SA-1', 'Day', '2026-11-01', null]] },
    problems: [[0, OVERLAP]],
  },
  {
    name: 'change inside an assignment splits it and keeps the old end',
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-30']] },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-10', assignment: 'SA-1' },
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-01', '2026-11-09'],
        ['new-0', 'Night', '2026-11-10', '2026-11-30'],
      ],
    },
    problems: [],
  },
  {
    name: "change on the assignment's first day is refused",
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-30']] },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-01', assignment: 'SA-1' },
    ],
    expect: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-30']] },
    problems: [[0, CHANGE_ON_START]],
  },
  {
    name: 'change after the assignment ends is refused',
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-10']] },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-15', assignment: 'SA-1' },
    ],
    expect: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-10']] },
    problems: [[0, AFTER_END]],
  },
  {
    name: 'end before the start is refused',
    coverage: { E1: [['SA-1', 'Day', '2026-11-05', null]] },
    ops: [{ employee: 'E1', action: 'End', to_date: '2026-11-03', assignment: 'SA-1' }],
    expect: { E1: [['SA-1', 'Day', '2026-11-05', null]] },
    problems: [[0, END_BEFORE_START]],
  },
  {
    name: 'end after the current end is refused',
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-10']] },
    ops: [{ employee: 'E1', action: 'End', to_date: '2026-11-15', assignment: 'SA-1' }],
    expect: { E1: [['SA-1', 'Day', '2026-11-01', '2026-11-10']] },
    problems: [[0, AFTER_END]],
  },
  {
    name: 'assign a range then change inside it combines',
    coverage: {},
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-20' },
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-10' },
    ],
    expect: {
      E1: [
        ['new-0', 'Day', '2026-11-02', '2026-11-09'],
        ['new-1', 'Night', '2026-11-10', '2026-11-20'],
      ],
    },
    problems: [],
  },
  {
    name: 'an open-ended assignment followed by an end is bounded',
    coverage: {},
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02' },
      { employee: 'E1', action: 'End', to_date: '2026-11-30' },
    ],
    expect: { E1: [['new-0', 'Day', '2026-11-02', '2026-11-30']] },
    problems: [],
  },
  {
    name: 'change on an open-ended assignment stays open-ended',
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', null]] },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-10', assignment: 'SA-1' },
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-01', '2026-11-09'],
        ['new-0', 'Night', '2026-11-10', null],
      ],
    },
    problems: [],
  },
  {
    name: 'change with no shift on that date is refused',
    coverage: {},
    ops: [{ employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-10' }],
    expect: {},
    problems: [[0, NO_SHIFT_ON_DATE]],
  },
  {
    name: 'assign without a shift is refused',
    coverage: {},
    ops: [{ employee: 'E1', action: 'Assign', from_date: '2026-11-02' }],
    expect: {},
    problems: [[0, NO_SHIFT_TYPE]],
  },
  {
    name: 'a refused operation is skipped and later ones still apply',
    coverage: { E1: [['SA-1', 'Day', '2026-11-01', null]] },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Night', from_date: '2026-11-05' },
      { employee: 'E2', action: 'Assign', shift_type: 'Night', from_date: '2026-11-05', to_date: '2026-11-05' },
    ],
    expect: {
      E1: [['SA-1', 'Day', '2026-11-01', null]],
      E2: [['new-1', 'Night', '2026-11-05', '2026-11-05']],
    },
    problems: [[0, OVERLAP]],
  },
]

const coverageIn = (table) =>
  Object.fromEntries(
    Object.entries(table).map(([employee, rows]) => [
      employee,
      rows.map(([key, shift_type, start_date, end_date]) => ({ key, shift_type, start_date, end_date })),
    ]),
  )

const coverageOut = (coverage) =>
  Object.fromEntries(
    Object.entries(coverage)
      .filter(([, rows]) => rows.length)
      .map(([employee, rows]) => [
        employee,
        rows.map((a) => [a.key, a.shift_type, a.start_date, a.end_date]),
      ]),
  )

describe('simulate (shared cases)', () => {
  it.each(CASES.map((c) => [c.name, c]))('%s', (_name, c) => {
    const { coverage, problems } = simulate(coverageIn(c.coverage), c.ops)
    expect(coverageOut(coverage)).toEqual(c.expect)
    expect(problems.map((p) => [p.index, p.message])).toEqual(c.problems)
  })
})

describe('simulate', () => {
  it('does not mutate the input coverage', () => {
    const original = coverageIn({ E1: [['SA-1', 'Day', '2026-11-01', null]] })
    simulate(original, [{ employee: 'E1', action: 'End', to_date: '2026-11-30', assignment: 'SA-1' }])
    expect(original.E1[0].end_date).toBeNull()
  })

  it('names the employee on a problem', () => {
    const { problems } = simulate({}, [
      { employee: 'E9', action: 'Change', shift_type: 'Day', from_date: '2026-11-10' },
    ])
    expect(problems[0].employee).toBe('E9')
  })

  it('crosses a month boundary when a change splits', () => {
    const { coverage } = simulate(coverageIn({ E1: [['SA-1', 'Day', '2026-10-01', null]] }), [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-12-01', assignment: 'SA-1' },
    ])
    expect(coverage.E1[0].end_date).toBe('2026-11-30')
  })
})

describe('opDate', () => {
  it('is the end date for End and the from date otherwise', () => {
    expect(opDate({ action: 'End', from_date: '2026-11-01', to_date: '2026-11-09' })).toBe('2026-11-09')
    expect(opDate({ action: 'Change', from_date: '2026-11-01' })).toBe('2026-11-01')
    expect(opDate({ action: 'Assign', from_date: '2026-02-30' })).toBeNull()
  })
})
