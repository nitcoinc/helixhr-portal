import { createRouter, createWebHistory } from 'vue-router'
import { ensureBootstrap, session } from './lib/session'

// P2-U2 / P2-R12. The exact-detail route convention, in one place, because
// U4-U8 all have to obey the same one:
//
//   list             detail                            param
//   ---------------------------------------------------------------------
//   /leave           /leave/:name                      Leave Application id
//   /requests        /requests/:name                   HR Request id
//   /approvals       /approvals/:kind/:name            kind = leave|timesheet|attendance|change|roster
//   /timesheet       /timesheet/:weekStart             Monday, YYYY-MM-DD
//   /payslips        /payslips/:name                   Salary Slip id (P3-U2)
//   /attendance      /attendance/requests/:name        Attendance Request id (P3-U6)
//   /holidays, /team, /directory                       lists only (P3-U3, U7, U8)
//   /notifications   (opens the target record's route above)
//
// Rules that go with it:
//   * The parameter is the record's real Frappe name, or -- for a week --
//     its Monday as a plain calendar date. Never an index, never an offset
//     from "now": both change meaning on refresh, which is exactly what
//     P2-R12 forbids.
//   * `:weekStart` is constrained to YYYY-MM-DD so /timesheet/history stays
//     its own route and a malformed week falls through to Not found rather
//     than rendering an arbitrary week.
//   * Every detail route sets `props: true`, so the page takes the record
//     id as a prop and never reaches into `useRoute()` for it.
//   * Route names are PascalCase and stable: `LeaveDetail`, `RequestDetail`,
//     `ApprovalDetail`, `TimesheetWeek`. Link to them by name.
//   * A detail route is reachable directly, without its list. Refresh and
//     browser Back both have to land on the same record.
//
// The detail routes below currently resolve to the list page they belong
// to. Each unit that builds the real detail screen swaps that component in
// and touches nothing else -- the same swap-a-stub pattern already used for
// the pages themselves.
const routes = [
  {
    path: '/',
    name: 'Dashboard',
    meta: { title: 'Home', deskOnly: true },
    component: () => import('@/pages/Dashboard.vue'),
  },
  {
    path: '/leave',
    name: 'Leave',
    meta: { title: 'Leave' },
    component: () => import('@/pages/Leave.vue'),
  },
  {
    path: '/leave/:name',
    name: 'LeaveDetail',
    meta: { title: 'Leave' },
    component: () => import('@/pages/Leave.vue'),
    props: true,
  },
  {
    path: '/attendance',
    name: 'Attendance',
    meta: { title: 'Attendance' },
    component: () => import('@/pages/Attendance.vue'),
  },
  {
    // P3-U1 step 7: resolves to the Attendance page until P3-U6 builds the
    // request detail.
    path: '/attendance/requests/:name',
    name: 'AttendanceRequestDetail',
    meta: { title: 'Attendance' },
    component: () => import('@/pages/Attendance.vue'),
    props: true,
  },
  {
    path: '/payslips',
    name: 'Payslips',
    meta: { title: 'Payslips' },
    component: () => import('@/pages/Payslips.vue'),
  },
  {
    path: '/payslips/:name',
    name: 'PayslipDetail',
    meta: { title: 'Payslips' },
    component: () => import('@/pages/Payslips.vue'),
    props: true,
  },
  {
    path: '/holidays',
    name: 'Holidays',
    meta: { title: 'Holidays' },
    component: () => import('@/pages/Holidays.vue'),
  },
  {
    // EP-24: the rail hides Team without direct reports (`hasReports`), and
    // an old link or the back stack lands Home instead of on a refusal.
    path: '/team',
    name: 'Team',
    meta: { title: 'Team', reportsOnly: true },
    component: () => import('@/pages/Team.vue'),
  },
  {
    // Plan 2026-09-30-001 U9. `get_roster_week` is the server's own gate per
    // mode, so a desk-only HR session keeps the page (its only mode is
    // "everyone"); an employee asking for more gets AsyncState's
    // 'forbidden' region.
    path: '/roster',
    name: 'Roster',
    meta: { title: 'Roster', deskOnly: true },
    component: () => import('@/pages/Roster.vue'),
  },
  {
    path: '/directory',
    name: 'Directory',
    meta: { title: 'Directory' },
    component: () => import('@/pages/Directory.vue'),
  },
  {
    path: '/timesheet',
    name: 'Timesheet',
    meta: { title: 'Timesheet' },
    component: () => import('@/pages/Timesheet.vue'),
  },
  {
    path: '/timesheet/history',
    name: 'TimesheetHistory',
    meta: { title: 'Timesheet history' },
    component: () => import('@/pages/TimesheetHistory.vue'),
  },
  {
    // A week is addressed by its Monday, the same identity the server uses
    // (`helixhr.utils.get_week_bounds`, KTD10).
    path: '/timesheet/:weekStart(\\d{4}-\\d{2}-\\d{2})',
    name: 'TimesheetWeek',
    meta: { title: 'Timesheet' },
    component: () => import('@/pages/Timesheet.vue'),
    props: true,
  },
  {
    // Plan 2026-10-05-001 U6: any linked employee's own projects.
    path: '/my-projects',
    name: 'MyProjects',
    meta: { title: 'My projects' },
    component: () => import('@/pages/MyProjects.vue'),
  },
  {
    path: '/requests',
    name: 'Requests',
    meta: { title: 'Requests' },
    component: () => import('@/pages/Requests.vue'),
  },
  {
    path: '/requests/:name',
    name: 'RequestDetail',
    meta: { title: 'Requests' },
    component: () => import('@/pages/Requests.vue'),
    props: true,
  },
  {
    path: '/documents',
    name: 'Documents',
    meta: { title: 'Documents' },
    component: () => import('@/pages/Documents.vue'),
  },
  {
    path: '/notifications',
    name: 'Notifications',
    meta: { title: 'Notifications' },
    component: () => import('@/pages/Notifications.vue'),
  },
  {
    // Plan 2026-10-16-001 KTD8: Old mail carries /approvals/request/:name.
    // Permanent redirect to Team inbox detail; stays permanently.
    path: '/approvals/request/:name',
    redirect: (to) => ({
      name: 'RequestDetail',
      params: { name: to.params.name },
      query: { tab: 'work' },
    }),
  },
  {
    path: '/approvals',
    name: 'Approvals',
    meta: { title: 'Approvals' },
    component: () => import('@/pages/Approvals.vue'),
  },
  {
    path: '/approvals/:kind(leave|timesheet|attendance|change|roster)/:name',
    name: 'ApprovalDetail',
    meta: { title: 'Approvals' },
    component: () => import('@/pages/Approvals.vue'),
    props: true,
  },
  {
    // Plan 2026-09-29-001 U5. Each tab is addressable, so a refresh or a
    // shared link lands on the same tab; bare /profile is Personal. One route
    // record, so switching tabs keeps the page -- and any unsaved contact
    // edit -- rather than remounting it.
    path: '/profile/:section(personal|job|contact|history|bank)?',
    name: 'Profile',
    meta: { title: 'Profile' },
    component: () => import('@/pages/Profile.vue'),
    props: true,
  },
  {
    path: '/settings',
    name: 'Settings',
    meta: { title: 'Settings', deskOnly: true },
    component: () => import('@/pages/Settings.vue'),
  },
  {
    // P5-U14: a section is opened directly (e.g. from a link elsewhere in
    // the portal) rather than only via in-page tabs. `get_portal_config` is
    // the server's own gate -- an employee hitting this route directly gets
    // AsyncState's 'forbidden' region, not a client-side redirect.
    path: '/settings/:section(categories|leave-types|holiday-lists|shift-types|leave-rules|report-access|people-access|approvers)',
    name: 'SettingsSection',
    meta: { title: 'Settings', deskOnly: true },
    component: () => import('@/pages/Settings.vue'),
    props: true,
  },
  {
    // Plan 2026-10-19-001 U7: one person open in People & access. A route,
    // so Back closes the editor; the same page and section stay mounted
    // under it (one `meta.title`, so the shell does not move focus).
    path: '/settings/people-access/:user',
    name: 'SettingsPeopleAccessPerson',
    meta: { title: 'Settings', deskOnly: true },
    component: () => import('@/pages/Settings.vue'),
    props: { section: 'people-access' },
  },
  {
    // Plan 2026-10-15-001 U6: People & access replaced these two sections.
    path: '/settings/:section(portal-roles|company-access)',
    name: 'SettingsPeopleAccessRedirect',
    redirect: { name: 'SettingsSection', params: { section: 'people-access' } },
  },
  {
    // Plan 2026-10-04-004 U5 / R13: the Settings page's celebrations section
    // moved to the Email templates page's own group; the old URL lands there.
    path: '/settings/celebrations',
    name: 'SettingsCelebrations',
    redirect: () => ({ name: 'EmailTemplates', query: { group: 'celebrations' } }),
  },
  {
    // Plan 2026-10-02-001 U7. The page is U10's; every method behind it is
    // the server's own gate, same posture as /settings above.
    path: '/email-templates',
    name: 'EmailTemplates',
    // The route gate is the bootstrap flag `can_edit_email_templates`
    // (Portal Admin or System Manager) -- never a role list; every endpoint
    // behind the page checks the same predicate.
    meta: { title: 'Email templates', deskOnly: true, templatesOnly: true },
    component: () => import('@/pages/EmailTemplates.vue'),
  },
  {
    // P5-U15. `get_organisation_view` is the server's own gate -- a caller
    // hitting this route directly with no capability gets AsyncState's
    // 'forbidden' region, not a client-side redirect (same posture as
    // /settings above).
    path: '/organisation',
    name: 'Organisation',
    meta: { title: 'Organisation', deskOnly: true },
    component: () => import('@/pages/Organisation.vue'),
  },
  {
    // P6-U5. `search_people` is the server's own gate (`resolve_admin_
    // scope`) -- same posture as /organisation and /settings above.
    path: '/people',
    name: 'People',
    meta: { title: 'People', deskOnly: true },
    component: () => import('@/pages/People.vue'),
  },
  {
    // Plan 2026-10-22-001 U4. Registered before `/people/:employee` so the
    // literal path reads as itself. `get_new_person_options` is the server's
    // gate (`_can_add_people`), the same posture as /people.
    path: '/people/new',
    name: 'PersonNew',
    meta: { title: 'Add employee', deskOnly: true },
    component: () => import('@/pages/PersonNew.vue'),
  },
  {
    // The person's record is addressable directly, like every other detail
    // route (P2-R12): a reload or a shared link lands on the same person.
    path: '/people/:employee',
    name: 'PersonDetail',
    meta: { title: 'People', deskOnly: true },
    component: () => import('@/pages/People.vue'),
    props: true,
  },
  {
    // P6-U6. `?employee=<id>` pre-filters the launcher from a person's view
    // -- a filter, not a record, so it stays a query param rather than a
    // path segment (P6-R10).
    path: '/reports',
    name: 'Reports',
    meta: { title: 'Reports', deskOnly: true },
    component: () => import('@/pages/Reports.vue'),
  },
  {
    // Plan 2026-10-04-001 U4: one report, its filters/grouping/sort in the
    // query (lib/reportQuery.js) so a link reopens -- and re-runs -- it.
    path: '/reports/:reportKey',
    name: 'ReportView',
    meta: { title: 'Reports', deskOnly: true },
    component: () => import('@/pages/Reports.vue'),
    props: true,
  },
  {
    // P7-U5. `search_projects` is the server's own gate (`resolve_project_
    // scope`) -- same posture as /people, /organisation and /settings above.
    path: '/projects',
    name: 'Projects',
    meta: { title: 'Projects', deskOnly: true },
    component: () => import('@/pages/Projects.vue'),
  },
  {
    // The project's record is addressable directly, like every other detail
    // route (P2-R12): a reload or a shared link lands on the same project.
    path: '/projects/:project',
    name: 'ProjectDetail',
    meta: { title: 'Projects', deskOnly: true },
    component: () => import('@/pages/Projects.vue'),
    props: true,
  },
  // The three states that are not a page of the portal. All of them render
  // NotLinked.vue, which reads the session status; none of them get the nav
  // shell (there is nothing to navigate with, and for a Guest there is
  // nothing to navigate to).
  {
    path: '/not-linked',
    name: 'NotLinked',
    component: () => import('@/pages/NotLinked.vue'),
    meta: { title: 'Account not set up', shell: false },
  },
  {
    path: '/unavailable',
    name: 'Unavailable',
    component: () => import('@/pages/NotLinked.vue'),
    meta: { title: 'Unavailable', shell: false },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/pages/NotLinked.vue'),
    meta: { title: 'Page not found', shell: false },
  },
]

const STATE_ROUTES = ['NotLinked', 'Unavailable', 'NotFound']

const router = createRouter({
  history: createWebHistory('/helixhr'),
  routes,
  // P2-U2 scenario 5. Back out of a record and you land where you were in
  // the list, not at the top of it. `savedPosition` is only ever set by a
  // real popstate, so forward navigation still starts at the top.
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) return { el: to.hash }
    return { top: 0 }
  },
})

// One bootstrap per hard load (P2-R20, P2-R21). `ensureBootstrap` resolves
// from memory after the first call, so the six route changes after it cost
// no identity or capability request at all.
//
// Five outcomes are kept apart on purpose (P2-U2 scenario 3, 4):
//   Guest             lib/api.js has already sent them to /login carrying
//                     the destination in `redirect-to`.
//   unlinked employee /not-linked, with the site's HR contact.
//   HR, no Employee   desk-only: Home plus the role-scoped admin pages.
//   service failure   /unavailable, with Retry -- never "not set up".
//   unknown route     /:pathMatch -> Not found, with a way Home.
//   permission denied stays an in-app error on the page that asked; it
//                     never reaches this guard.
router.beforeEach(async (to) => {
  // An unknown URL is answerable without knowing who is asking, and asking
  // would only turn a typo into a login round trip.
  if (to.name === 'NotFound') return true

  await ensureBootstrap()

  // Plan 2026-10-02-001 U10: a caller without `can_edit_email_templates`
  // (an HR Manager, say) is sent Home rather than shown a refusal. The
  // server's guard is still the real gate; the nav item reads the same flag.
  if (to.meta.templatesOnly && !session.canEditEmailTemplates && session.status !== 'unavailable') {
    return { name: 'Dashboard' }
  }

  // Plan 2026-10-16-001 KTD8: Old links/bookmarks to /approvals?kind=request or
  // /approvals?category=... redirect permanently to Team inbox.
  if (to.name === 'Approvals' && (to.query.kind === 'request' || to.query.category)) {
    return { name: 'Requests', query: { tab: 'work' } }
  }

  if (session.status === 'ready') {
    // Reaching a state page with a healthy session (a stale bookmark, or a
    // Back into /unavailable after a successful retry) means the state is
    // over; go where the user actually wanted to be.
    if (STATE_ROUTES.includes(to.name)) return { name: 'Dashboard' }
    return to.meta.reportsOnly && !session.hasReports ? { name: 'Dashboard' } : true
  }

  // An HR or System Manager with no Employee record: the pages whose
  // server methods are scoped by role (`meta.deskOnly`), and Home. Every
  // other page reads "my" records and would only fail.
  // A Portal Admin with no Employee and no Desk role has one page in the
  // portal, so every route lands on Email templates.
  if (session.status === 'desk-only') {
    if (session.canEditEmailTemplates && !session.deskUrl) {
      return to.name === 'EmailTemplates' ? true : { name: 'EmailTemplates' }
    }
    return to.meta.deskOnly ? true : { name: 'Dashboard' }
  }

  if (session.status === 'not-linked') {
    return to.name === 'NotLinked' ? true : { name: 'NotLinked' }
  }

  // 'unavailable'. The destination rides along so Retry can resume it
  // instead of dumping the user on Home (P2-R25).
  return to.name === 'Unavailable'
    ? true
    : { name: 'Unavailable', query: { 'retry-to': to.fullPath } }
})

// Plan 2026-10-17-001 U4 (R10, WCAG 2.4.2): every page names itself in the
// tab title, from its route's `meta.title`. Focus after a route change is the
// shell's (AppShell.vue), because only the shell owns <main>.
router.afterEach((to, from, failure) => {
  if (failure) return
  document.title = to.meta.title ? `${to.meta.title} · HelixHR` : 'HelixHR'
})

export default router
