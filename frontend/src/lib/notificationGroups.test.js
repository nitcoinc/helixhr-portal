import { describe, expect, it } from 'vitest'
import { bucketGroups, bucketOf, groupByRecord } from './notificationGroups'

// A Saturday, so Today, Yesterday, This week and Older all have room.
const TODAY = '2026-10-17'
const log = (name, creation, extra = {}) => ({
  name,
  creation: `${creation} 09:30:00.000000`,
  read: 0,
  document_type: 'HR Request',
  document_name: 'REQ-1',
  ...extra,
})
const dayOf = (row) => row.creation.slice(0, 10)

describe('bucketOf', () => {
  it('cuts the calendar into Today / Yesterday / This week / Older', () => {
    expect(bucketOf('2026-10-17', TODAY)).toBe('today')
    expect(bucketOf('2026-10-16', TODAY)).toBe('yesterday')
    expect(bucketOf('2026-10-12', TODAY)).toBe('week') // Monday of this week
    expect(bucketOf('2026-10-11', TODAY)).toBe('older') // last Sunday
  })

  it('has no This week on a Monday, and dates a future stamp as today', () => {
    expect(bucketOf('2026-10-11', '2026-10-12')).toBe('yesterday')
    expect(bucketOf('2026-10-10', '2026-10-12')).toBe('older')
    expect(bucketOf('2026-10-18', TODAY)).toBe('today')
    expect(bucketOf(null, TODAY)).toBe('older')
  })
})

describe('groupByRecord', () => {
  it('collapses every log about one record into its newest, counting unread', () => {
    const rows = [
      log('n6', '2026-10-17'),
      log('other', '2026-10-17', { document_name: 'REQ-2' }),
      ...['n5', 'n4', 'n3', 'n2'].map((name) => log(name, '2026-10-16')),
      log('n1', '2026-10-10', { read: 1 }),
    ]
    const groups = groupByRecord(rows)
    expect(groups.map((g) => g.latest.name)).toEqual(['n6', 'other'])
    expect(groups[0].earlier.map((row) => row.name)).toEqual(['n5', 'n4', 'n3', 'n2', 'n1'])
    expect(groups[0].unread).toBe(5)
    expect(groups[1].earlier).toEqual([])
  })

  it('keeps the same name on different doctypes apart, and record-less logs single', () => {
    const groups = groupByRecord([
      log('a', TODAY, { document_type: 'Timesheet', document_name: 'X' }),
      log('b', TODAY, { document_type: 'Leave Application', document_name: 'X' }),
      log('c', TODAY, { document_type: null, document_name: null }),
      log('d', TODAY, { document_type: null, document_name: null }),
    ])
    expect(groups).toHaveLength(4)
  })
})

describe('bucketGroups', () => {
  it('places a group under the day of its newest log and drops empty buckets', () => {
    const buckets = bucketGroups(
      [
        log('new', '2026-10-17'),
        log('mid', '2026-10-13', { document_name: 'REQ-2' }),
        log('old-of-new', '2026-09-01'),
        log('old', '2026-09-01', { document_name: 'REQ-3' }),
      ],
      TODAY,
      dayOf,
    )
    expect(buckets.map((b) => b.label)).toEqual(['Today', 'This week', 'Older'])
    expect(buckets[0].groups[0].earlier.map((row) => row.name)).toEqual(['old-of-new'])
    expect(buckets[2].groups.map((g) => g.latest.name)).toEqual(['old'])
  })
})
