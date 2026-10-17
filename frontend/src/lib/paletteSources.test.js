import { describe, expect, it } from 'vitest'
import { allowedNav } from './nav'
import {
  PALETTE_RECENTS_MAX,
  forgetRecent,
  matchItems,
  paletteActions,
  palettePages,
  paletteReports,
  readRecents,
  recentGranted,
  recordRecent,
  serverGroups,
} from './paletteSources'
import { PALETTE_RECENTS_PREFIX } from './viewerStorage'

// Plan 2026-10-17-001 U5. The palette offers exactly what the rail and the
// router allow each identity, and remembers per user, per device.
const IDENTITIES = {
  employee: { status: 'ready', user: 'employee@x' },
  hr: {
    status: 'ready',
    user: 'hr@x',
    canApprove: true,
    canHandleRequests: true,
    canConfigure: true,
    canSeeOrganisation: true,
    canSeePeople: true,
    canRosterEveryone: true,
    canSeeProjects: true,
    canRunReports: true,
  },
  deskOnlyHr: {
    status: 'desk-only',
    user: 'hr-desk@x',
    deskUrl: '/desk',
    canConfigure: true,
    canSeeOrganisation: true,
    canSeePeople: true,
    canRosterEveryone: true,
    canSeeProjects: true,
    canRunReports: true,
  },
  portalAdmin: { status: 'ready', user: 'admin@x', canAdminPortal: true, canEditEmailTemplates: true },
  templatesOnly: { status: 'desk-only', user: 'mail@x', canAdminPortal: true, canEditEmailTemplates: true },
  notLinked: { status: 'not-linked', user: 'nobody@x' },
}

const labels = (items) => items.map((item) => item.label)

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  }
}

describe('palettePages and paletteActions', () => {
  it('offers a plain employee no People, Settings, Organisation or Reports', () => {
    const pages = labels(palettePages(IDENTITIES.employee))
    for (const hidden of ['People', 'Settings', 'Organisation', 'Reports', 'Projects', 'Email templates']) {
      expect(pages).not.toContain(hidden)
    }
    expect(pages).toContain('Payslips')
    expect(labels(paletteActions(IDENTITIES.employee))).toEqual(['Ask for leave', 'Fill timesheet', 'New request'])
  })

  it('offers a desk-only HR session only deskOnly pages, People and Reports, and no actions', () => {
    const pages = palettePages(IDENTITIES.deskOnlyHr)
    expect(labels(pages)).toEqual(labels(allowedNav(IDENTITIES.deskOnlyHr)))
    expect(labels(pages)).toEqual(expect.arrayContaining(['People', 'Reports']))
    expect(labels(pages)).not.toContain('Leave')
    expect(paletteActions(IDENTITIES.deskOnlyHr)).toEqual([])
  })

  it("offers a Portal Admin exactly its rail's pages", () => {
    expect(labels(palettePages(IDENTITIES.portalAdmin))).toEqual(labels(allowedNav(IDENTITIES.portalAdmin)))
    expect(labels(paletteActions(IDENTITIES.portalAdmin))).toContain('Ask for leave')
  })

  it('pins a templates-only Portal Admin to Email templates, as the router does', () => {
    expect(labels(palettePages(IDENTITIES.templatesOnly))).toEqual(['Email templates'])
    expect(paletteActions(IDENTITIES.templatesOnly)).toEqual([])
  })

  it('offers nothing to a session the shell never renders for', () => {
    expect(palettePages(IDENTITIES.notLinked)).toEqual([])
    expect(paletteActions(IDENTITIES.notLinked)).toEqual([])
  })

  it("routes a handler's Requests page to the Team inbox and actions to their forms", () => {
    const requests = palettePages(IDENTITIES.hr).find((page) => page.label === 'Requests')
    expect(requests.route).toBe('/requests?tab=work')
    const routes = Object.fromEntries(paletteActions(IDENTITIES.hr).map((action) => [action.label, action.route]))
    expect(routes).toEqual({
      'Ask for leave': '/leave?ask=1',
      'Fill timesheet': '/timesheet',
      'New request': '/requests?category=Other',
    })
  })
})

describe('matchItems', () => {
  it('finds Payslips from "pay", prefix matches first', () => {
    const items = [...palettePages(IDENTITIES.employee), ...paletteActions(IDENTITIES.employee)]
    expect(labels(matchItems(items, 'pay'))[0]).toBe('Payslips')
    expect(labels(matchItems(items, 'TIME'))).toEqual(['Timesheet', 'Fill timesheet'])
  })

  it('matches a report by its question, after label matches', () => {
    const reports = paletteReports([
      { key: 'leave_ledger', label: 'Leave ledger', question: 'Who took what leave?' },
      { key: 'aging', label: 'Request aging', question: 'Which leave requests wait longest?' },
    ])
    expect(labels(matchItems(reports, 'leave'))).toEqual(['Leave ledger', 'Request aging'])
    expect(reports[0].route).toBe('/reports/leave_ledger')
  })

  it('ignores case and accents, and matches nothing for a blank query', () => {
    const items = [{ label: 'Résumé review' }]
    expect(labels(matchItems(items, 'resume'))).toEqual(['Résumé review'])
    expect(matchItems(items, '   ')).toEqual([])
  })
})

describe('serverGroups', () => {
  const result = {
    people: [{ name: 'HR-EMP-1', employee_name: 'Ann Lee', designation: 'Engineer' }],
    requests: [{ name: 'HR-REQ-1', subject: 'Letter', status: 'Open', category: 'HR Letter' }],
    inbox: [{ name: 'HR-REQ-2', subject: 'Laptop', status: 'Open', category: 'IT / Asset' }],
    failed: [],
  }

  it('opens a person in People for a People session, else the filtered directory', () => {
    expect(serverGroups(result, IDENTITIES.hr)[0].items[0].route).toBe('/people/HR-EMP-1')
    expect(serverGroups(result, IDENTITIES.employee)[0].items[0].route).toBe('/directory?q=Ann%20Lee')
  })

  it('opens an own request in the employee view and an inbox one in the handler view', () => {
    const [, mine, inbox] = serverGroups(result, IDENTITIES.hr)
    expect(mine.items[0]).toMatchObject({ type: 'request', route: '/requests/HR-REQ-1', hint: 'HR Letter · Sent to HR' })
    expect(inbox.items[0]).toMatchObject({ type: 'inbox', route: '/requests/HR-REQ-2?tab=work', hint: 'IT / Asset · New' })
  })

  it('drops empty groups', () => {
    expect(serverGroups({ people: [], requests: [], inbox: [], failed: ['people'] }, IDENTITIES.hr)).toEqual([])
    expect(serverGroups(null, IDENTITIES.hr)).toEqual([])
  })
})

describe('recents (KTD13)', () => {
  const person = { type: 'person', route: '/people/HR-EMP-1', label: 'Ann Lee', hint: 'secret', id: 'x' }
  const inbox = { type: 'inbox', route: '/requests/HR-REQ-2?tab=work', label: 'Laptop' }
  const report = { type: 'report', route: '/reports/leave_ledger', label: 'Leave ledger' }

  it('stores only type, route and label, under a key that names the user', () => {
    const storage = memoryStorage()
    recordRecent('hr@x', person, storage)
    expect(JSON.parse(storage.getItem(`${PALETTE_RECENTS_PREFIX}hr@x`))).toEqual([
      { type: 'person', route: '/people/HR-EMP-1', label: 'Ann Lee' },
    ])
  })

  it('keeps the newest first, once each, at most five', () => {
    const storage = memoryStorage()
    for (let index = 0; index < 7; index += 1) {
      recordRecent('hr@x', { type: 'page', route: `/p${index}`, label: `P${index}` }, storage)
    }
    recordRecent('hr@x', { type: 'page', route: '/p4', label: 'P4' }, storage)
    const stored = JSON.parse(storage.getItem(`${PALETTE_RECENTS_PREFIX}hr@x`))
    expect(stored.map((item) => item.route)).toEqual(['/p4', '/p6', '/p5', '/p3', '/p2'])
    expect(stored).toHaveLength(PALETTE_RECENTS_MAX)
  })

  it("never shows one user's recents to another", () => {
    const storage = memoryStorage()
    recordRecent('hr@x', person, storage)
    expect(readRecents(IDENTITIES.employee, storage)).toEqual([])
  })

  it('drops a recent whose group the session no longer holds', () => {
    expect(recentGranted(person, IDENTITIES.hr)).toBe(true)
    expect(recentGranted(person, { ...IDENTITIES.hr, canSeePeople: false })).toBe(false)
    expect(recentGranted(inbox, IDENTITIES.hr)).toBe(true)
    expect(recentGranted(inbox, { ...IDENTITIES.hr, canHandleRequests: false })).toBe(false)
    expect(recentGranted(report, { ...IDENTITIES.hr, canRunReports: false })).toBe(false)
    const storage = memoryStorage()
    recordRecent('hr@x', person, storage)
    recordRecent('hr@x', report, storage)
    expect(readRecents({ ...IDENTITIES.hr, canSeePeople: false }, storage).map((item) => item.label)).toEqual([
      'Leave ledger',
    ])
  })

  it('forgets a recent that now lands on a refusal', () => {
    const storage = memoryStorage()
    recordRecent('hr@x', person, storage)
    recordRecent('hr@x', report, storage)
    forgetRecent('hr@x', person.route, storage)
    expect(readRecents(IDENTITIES.hr, storage).map((item) => item.route)).toEqual([report.route])
  })

  it('reads as empty, and never throws, when storage does or holds junk', () => {
    const throwing = {
      getItem() {
        throw new Error('SecurityError')
      },
      setItem() {
        throw new Error('SecurityError')
      },
    }
    expect(readRecents(IDENTITIES.hr, throwing)).toEqual([])
    expect(() => recordRecent('hr@x', person, throwing)).not.toThrow()
    expect(() => forgetRecent('hr@x', person.route, throwing)).not.toThrow()
    const junk = memoryStorage({
      [`${PALETTE_RECENTS_PREFIX}hr@x`]: '[{"type":"evil","route":"javascript:x","label":1},"x"]',
    })
    expect(readRecents(IDENTITIES.hr, junk)).toEqual([])
    expect(readRecents(IDENTITIES.hr, memoryStorage({ [`${PALETTE_RECENTS_PREFIX}hr@x`]: '{nope' }))).toEqual([])
  })

  it('stores nothing without a user', () => {
    const storage = memoryStorage()
    recordRecent('', person, storage)
    expect(storage.data.size).toBe(0)
  })
})
