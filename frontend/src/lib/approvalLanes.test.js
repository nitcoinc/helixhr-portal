// Plan 2026-10-15-002 U5: the Approvals page's lane helpers.
import { describe, expect, it } from 'vitest'
import {
  BULK_MAX_ITEMS,
  approveAllBatch,
  approveAllLabel,
  batchItem,
  batchLabel,
  decidedForTab,
  groupByPerson,
  hrLine,
  hrReasonLabel,
  isBatchable,
  isWithManager,
  laneSections,
  laneSubsections,
  readShortcuts,
  selectionState,
  showsHrChip,
  writeShortcuts,
} from './approvalLanes'
import { APPROVALS_SHORTCUTS_PREFIX } from './viewerStorage'

const row = (lane, name, extra = {}) => ({ lane, name, employee: `E-${name}`, kind: 'leave', ...extra })

describe('laneSections', () => {
  it('orders team, decided, hr with no it lane', () => {
    const lanes = laneSections([row('it', 'c'), row('hr', 'b'), row('team', 'a')], 2)
    expect(lanes.map((lane) => lane.key)).toEqual(['team', 'decided', 'hr'])
    expect(lanes.map((lane) => lane.title)).toEqual(['My team', 'Decided by me', 'HR'])
  })

  it('drops empty pending lanes', () => {
    expect(laneSections([row('team', 'a')], 0).map((lane) => lane.key)).toEqual(['team'])
    expect(laneSections([], 0)).toEqual([])
  })

  it('keeps Decided, first, when My team is empty', () => {
    expect(laneSections([row('hr', 'b')], 1).map((lane) => lane.key)).toEqual(['decided', 'hr'])
    expect(laneSections([], 3).map((lane) => lane.key)).toEqual(['decided'])
  })

  it('treats an untagged row as the caller\'s team', () => {
    expect(laneSections([{ name: 'x', kind: 'leave' }])[0].key).toBe('team')
  })

  it('keeps change rows first within a lane, then flagged, then normal', () => {
    const [lane] = laneSections([
      row('team', 'n'),
      row('team', 'f', { needs_look: true }),
      row('team', 'c', { kind: 'change' }),
    ])
    expect(lane.sections.map((section) => section.key)).toEqual(['change', 'flagged', 'normal'])
    expect(lane.sections[0].rows.map((r) => r.name)).toEqual(['c'])
  })

  it('puts a roster request with the change requests, never in Looks normal', () => {
    const [lane] = laneSections([row('hr', 'n'), row('hr', 'r', { kind: 'roster' })])
    expect(lane.key).toBe('hr')
    expect(lane.sections.map((section) => section.key)).toEqual(['change', 'normal'])
    expect(lane.sections[0].rows.map((r) => r.name)).toEqual(['r'])
    expect(isBatchable({ kind: 'roster', needs_look: false })).toBe(false)
  })
})

// Plan 2026-10-31-001 U13 (R22, R23): a manager-stage leave HR sees is never
// HR's work in the words or the grouping.
describe('manager-stage leave in HR\'s queue', () => {
  const hrRow = (name, extra = {}) => row('hr', name, { for_hr: true, ...extra })

  it('puts stalled rows after HR and the With managers group last, one section', () => {
    const lanes = laneSections(
      [
        hrRow('w', { hr_reason: 'with_manager', approver_name: 'Arjun Mehta' }),
        hrRow('o', { hr_reason: 'overdue', approver_name: 'Arjun Mehta' }),
        hrRow('h'),
        row('team', 't'),
      ],
      1,
    )
    expect(lanes.map((lane) => lane.key)).toEqual(['team', 'decided', 'hr', 'stalled', 'with-managers'])
    const byKey = Object.fromEntries(lanes.map((lane) => [lane.key, lane]))
    expect(byKey.hr.rows.map((r) => r.name)).toEqual(['h'])
    expect(byKey.stalled.rows.map((r) => r.name)).toEqual(['o'])
    expect(byKey['with-managers'].title).toBe('With managers')
    expect(byKey['with-managers'].sections).toEqual([
      { key: 'with-managers', title: '', note: '', rows: [byKey['with-managers'].rows[0]] },
    ])
  })

  it('leaves no HR lane when HR only has rows waiting on managers', () => {
    const lanes = laneSections([hrRow('a', { hr_reason: 'approver_away' }), hrRow('w', { hr_reason: 'with_manager' })])
    expect(lanes.map((lane) => lane.key)).toEqual(['stalled', 'with-managers'])
  })

  it('names the approver, never HR', () => {
    expect(hrReasonLabel({ hr_reason: 'with_manager', approver_name: 'Arjun Mehta' })).toBe('Waiting on Arjun Mehta')
    expect(hrReasonLabel({ hr_reason: 'with_manager', approver_name: null })).toBe('No approver set')
    expect(hrReasonLabel({ hr_reason: 'approver_away', approver_name: 'Arjun Mehta' })).toBe(
      'Approver on leave · Arjun Mehta',
    )
    expect(hrReasonLabel({ hr_reason: 'overdue', approver_name: 'Arjun Mehta' })).toBe('Overdue with Arjun Mehta')
    expect(hrReasonLabel({ hr_reason: 'overdue', approver_name: null })).toBe('Overdue · No approver set')
    expect(hrReasonLabel({ hr_reason: null })).toBe('')
  })

  it('keeps the HR chip for HR-stage work only', () => {
    expect(showsHrChip(hrRow('h'))).toBe(true)
    expect(showsHrChip(hrRow('o', { hr_reason: 'overdue' }))).toBe(false)
    expect(showsHrChip(hrRow('w', { hr_reason: 'with_manager' }))).toBe(false)
    expect(showsHrChip(row('team', 't'))).toBe(false)
  })

  it('tells With managers rows apart', () => {
    expect(isWithManager({ hr_reason: 'with_manager' })).toBe(true)
    expect(isWithManager({ hr_reason: 'overdue' })).toBe(false)
    expect(isWithManager(null)).toBe(false)
  })
})

describe('hrLine', () => {
  it('joins why HR sees a row, who sent it and their note', () => {
    expect(hrLine({ for_hr: true, sent_to_hr_by: 'Arjun', hr_note: 'policy check' })).toBe(
      'Sent by Arjun · “policy check”',
    )
    expect(hrLine({ for_hr: true, hr_reason: 'overdue', approver_name: 'Arjun' })).toBe('Overdue with Arjun')
  })

  it('names who a manager-stage detail is waiting on', () => {
    expect(hrLine({ for_hr: false, waiting_on: 'Arjun Mehta' })).toBe('Waiting on Arjun Mehta')
    expect(hrLine({ for_hr: false, waiting_on: null })).toBe('')
    expect(hrLine(null)).toBe('')
  })
})

describe('decidedForTab', () => {
  const decided = [
    { name: 'L-1', kind: 'leave' },
    { name: 'TS-1', kind: 'timesheet' },
    { name: 'AR-1', kind: 'attendance' },
    { name: 'CH-1', kind: 'change' },
  ]
  const names = (rows) => rows.map((entry) => entry.name)

  it('keeps the unsearched receipt on Leave & attendance only, whole', () => {
    expect(names(decidedForTab(decided, 'queue', false))).toEqual(['L-1', 'TS-1', 'AR-1', 'CH-1'])
    expect(decidedForTab(decided, 'timesheets', false)).toEqual([])
  })

  it('splits a search by tab: timesheets on Timesheets, the rest on Leave & attendance', () => {
    expect(names(decidedForTab(decided, 'timesheets', true))).toEqual(['TS-1'])
    expect(names(decidedForTab(decided, 'queue', true))).toEqual(['L-1', 'AR-1', 'CH-1'])
  })

  it('answers an empty list for no rows', () => {
    expect(decidedForTab(undefined, 'queue', false)).toEqual([])
    expect(decidedForTab(null, 'timesheets', true)).toEqual([])
  })
})

describe('laneSubsections', () => {
  it('drops empty sub-sections', () => {
    expect(laneSubsections([row('team', 'n')]).map((section) => section.key)).toEqual(['normal'])
  })
})

describe('groupByPerson', () => {
  it('groups by employee in first-seen order, oldest row leading', () => {
    const rows = [
      { employee: 'E-1', employee_name: 'Alice', name: 'a1' },
      { employee: 'E-2', employee_name: 'Bob', name: 'b1' },
      { employee: 'E-1', employee_name: 'Alice', name: 'a2' },
    ]
    const groups = groupByPerson(rows)
    expect(groups.map((group) => group.employee)).toEqual(['E-1', 'E-2'])
    expect(groups[0].rows.map((r) => r.name)).toEqual(['a1', 'a2'])
    expect(groups[0].oldest.name).toBe('a1')
  })
})

// Plan 2026-10-17-001 U7: Approve all, select-all and the keyboard opt-in.
const clean = (name, kind = 'timesheet', extra = {}) => ({ name, kind, needs_look: false, ...extra })

describe('isBatchable', () => {
  it('takes clean timesheets and leave only', () => {
    expect(isBatchable(clean('t'))).toBe(true)
    expect(isBatchable(clean('l', 'leave'))).toBe(true)
    // approve_clean_items refuses these, so they never join a batch.
    expect(isBatchable(clean('a', 'attendance'))).toBe(false)
    expect(isBatchable(clean('c', 'change'))).toBe(false)
    expect(isBatchable(clean('f', 'timesheet', { needs_look: true }))).toBe(false)
    expect(isBatchable(null)).toBe(false)
  })

  it('takes only rows the server called clean, not rows it never flagged', () => {
    // An HR-lane timesheet or stalled leave: no flags, no token.
    expect(isBatchable(clean('h', 'timesheet', { needs_look: null }))).toBe(false)
    expect(isBatchable({ name: 'u', kind: 'leave' })).toBe(false)
    expect(isBatchable(clean('t', 'timesheet', { needs_look: false }))).toBe(true)
  })

  it('leaves out a row its approver cannot act on', () => {
    expect(isBatchable(clean('b', 'timesheet', { can_act: false }))).toBe(false)
    expect(isBatchable(clean('k', 'timesheet', { can_act: true }))).toBe(true)
  })
})

describe('approveAllBatch', () => {
  it('keeps eligible rows in their oldest-first order', () => {
    const batch = approveAllBatch([clean('t1'), clean('a', 'attendance'), clean('l1', 'leave'), clean('f', 'leave', { needs_look: true })])
    expect(batch.rows.map((row) => row.name)).toEqual(['t1', 'l1'])
    expect(batch.eligible).toBe(2)
    expect(approveAllLabel(batch)).toBe('Approve all 2 that look normal')
  })

  it('cuts at the server limit and says so', () => {
    const rows = Array.from({ length: 75 }, (_, index) => clean(`t${index}`))
    const batch = approveAllBatch(rows)
    expect(BULK_MAX_ITEMS).toBe(60)
    expect(batch.rows).toHaveLength(60)
    expect(batch.rows[0].name).toBe('t0')
    expect(batch.rows[59].name).toBe('t59')
    expect(approveAllLabel(batch)).toBe('Approve the oldest 60 of 75')
  })

  it('has no label when nothing is eligible', () => {
    expect(approveAllLabel(approveAllBatch([clean('a', 'attendance')]))).toBe('')
    expect(approveAllLabel(approveAllBatch(undefined))).toBe('')
  })
})

describe('batchItem and batchLabel', () => {
  it('carries the row token as the expected state', () => {
    expect(batchItem({ doctype: 'Timesheet', name: 'TS-1', token_modified: 'm', status: 'Pending Approval' })).toEqual({
      doctype: 'Timesheet',
      name: 'TS-1',
      expected_modified: 'm',
      expected_state: 'Pending Approval',
    })
  })

  it('sums hours and leave days', () => {
    expect(
      batchLabel([
        clean('t1', 'timesheet', { total_hours: 40 }),
        clean('t2', 'timesheet', { total_hours: 37.5 }),
        clean('l1', 'leave', { total_days: 2 }),
        clean('l2', 'leave', { total_days: 0.5 }),
      ]),
    ).toBe('Approve 4 items · 77.5 h · 2.5 days leave')
    expect(batchLabel([clean('l1', 'leave', { total_days: 1 })])).toBe('Approve 1 item · 1 day leave')
  })
})

describe('selectionState', () => {
  const rows = [clean('t1'), clean('t2'), clean('a', 'attendance')]
  it('reads none, some and all over the batchable rows only', () => {
    expect(selectionState(rows, [])).toBe('none')
    expect(selectionState(rows, ['t1'])).toBe('some')
    expect(selectionState(rows, ['t1', 't2'])).toBe('all')
    expect(selectionState([clean('a', 'attendance')], ['a'])).toBe('none')
  })
})

describe('keyboard shortcuts flag', () => {
  const memory = () => {
    const data = {}
    return {
      getItem: (key) => (key in data ? data[key] : null),
      setItem: (key, value) => {
        data[key] = String(value)
      },
      removeItem: (key) => {
        delete data[key]
      },
      data,
    }
  }

  it('is off by default and keyed by user', () => {
    const storage = memory()
    expect(readShortcuts('a@x', storage)).toBe(false)
    expect(writeShortcuts('a@x', true, storage)).toBe(true)
    expect(storage.data[`${APPROVALS_SHORTCUTS_PREFIX}a@x`]).toBe('1')
    expect(readShortcuts('a@x', storage)).toBe(true)
    expect(readShortcuts('b@x', storage)).toBe(false)
    writeShortcuts('a@x', false, storage)
    expect(readShortcuts('a@x', storage)).toBe(false)
  })

  it('stays off with no user or a throwing storage', () => {
    const broken = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    }
    expect(readShortcuts('', memory())).toBe(false)
    expect(readShortcuts('a@x', broken)).toBe(false)
    expect(writeShortcuts('a@x', true, broken)).toBe(false)
    expect(writeShortcuts('', true, memory())).toBe(false)
  })
})
