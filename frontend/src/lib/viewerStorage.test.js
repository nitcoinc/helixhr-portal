import { describe, expect, it } from 'vitest'
import { NAV_COLLAPSED_PREFIX } from './navGroups'
import {
  APPROVALS_SHORTCUTS_PREFIX,
  PALETTE_RECENTS_PREFIX,
  RECENT_REPORTS_PREFIX,
  clearViewerStorage,
  purgeOtherViewers,
} from './viewerStorage'

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    data,
    get length() {
      return data.size
    },
    key: (index) => [...data.keys()][index] ?? null,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  }
}

const seeded = () =>
  memoryStorage({
    [`${NAV_COLLAPSED_PREFIX}a@x`]: '["hr"]',
    [`${NAV_COLLAPSED_PREFIX}b@x`]: '[]',
    [`${RECENT_REPORTS_PREFIX}a@x`]: '["leave_ledger"]',
    [`${RECENT_REPORTS_PREFIX}b@x`]: '["hr_request_aging"]',
    [`${PALETTE_RECENTS_PREFIX}a@x`]: '[{"type":"page","route":"/payslips","label":"Payslips"}]',
    [`${PALETTE_RECENTS_PREFIX}b@x`]: '[{"type":"person","route":"/directory?q=Ann","label":"Ann"}]',
    [`${APPROVALS_SHORTCUTS_PREFIX}a@x`]: '1',
    [`${APPROVALS_SHORTCUTS_PREFIX}b@x`]: '1',
    'helixhr.nav.collapsed': '["hr","admin"]',
    'frappe.something': 'kept',
  })

describe('viewer storage (KTD13)', () => {
  it('sign-out clears every viewer key and nothing else', () => {
    const storage = seeded()
    clearViewerStorage(storage)
    expect([...storage.data.keys()]).toEqual(['frappe.something'])
  })

  it("a bootstrap keeps only this user's keys, and drops the unkeyed legacy one", () => {
    const storage = seeded()
    purgeOtherViewers('a@x', storage)
    expect([...storage.data.keys()].sort()).toEqual(
      [
        'frappe.something',
        `${NAV_COLLAPSED_PREFIX}a@x`,
        `${RECENT_REPORTS_PREFIX}a@x`,
        `${PALETTE_RECENTS_PREFIX}a@x`,
        `${APPROVALS_SHORTCUTS_PREFIX}a@x`,
      ].sort(),
    )
  })

  it('a guest or expired session keeps none', () => {
    const storage = seeded()
    purgeOtherViewers(null, storage)
    expect([...storage.data.keys()]).toEqual(['frappe.something'])
  })

  it('never throws when storage does', () => {
    const throwing = {
      get length() {
        throw new Error('SecurityError')
      },
    }
    expect(() => clearViewerStorage(throwing)).not.toThrow()
    expect(() => purgeOtherViewers('a@x', throwing)).not.toThrow()
    expect(() => clearViewerStorage(null)).not.toThrow()
  })
})
