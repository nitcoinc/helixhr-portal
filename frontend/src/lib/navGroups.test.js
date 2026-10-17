import { afterEach, describe, expect, it } from 'vitest'
import { allowedNav } from './nav'
import {
  NAV_COLLAPSED_PREFIX,
  NAV_GROUPS,
  defaultCollapsed,
  groupNav,
  railGroups,
  readCollapsed,
  workGroup,
  writeCollapsed,
} from './navGroups'

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = String(value)
    },
    data,
  }
}

const throwing = {
  getItem() {
    throw new Error('denied')
  },
  setItem() {
    throw new Error('denied')
  },
}

describe('groupNav', () => {
  const items = [
    { label: 'Home', group: 'pinned' },
    { label: 'Leave', group: 'work' },
    { label: 'People', group: 'hr' },
    { label: 'Timesheet', group: 'work' },
    { label: 'Profile', group: 'bottom' },
  ]

  it('orders groups by NAV_GROUPS and keeps item order inside a group', () => {
    const groups = groupNav(items)
    expect(groups.map((g) => g.id)).toEqual(['pinned', 'work', 'hr', 'bottom'])
    expect(groups[1].items.map((i) => i.label)).toEqual(['Leave', 'Timesheet'])
  })

  it('drops a group whose items were all gated away', () => {
    const employee = items.filter((item) => item.group !== 'hr')
    expect(groupNav(employee).some((g) => g.id === 'hr' || g.id === 'admin')).toBe(false)
  })

  it('marks only HR and Admin collapsible for everyone', () => {
    expect(NAV_GROUPS.filter((g) => g.collapsible).map((g) => g.id)).toEqual(['hr', 'admin'])
  })
})

// Plan 2026-10-17-001 U4 (R8, HR-06, MH-12, AI-15). Replaces the old single
// assertion "nothing remembered -> every collapsible group collapsed" with
// the per-identity rule, case by case: the session's own work group sits
// under the pinned items and starts open; every other collapsible group
// starts closed; an employee's rail has nothing to collapse.
describe('the rail per identity', () => {
  const HR = {
    status: 'ready',
    canApprove: true,
    canConfigure: true,
    canSeeOrganisation: true,
    canSeePeople: true,
    canSeeProjects: true,
    canRunReports: true,
  }
  const IDENTITIES = [
    ['employee', { status: 'ready' }, null, ['pinned', 'work', 'pay', 'people', 'bottom'], []],
    ['manager', { status: 'ready', canApprove: true, hasReports: true }, null, ['pinned', 'work', 'pay', 'people', 'bottom'], []],
    ['hr (HR Manager + Settings)', HR, 'hr', ['pinned', 'hr', 'work', 'pay', 'people', 'admin', 'bottom'], ['pay', 'admin']],
    [
      'System Manager (HR + Admin)',
      { ...HR, canAdminPortal: true, canEditEmailTemplates: true },
      'hr',
      ['pinned', 'hr', 'work', 'pay', 'people', 'admin', 'bottom'],
      ['pay', 'admin'],
    ],
    [
      'Portal Admin',
      { status: 'ready', canAdminPortal: true, canEditEmailTemplates: true },
      'admin',
      ['pinned', 'admin', 'work', 'pay', 'people', 'hr', 'bottom'],
      ['pay', 'hr'],
    ],
    ['HR User', { status: 'ready', canOverseeTimesheets: true, canSeePeople: true }, 'hr', ['pinned', 'hr', 'work', 'pay', 'people', 'bottom'], ['pay']],
    ['desk-only HR', { ...HR, status: 'desk-only', canRosterEveryone: true }, 'hr', ['pinned', 'hr', 'people', 'admin'], ['admin']],
  ]

  it.each(IDENTITIES)('%s', (_name, session, own, order, closed) => {
    const { groups, own: mine } = railGroups(allowedNav(session), session)
    expect(mine).toBe(own)
    expect(groups.map((g) => g.id)).toEqual(order)
    expect(defaultCollapsed(groups, mine)).toEqual(closed)
    // Only role holders get a toggle on Pay & policies.
    expect(groups.find((g) => g.id === 'pay')?.collapsible ?? false).toBe(own !== null && order.includes('pay'))
  })

  it('names no work group when no role group is drawn', () => {
    expect(workGroup({ canAdminPortal: true }, [{ id: 'pinned' }])).toBeNull()
  })
})

describe('collapsed state storage (KTD13)', () => {
  const FALLBACK = ['pay', 'admin']

  afterEach(() => {
    delete globalThis.localStorage
  })

  it('round-trips the collapsed ids per user', () => {
    const storage = memoryStorage()
    writeCollapsed('a@x', ['hr'], storage)
    expect(readCollapsed('a@x', FALLBACK, storage)).toEqual(['hr'])
    expect(storage.data[`${NAV_COLLAPSED_PREFIX}a@x`]).toBe('["hr"]')
    // Another account on the same browser starts from its own default.
    expect(readCollapsed('b@x', FALLBACK, storage)).toEqual(FALLBACK)
  })

  it('remembers an explicit "nothing collapsed" -- a remembered state wins', () => {
    const storage = memoryStorage()
    writeCollapsed('a@x', [], storage)
    expect(readCollapsed('a@x', FALLBACK, storage)).toEqual([])
  })

  it('falls back to the identity default when nothing usable is stored', () => {
    expect(readCollapsed('a@x', FALLBACK, memoryStorage())).toEqual(FALLBACK)
    expect(readCollapsed('a@x', FALLBACK, memoryStorage({ [`${NAV_COLLAPSED_PREFIX}a@x`]: '{nope' }))).toEqual(FALLBACK)
    expect(readCollapsed('a@x', FALLBACK, memoryStorage({ [`${NAV_COLLAPSED_PREFIX}a@x`]: '{"a":1}' }))).toEqual(FALLBACK)
    expect(readCollapsed(null, FALLBACK, memoryStorage())).toEqual(FALLBACK)
    expect(readCollapsed('a@x', FALLBACK, throwing)).toEqual(FALLBACK)
    expect(() => writeCollapsed('a@x', ['hr'], throwing)).not.toThrow()
  })

  it('survives a localStorage getter that throws (site data blocked)', () => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError')
      },
    })
    expect(readCollapsed('a@x', FALLBACK)).toEqual(FALLBACK)
    expect(() => writeCollapsed('a@x', ['hr'])).not.toThrow()
  })
})
