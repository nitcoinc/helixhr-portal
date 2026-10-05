import { describe, expect, it } from 'vitest'
import { addMonths, isMonth, mondaysOfMonth, monthOf, weekCardStatus } from './month'

describe('mondaysOfMonth', () => {
  it('starts on the first when the month starts on a Monday', () => {
    // 1 June 2026 is a Monday.
    expect(mondaysOfMonth('2026-06')).toEqual([
      '2026-06-01',
      '2026-06-08',
      '2026-06-15',
      '2026-06-22',
      '2026-06-29',
    ])
  })

  it('reaches back into the previous month when it starts on a Sunday', () => {
    // 1 March 2026 is a Sunday: its week began on 23 February.
    const mondays = mondaysOfMonth('2026-03')
    expect(mondays[0]).toBe('2026-02-23')
    expect(mondays.at(-1)).toBe('2026-03-30')
    expect(mondays).toHaveLength(6)
  })

  it('handles February in a leap year', () => {
    // 2028 is a leap year; 29 Feb 2028 is a Tuesday.
    expect(mondaysOfMonth('2028-02')).toEqual([
      '2028-01-31',
      '2028-02-07',
      '2028-02-14',
      '2028-02-21',
      '2028-02-28',
    ])
  })
})

describe('month helpers', () => {
  it('rolls months across a year', () => {
    expect(addMonths('2026-12', 1)).toBe('2027-01')
    expect(addMonths('2026-01', -1)).toBe('2025-12')
  })

  it('validates YYYY-MM only', () => {
    expect(isMonth('2026-10')).toBe(true)
    for (const bad of ['2026-13', '2026-1', '2026-10-01', '', null]) expect(isMonth(bad)).toBe(false)
    expect(monthOf('2026-10-05')).toBe('2026-10')
  })

  it('names the two states a workflow_state cannot', () => {
    expect(weekCardStatus({ state: null }).label).toBe('Not started')
    expect(weekCardStatus({ state: 'Approved', change_open: true }).label).toBe('Change requested')
    expect(weekCardStatus({ state: 'Sent Back' }).label).toBe('Sent back')
    expect(weekCardStatus({ state: 'Approved' }).label).toBe('Approved')
  })
})
