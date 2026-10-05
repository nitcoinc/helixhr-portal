import { describe, expect, it } from 'vitest'

import { matchPreset, presetRange } from './datePresets'

describe('presetRange', () => {
  it('last month crosses the year boundary', () => {
    expect(presetRange('last_month', '2026-01-15')).toEqual({ from_date: '2025-12-01', to_date: '2025-12-31' })
  })

  it('this quarter on 2026-05-20 is April to June', () => {
    expect(presetRange('this_quarter', '2026-05-20')).toEqual({ from_date: '2026-04-01', to_date: '2026-06-30' })
  })

  it('last quarter from Q1 is the previous Q4', () => {
    expect(presetRange('last_quarter', '2026-02-10')).toEqual({ from_date: '2025-10-01', to_date: '2025-12-31' })
  })

  it('handles leap-year February', () => {
    expect(presetRange('this_month', '2028-02-03')).toEqual({ from_date: '2028-02-01', to_date: '2028-02-29' })
    expect(presetRange('last_month', '2027-03-31')).toEqual({ from_date: '2027-02-01', to_date: '2027-02-28' })
  })

  it('years', () => {
    expect(presetRange('this_year', '2026-07-04')).toEqual({ from_date: '2026-01-01', to_date: '2026-12-31' })
    expect(presetRange('last_year', '2026-07-04')).toEqual({ from_date: '2025-01-01', to_date: '2025-12-31' })
  })

  it('custom is not a range: the caller keeps its dates (plan 2026-10-05-001 U9)', () => {
    expect(presetRange('custom', '2026-01-15')).toBeNull()
  })

  it('refuses an unknown id or a malformed today', () => {
    expect(presetRange('nope', '2026-01-01')).toBeNull()
    expect(presetRange('this_month', '')).toBeNull()
  })
})

describe('matchPreset', () => {
  it('names the preset a range equals, else custom', () => {
    expect(matchPreset('2025-12-01', '2025-12-31', '2026-01-15')).toBe('last_month')
    expect(matchPreset('2025-12-02', '2025-12-31', '2026-01-15')).toBe('custom')
    expect(matchPreset('2026-01-01', '2026-12-31', '2026-01-15')).toBe('this_year')
  })
})
