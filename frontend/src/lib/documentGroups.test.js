import { describe, expect, it } from 'vitest'
import { groupDocuments } from './documentGroups'

const TODAY = '2026-10-05'
const doc = (name, published_on) => ({ name, published_on })

describe('groupDocuments', () => {
  it('buckets by day for 30 days, then month this year, then year', () => {
    const groups = groupDocuments(
      [
        doc('old-year', '2024-12-31'),
        doc('today', TODAY),
        doc('month', '2026-08-20'),
        doc('yesterday', '2026-10-04'),
        doc('recent', '2026-09-28'),
        doc('month-2', '2026-08-01'),
        doc('last-year', '2025-06-01'),
        doc('edge-out', '2026-09-05'),
        doc('edge-in', '2026-09-06'),
      ],
      TODAY,
    )
    expect(groups.map((g) => g.label)).toEqual([
      'Today',
      'Yesterday',
      expect.stringMatching(/28/),
      expect.stringMatching(/6/),
      'September 2026',
      'August 2026',
      '2025',
      '2024',
    ])
    expect(groups.find((g) => g.label === 'August 2026').rows.map((r) => r.name)).toEqual([
      'month',
      'month-2',
    ])
    expect(groups.find((g) => g.label === 'September 2026').rows.map((r) => r.name)).toEqual([
      'edge-out',
    ])
  })

  it('crosses a year boundary inside the 30-day window by day, not by year', () => {
    const groups = groupDocuments([doc('dec', '2026-12-20')], '2027-01-03')
    expect(groups[0].label).not.toBe('2026')
    expect(groups[0].key).toBe('2026-12-20')
  })

  it('keeps undated rows, last, and handles an empty list', () => {
    expect(groupDocuments([], TODAY)).toEqual([])
    const groups = groupDocuments([doc('none', null), doc('today', TODAY)], TODAY)
    expect(groups.map((g) => g.label)).toEqual(['Today', 'Undated'])
  })
})
