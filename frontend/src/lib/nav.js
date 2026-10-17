// Plan 2026-10-17-001 U4. The portal's destinations and the one gate
// predicate that decides which of them a session sees. Moved out of
// AppShell.vue so the command palette (U5) applies exactly the rail's gates;
// `router.beforeEach` enforces only some of them (`reportsOnly`,
// `templatesOnly`, `deskOnly`) -- every page's server method is the real gate.
//
// `primary` items are the four that fit the phone tab bar alongside "More"
// (design system: max 5 tab items); `tabBar` swaps Approvals into Leave's
// slot for a stable approver (KTD7). Everything else lives in the desktop
// sidebar and, on a phone, behind More.
//
// Two approval gates, kept apart on purpose (P3-KTD11): `managerOnly` reads
// `canApprove` (a decision is waiting, someone reports to you, or you are HR)
// or timesheet oversight; `reportsOnly` reads `hasReports`. A leave approver
// with no reports gets Approvals and not an empty Team page.
//
// `deskOnly` marks the items a 'desk-only' session (HR or System Manager with
// no Employee record) keeps: Home and the role-scoped admin pages. `group`
// places the item in a sidebar section (lib/navGroups.js). `badge` names the
// attention count the item carries ('unread' or 'approvals').
//
/** A request handler's inbox: Requests on its work tab. */
export const HANDLER_INBOX = { path: '/requests', query: { tab: 'work' } }

// Each destination has its own glyph (MH-21, AI-23, EP-25): nav.test.js
// fails on a shared one.
export const NAV = [
  { label: 'Home', to: '/', icon: 'home', primary: true, deskOnly: true, group: 'pinned' },
  { label: 'Notifications', to: '/notifications', icon: 'notifications', badge: 'unread', group: 'pinned' },
  { label: 'Leave', to: '/leave', icon: 'leave', primary: true, group: 'work' },
  { label: 'Timesheet', to: '/timesheet', icon: 'timesheet', primary: true, group: 'work' },
  { label: 'My projects', to: '/my-projects', icon: 'briefcase', group: 'work' },
  { label: 'Attendance', to: '/attendance', icon: 'attendance', group: 'work' },
  // AI-02: a request handler's Requests entry opens the Team inbox.
  { label: 'Requests', to: '/requests', icon: 'requests', primary: true, handlerQuery: HANDLER_INBOX.query, group: 'work' },
  { label: 'Payslips', to: '/payslips', icon: 'wallet', group: 'pay' },
  { label: 'Holidays', to: '/holidays', icon: 'sun', group: 'pay' },
  { label: 'Documents', to: '/documents', icon: 'documents', group: 'pay' },
  // HR-07: `directoryOnly` -- a session that can open People gets one
  // "People" entry, not two lookups. /directory stays routable.
  { label: 'Directory', to: '/directory', icon: 'users', directoryOnly: true, group: 'people' },
  { label: 'Team', to: '/team', icon: 'usersRound', reportsOnly: true, group: 'people' },
  // Plan 2026-09-30-001 U9: everyone has a roster row of their own. Not
  // `primary` -- the tab bar is full. `rosterOnly` hides it from a desk-only
  // session with no admin scope, which would have no mode to show.
  { label: 'Roster', to: '/roster', icon: 'calendarClock', rosterOnly: true, deskOnly: true, group: 'people' },
  { label: 'Approvals', to: '/approvals', icon: 'approvals', badge: 'approvals', managerOnly: true, group: 'people' },
  { label: 'People', to: '/people', icon: 'peopleSearch', peopleOnly: true, deskOnly: true, group: 'hr' },
  { label: 'Reports', to: '/reports', icon: 'reports', runReportsOnly: true, deskOnly: true, group: 'hr' },
  { label: 'Organisation', to: '/organisation', icon: 'organisation', organisationOnly: true, deskOnly: true, group: 'hr' },
  { label: 'Projects', to: '/projects', icon: 'folder', projectsOnly: true, deskOnly: true, group: 'hr' },
  { label: 'Settings', to: '/settings', icon: 'settings', configureOnly: true, deskOnly: true, group: 'admin' },
  // Gated on the boolean `can_edit_email_templates` (Portal Admin or System
  // Manager), never a role list; HR roles do not see it.
  { label: 'Email templates', to: '/email-templates', icon: 'mail', templatesOnly: true, deskOnly: true, group: 'admin' },
  { label: 'Profile', to: '/profile', icon: 'profile', group: 'bottom' },
]

/**
 * The destinations `session` may see, in NAV order. Every gate is a
 * bootstrap boolean, never a role list (docs/architecture.md). None of it is
 * authorization: each page's server method refuses on its own.
 */
export function allowedNav(session, items = NAV) {
  const deskOnly = session.status === 'desk-only'
  // P2-R11 / plan 2026-10-16-001 R3: decisions to make, or timesheet
  // oversight. A request handler with no decisions (IT Team) has none.
  const approves = session.canApprove || session.canOverseeTimesheets
  return items
    .filter(
      (item) =>
        (!item.managerOnly || approves) &&
        (!item.reportsOnly || session.hasReports) &&
        (!item.directoryOnly || !session.canSeePeople) &&
        // A Portal Admin gets Settings (two sections) and Reports (export log).
        (!item.configureOnly || session.canConfigure || session.canAdminPortal) &&
        (!item.templatesOnly || session.canEditEmailTemplates) &&
        (!item.organisationOnly || session.canSeeOrganisation) &&
        (!item.peopleOnly || session.canSeePeople) &&
        (!item.projectsOnly || session.canSeeProjects) &&
        (!item.runReportsOnly || session.canRunReports || session.canAdminPortal) &&
        (!item.rosterOnly || !deskOnly || session.canRosterEveryone) &&
        (!deskOnly || item.deskOnly),
    )
    .map((item) =>
      item.handlerQuery && session.canHandleRequests ? { ...item, query: item.handlerQuery } : item,
    )
}

/** Where a nav item links: its path, plus the handler query when it has one. */
export function navTarget(item) {
  return item.query ? { path: item.to, query: item.query } : item.to
}

/**
 * The phone tab bar's destinations (KTD7). A stable approver -- reports, or
 * an HR approver, so the answer cannot change between two loads -- gets
 * Approvals in Leave's slot; Leave then leads "My work" in More. Everyone
 * else, a queue-dependent leave approver included, keeps the employee bar.
 */
export function tabBar(items, session) {
  const primary = items.filter((item) => item.primary)
  const approvals = session.stableApprover && items.find((item) => item.to === '/approvals')
  return approvals ? primary.map((item) => (item.to === '/leave' ? approvals : item)) : primary
}

/** "Ask HR" (R10, WCAG 3.2.6): the New request form, through the Requests
 * page's existing `?category&subject` prefill. `null` for a session with no
 * Employee, which has no requests of its own to file. */
export function askHrTarget(session) {
  return session.status === 'ready' ? { path: '/requests', query: { category: 'Other' } } : null
}
