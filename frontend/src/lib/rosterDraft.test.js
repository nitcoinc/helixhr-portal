import { describe, expect, it } from 'vitest'
import {
  AFTER_END,
  CHANGE_ON_START,
  END_BEFORE_START,
  NO_SHIFT_ON_DATE,
  NO_SHIFT_TYPE,
  NO_WORKING_DAYS,
  OFF_DAYS_UNKNOWN,
  OVERLAP,
  SKIP_SPAN,
  opDate,
  simulate,
} from './rosterDraft'

// --- Shared literal case table (keep identical to helixhr/tests/test_roster_ops.py) ---
// coverage / expect: { employee: [[key, shift_type, start_date, end_date]] }
// problems: [[op index, message]]
// off_days (optional, default {} = every day unknown): { employee: { from, to, dates } | null }
// steps (optional): per op, [[action, key, shift_type, start_date, end_date]]
const WEEKENDS = [
  '2026-11-01',
  '2026-11-07',
  '2026-11-08',
  '2026-11-14',
  '2026-11-15',
  '2026-11-21',
  '2026-11-22',
  '2026-11-28',
  '2026-11-29',
  '2026-12-05',
  '2026-12-06',
  '2026-12-12',
  '2026-12-13',
  '2026-12-19',
  '2026-12-20',
  '2026-12-26',
  '2026-12-27',
]
const CASES = [
  {
    name: 'assign on an empty range succeeds',
    coverage: {},
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-08' },
    ],
    expect: { E1: [['new-0-0', 'Day', '2026-11-02', '2026-11-08']] },
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
        ['new-0-0', 'Night', '2026-11-10', '2026-11-30'],
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
        ['new-0-0', 'Day', '2026-11-02', '2026-11-09'],
        ['new-1-0', 'Night', '2026-11-10', '2026-11-20'],
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
    expect: { E1: [['new-0-0', 'Day', '2026-11-02', '2026-11-30']] },
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
        ['new-0-0', 'Night', '2026-11-10', null],
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
    name: 'a change whose assignment is gone falls back to the one covering the date',
    coverage: { E1: [['SA-2', 'Day', '2026-11-01', '2026-11-30']] },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-10', assignment: 'SA-1' },
    ],
    expect: {
      E1: [
        ['SA-2', 'Day', '2026-11-01', '2026-11-09'],
        ['new-0-0', 'Night', '2026-11-10', '2026-11-30'],
      ],
    },
    problems: [],
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
      E2: [['new-1-0', 'Night', '2026-11-05', '2026-11-05']],
    },
    problems: [[0, OVERLAP]],
  },
  {
    name: 'skip: a four-week assign with weekends off becomes four blocks',
    coverage: {},
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-27', skip_off_days: 1 }
    ],
    expect: {
      E1: [
        ['new-0-0', 'Day', '2026-11-02', '2026-11-06'],
        ['new-0-1', 'Day', '2026-11-09', '2026-11-13'],
        ['new-0-2', 'Day', '2026-11-16', '2026-11-20'],
        ['new-0-3', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    problems: [],
    steps: [
      [
        ['create', 'new-0-0', 'Day', '2026-11-02', '2026-11-06'],
        ['create', 'new-0-1', 'Day', '2026-11-09', '2026-11-13'],
        ['create', 'new-0-2', 'Day', '2026-11-16', '2026-11-20'],
        ['create', 'new-0-3', 'Day', '2026-11-23', '2026-11-27']
      ]
    ]
  },
  {
    name: 'skip: a holiday splits the block around it',
    coverage: {},
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: [...WEEKENDS, '2026-11-12'] }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-27', skip_off_days: 1 }
    ],
    expect: {
      E1: [
        ['new-0-0', 'Day', '2026-11-02', '2026-11-06'],
        ['new-0-1', 'Day', '2026-11-09', '2026-11-11'],
        ['new-0-2', 'Day', '2026-11-13', '2026-11-13'],
        ['new-0-3', 'Day', '2026-11-16', '2026-11-20'],
        ['new-0-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    problems: []
  },
  {
    name: 'skip: a range starting and ending on off days is trimmed',
    coverage: {},
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-01', to_date: '2026-11-08', skip_off_days: 1 }
    ],
    expect: {
      E1: [
        ['new-0-0', 'Day', '2026-11-02', '2026-11-06']
      ]
    },
    problems: []
  },
  {
    name: 'skip: only off days is refused',
    coverage: {},
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-07', to_date: '2026-11-08', skip_off_days: 1 }
    ],
    expect: {},
    problems: [
      [0, NO_WORKING_DAYS]
    ]
  },
  {
    name: 'skip: unknown off days is refused, without skip one block',
    coverage: {},
    off_days: {
      E1: null
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-27', skip_off_days: 1 },
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-27' }
    ],
    expect: {
      E1: [
        ['new-1-0', 'Day', '2026-11-02', '2026-11-27']
      ]
    },
    problems: [
      [0, OFF_DAYS_UNKNOWN]
    ]
  },
  {
    name: 'skip: a holiday list ending inside the range is unknown',
    coverage: {},
    off_days: {
      E1: { from: '2026-11-01', to: '2026-11-20', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-27', skip_off_days: 1 }
    ],
    expect: {},
    problems: [
      [0, OFF_DAYS_UNKNOWN]
    ]
  },
  {
    name: 'skip: more than 92 days is refused',
    coverage: {},
    off_days: {
      E1: { from: '2026-11-01', to: '2027-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2027-02-02', skip_off_days: 1 }
    ],
    expect: {},
    problems: [
      [0, SKIP_SPAN]
    ]
  },
  {
    name: 'skip is ignored on an open-ended assign, a change and an end',
    coverage: {
      E2: [
        ['SA-9', 'Day', '2026-11-02', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS },
      E2: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', skip_off_days: 1 },
      { employee: 'E2', action: 'Change', shift_type: 'Night', from_date: '2026-11-10', skip_off_days: 1 },
      { employee: 'E2', action: 'End', to_date: '2026-11-20', skip_off_days: 1 }
    ],
    expect: {
      E1: [
        ['new-0-0', 'Day', '2026-11-02', null]
      ],
      E2: [
        ['SA-9', 'Day', '2026-11-02', '2026-11-09'],
        ['new-1-0', 'Night', '2026-11-10', '2026-11-20']
      ]
    },
    problems: []
  },
  {
    name: 'run: change from mid-block splits it and swaps every later block',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-18', assignment: 'SA-3' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-17'],
        ['new-0-0', 'Night', '2026-11-18', '2026-11-20'],
        ['new-0-1', 'Night', '2026-11-23', '2026-11-27']
      ]
    },
    problems: [],
    steps: [
      [
        ['end', 'SA-3', 'Day', '2026-11-16', '2026-11-17'],
        ['cancel', 'SA-4', 'Day', '2026-11-23', '2026-11-27'],
        ['create', 'new-0-0', 'Night', '2026-11-18', '2026-11-20'],
        ['create', 'new-0-1', 'Night', '2026-11-23', '2026-11-27']
      ]
    ]
  },
  {
    name: 'run: change dated on an off-day gap acts on the next block on',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-22' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['new-0-0', 'Night', '2026-11-23', '2026-11-27']
      ]
    },
    problems: [],
    steps: [
      [
        ['cancel', 'SA-4', 'Day', '2026-11-23', '2026-11-27'],
        ['create', 'new-0-0', 'Night', '2026-11-23', '2026-11-27']
      ]
    ]
  },
  {
    name: 'run: change on a later block\'s first day recreates it',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-16', assignment: 'SA-3' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['new-0-0', 'Night', '2026-11-16', '2026-11-20'],
        ['new-0-1', 'Night', '2026-11-23', '2026-11-27']
      ]
    },
    problems: []
  },
  {
    name: 'run: change on the run\'s first day is still refused',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-02', assignment: 'SA-1' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    problems: [
      [0, CHANGE_ON_START]
    ]
  },
  {
    name: 'run: end mid-block ends it and cancels every later block',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'End', to_date: '2026-11-18', assignment: 'SA-3' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-18']
      ]
    },
    problems: [],
    steps: [
      [
        ['end', 'SA-3', 'Day', '2026-11-16', '2026-11-18'],
        ['cancel', 'SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    ]
  },
  {
    name: 'run: end dated on a gap cancels the later blocks',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'End', to_date: '2026-11-22' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20']
      ]
    },
    problems: [],
    steps: [
      [
        ['cancel', 'SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    ]
  },
  {
    name: 'run: another shift type breaks the run',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Late', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'Change', shift_type: 'Night', from_date: '2026-11-11', assignment: 'SA-2' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-10'],
        ['SA-3', 'Late', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27'],
        ['new-0-0', 'Night', '2026-11-11', '2026-11-13']
      ]
    },
    problems: []
  },
  {
    name: 'run: a working-day gap breaks the run',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-12-31', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'End', to_date: '2026-11-04', assignment: 'SA-1' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-04'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20']
      ]
    },
    problems: []
  },
  {
    name: 'run: unknown off days act on the covering block only',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: null
    },
    ops: [
      { employee: 'E1', action: 'End', to_date: '2026-11-18', assignment: 'SA-3' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-18'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    problems: []
  },
  {
    name: 'run: a gap day outside the known range breaks the run',
    coverage: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-13'],
        ['SA-3', 'Day', '2026-11-16', '2026-11-20'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    off_days: {
      E1: { from: '2026-11-01', to: '2026-11-20', dates: WEEKENDS }
    },
    ops: [
      { employee: 'E1', action: 'End', to_date: '2026-11-11', assignment: 'SA-2' }
    ],
    expect: {
      E1: [
        ['SA-1', 'Day', '2026-11-02', '2026-11-06'],
        ['SA-2', 'Day', '2026-11-09', '2026-11-11'],
        ['SA-4', 'Day', '2026-11-23', '2026-11-27']
      ]
    },
    problems: []
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
    const { coverage, problems, steps } = simulate(coverageIn(c.coverage), c.ops, c.off_days ?? {})
    expect(coverageOut(coverage)).toEqual(c.expect)
    expect(problems.map((p) => [p.index, p.message])).toEqual(c.problems)
    if (c.steps) {
      expect(
        steps.map((opSteps) =>
          opSteps.map((s) => [s.action, s.key, s.shift_type, s.start_date, s.end_date]),
        ),
      ).toEqual(c.steps)
    }
  })
})

describe('simulate', () => {
  it('ignores skip and follows no runs without off days (legacy requests)', () => {
    const { coverage, problems } = simulate({}, [
      { employee: 'E1', action: 'Assign', shift_type: 'Day', from_date: '2026-11-02', to_date: '2026-11-27', skip_off_days: 1 },
    ])
    expect(problems).toEqual([])
    expect(coverageOut(coverage)).toEqual({ E1: [['new-0-0', 'Day', '2026-11-02', '2026-11-27']] })
  })

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
