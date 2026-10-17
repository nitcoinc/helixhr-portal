import { describe, expect, it } from 'vitest'
import { RECENT_REPORTS_MAX, RECENT_REPORTS_PREFIX, readRecentReports, recordRecentReport } from './recentReports'

function memoryStorage() {
  const data = new Map()
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
  }
}

const throwing = {
  getItem() {
    throw new Error('SecurityError')
  },
  setItem() {
    throw new Error('QuotaExceededError')
  },
}

describe('recent reports', () => {
  it('starts empty and keeps the newest first, without duplicates', () => {
    const storage = memoryStorage()
    expect(readRecentReports('a@x', storage)).toEqual([])
    recordRecentReport('a@x', 'leave_ledger', storage)
    recordRecentReport('a@x', 'hr_request_aging', storage)
    expect(recordRecentReport('a@x', 'leave_ledger', storage)).toEqual(['leave_ledger', 'hr_request_aging'])
  })

  it('caps the list', () => {
    const storage = memoryStorage()
    for (let i = 0; i < RECENT_REPORTS_MAX + 3; i += 1) recordRecentReport('a@x', `r${i}`, storage)
    expect(readRecentReports('a@x', storage)).toHaveLength(RECENT_REPORTS_MAX)
    expect(readRecentReports('a@x', storage)[0]).toBe(`r${RECENT_REPORTS_MAX + 2}`)
  })

  it('keys by user, so one account never reads another', () => {
    const storage = memoryStorage()
    recordRecentReport('a@x', 'leave_ledger', storage)
    expect(readRecentReports('b@x', storage)).toEqual([])
    expect([...storage.data.keys()]).toEqual([`${RECENT_REPORTS_PREFIX}a@x`])
  })

  it('answers null with no user or with storage that throws', () => {
    expect(readRecentReports('', memoryStorage())).toBeNull()
    expect(readRecentReports('a@x', throwing)).toBeNull()
    expect(recordRecentReport('a@x', 'leave_ledger', throwing)).toBeNull()
  })

  it('answers null when the write alone fails', () => {
    const storage = { getItem: () => null, setItem: throwing.setItem }
    expect(recordRecentReport('a@x', 'leave_ledger', storage)).toBeNull()
  })

  it('ignores garbled or foreign values', () => {
    const storage = memoryStorage()
    storage.setItem(`${RECENT_REPORTS_PREFIX}a@x`, '{not json')
    expect(readRecentReports('a@x', storage)).toEqual([])
    storage.setItem(`${RECENT_REPORTS_PREFIX}a@x`, JSON.stringify(['leave_ledger', 3, null]))
    expect(readRecentReports('a@x', storage)).toEqual(['leave_ledger'])
  })
})
