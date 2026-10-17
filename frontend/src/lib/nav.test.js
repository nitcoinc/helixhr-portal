import { describe, expect, it } from 'vitest'
import { icons } from './icons'
import { NAV, allowedNav, askHrTarget, navTarget, tabBar } from './nav'

// The flag sets the bootstrap answers for each Playwright identity, plus the
// two shapes signed in inside specs (desk-only HR, a System Manager).
const IDENTITIES = {
  employee: { status: 'ready' },
  manager: { status: 'ready', canApprove: true, hasReports: true, stableApprover: true },
  leaveApprover: { status: 'ready', canApprove: true },
  hr: {
    status: 'ready',
    canApprove: true,
    stableApprover: true,
    canHandleRequests: true,
    canConfigure: true,
    canSeeOrganisation: true,
    canSeePeople: true,
    canRosterEveryone: true,
    canSeeProjects: true,
    canRunReports: true,
    canOverseeTimesheets: true,
  },
  hrUser: { status: 'ready', canOverseeTimesheets: true, canSeePeople: true },
  it: { status: 'ready', canHandleRequests: true, canWorkRequests: true },
  portalAdmin: { status: 'ready', canAdminPortal: true, canEditEmailTemplates: true },
  systemManager: {
    status: 'ready',
    canApprove: true,
    stableApprover: true,
    canHandleRequests: true,
    canConfigure: true,
    canAdminPortal: true,
    canEditEmailTemplates: true,
    canSeeOrganisation: true,
    canSeePeople: true,
    canRosterEveryone: true,
    canSeeProjects: true,
    canRunReports: true,
  },
  deskOnlyHr: {
    status: 'desk-only',
    canConfigure: true,
    canSeeOrganisation: true,
    canSeePeople: true,
    canRosterEveryone: true,
    canSeeProjects: true,
    canRunReports: true,
  },
}

const labels = (items) => items.map((item) => item.label)

describe('allowedNav', () => {
  it('gives a plain employee no People, Settings, Organisation, Reports or approvals', () => {
    const seen = labels(allowedNav(IDENTITIES.employee))
    for (const hidden of ['People', 'Settings', 'Organisation', 'Reports', 'Projects', 'Email templates', 'Approvals', 'Team']) {
      expect(seen).not.toContain(hidden)
    }
    expect(seen).toEqual([
      'Home',
      'Notifications',
      'Leave',
      'Timesheet',
      'My projects',
      'Attendance',
      'Requests',
      'Payslips',
      'Holidays',
      'Documents',
      'Directory',
      'Roster',
      'Profile',
    ])
  })

  it('separates Approvals (a decision) from Team (reports)', () => {
    expect(labels(allowedNav(IDENTITIES.manager))).toEqual(expect.arrayContaining(['Approvals', 'Team']))
    const approver = labels(allowedNav(IDENTITIES.leaveApprover))
    expect(approver).toContain('Approvals')
    expect(approver).not.toContain('Team')
    // Oversight alone draws Approvals; a request handler alone does not.
    expect(labels(allowedNav(IDENTITIES.hrUser))).toContain('Approvals')
    expect(labels(allowedNav(IDENTITIES.it))).not.toContain('Approvals')
  })

  it('merges Directory into People for a session that can open People (HR-07)', () => {
    const hr = labels(allowedNav(IDENTITIES.hr))
    expect(hr).toContain('People')
    expect(hr).not.toContain('Directory')
    expect(labels(allowedNav(IDENTITIES.employee))).toContain('Directory')
  })

  it('keeps Email templates to Portal Admin and System Manager, Settings to them and HR', () => {
    expect(labels(allowedNav(IDENTITIES.hr))).toContain('Settings')
    expect(labels(allowedNav(IDENTITIES.hr))).not.toContain('Email templates')
    expect(labels(allowedNav(IDENTITIES.portalAdmin))).toEqual(
      expect.arrayContaining(['Settings', 'Email templates', 'Reports']),
    )
    expect(labels(allowedNav(IDENTITIES.systemManager))).toEqual(
      expect.arrayContaining(['Settings', 'Email templates', 'People', 'Organisation', 'Projects']),
    )
  })

  it('leaves a desk-only session Home and the role-scoped pages', () => {
    expect(labels(allowedNav(IDENTITIES.deskOnlyHr))).toEqual([
      'Home',
      'Roster',
      'People',
      'Reports',
      'Organisation',
      'Projects',
      'Settings',
    ])
    expect(labels(allowedNav({ status: 'desk-only' }))).toEqual(['Home'])
  })

  it("opens a handler's Requests on the Team inbox and everyone else's on My requests (AI-02)", () => {
    const requests = (session) => allowedNav(session).find((item) => item.label === 'Requests')
    expect(navTarget(requests(IDENTITIES.it))).toEqual({ path: '/requests', query: { tab: 'work' } })
    expect(navTarget(requests(IDENTITIES.hr))).toEqual({ path: '/requests', query: { tab: 'work' } })
    expect(navTarget(requests(IDENTITIES.employee))).toBe('/requests')
  })
})

describe('tabBar (KTD7)', () => {
  const tabs = (name) => labels(tabBar(allowedNav(IDENTITIES[name]), IDENTITIES[name]))

  it.each([
    ['employee', ['Home', 'Leave', 'Timesheet', 'Requests']],
    ['manager', ['Home', 'Approvals', 'Timesheet', 'Requests']],
    ['hr', ['Home', 'Approvals', 'Timesheet', 'Requests']],
    ['hrUser', ['Home', 'Leave', 'Timesheet', 'Requests']],
    ['it', ['Home', 'Leave', 'Timesheet', 'Requests']],
    ['portalAdmin', ['Home', 'Leave', 'Timesheet', 'Requests']],
    ['leaveApprover', ['Home', 'Leave', 'Timesheet', 'Requests']],
    ['deskOnlyHr', ['Home']],
  ])('%s gets %j before More', (name, expected) => {
    expect(tabs(name)).toEqual(expected)
  })

  it('keys the swap on the stable flag alone, never on the queue', () => {
    const empty = { ...IDENTITIES.leaveApprover, canApprove: false }
    expect(tabs('leaveApprover')).toEqual(labels(tabBar(allowedNav(empty), empty)))
  })
})

describe('askHrTarget', () => {
  it('prefills the New request form, and hides for a session with no Employee', () => {
    expect(askHrTarget(IDENTITIES.employee)).toEqual({ path: '/requests', query: { category: 'Other' } })
    expect(askHrTarget(IDENTITIES.deskOnlyHr)).toBeNull()
  })
})

describe('NAV glyphs (MH-21, AI-23, EP-25)', () => {
  it('draws every destination with a glyph that exists, and no two alike', () => {
    for (const item of NAV) expect(icons[item.icon], `${item.label} -> ${item.icon}`).toBeTruthy()
    const glyphs = NAV.map((item) => item.icon)
    expect(new Set(glyphs).size).toBe(glyphs.length)
    expect(icons.askHr).toBeTruthy()
  })
})
