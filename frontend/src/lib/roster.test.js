import { describe, expect, it } from 'vitest'
import { isEmptyWeek, leaveLabel, rosterModes, shiftHours } from './roster'

describe('rosterModes', () => {
  const values = (session) => rosterModes(session).map((mode) => mode.value)

  it('offers an employee only their own row', () => {
    expect(values({ status: 'ready' })).toEqual(['mine'])
  })
  it('adds the team for someone with reports', () => {
    expect(values({ status: 'ready', hasReports: true })).toEqual(['mine', 'team'])
  })
  it('adds everyone for an admin scope', () => {
    expect(values({ status: 'ready', hasReports: true, canSeePeople: true })).toEqual([
      'mine',
      'team',
      'hr',
    ])
  })
  it('gives a desk-only session nothing but everyone', () => {
    expect(values({ status: 'desk-only', hasReports: true, canSeePeople: true })).toEqual(['hr'])
    expect(values({ status: 'desk-only' })).toEqual([])
  })
})

describe('shiftHours', () => {
  it('joins both ends', () => {
    expect(shiftHours({ start_time: '09:00', end_time: '17:00' })).toBe('09:00–17:00')
  })
  it('is empty without both ends', () => {
    expect(shiftHours({ start_time: '09:00', end_time: null })).toBe('')
    expect(shiftHours(null)).toBe('')
  })
})

describe('leaveLabel', () => {
  const row = {
    leaves: [{ start: '2026-09-29', end: '2026-09-30', leave_type: 'Casual Leave', waiting: true }],
  }
  it('names the leave covering the date', () => {
    expect(leaveLabel(row, '2026-09-30')).toBe('Casual Leave · waiting')
  })
  it('is empty outside it', () => {
    expect(leaveLabel(row, '2026-10-01')).toBe('')
    expect(leaveLabel({}, '2026-10-01')).toBe('')
  })
  it('marks a half day', () => {
    const half = { leaves: [{ start: 'a', end: 'z', leave_type: 'Sick', half_day: 1 }] }
    expect(leaveLabel(half, 'm')).toBe('Sick · half day')
  })
})

describe('isEmptyWeek', () => {
  it('is true with no shifts anywhere', () => {
    expect(isEmptyWeek([{ cells: [{ shift_type: null }] }])).toBe(true)
    expect(isEmptyWeek([])).toBe(true)
  })
  it('is false with one shift', () => {
    expect(isEmptyWeek([{ cells: [{ shift_type: null }, { shift_type: 'Day' }] }])).toBe(false)
  })
})
