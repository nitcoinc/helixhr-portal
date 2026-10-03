import { createRouter, createWebHistory } from 'vue-router'
import { ensureBootstrap, session } from './lib/session'

// P2-U2 / P2-R12. The exact-detail route convention, in one place, because
// U4-U8 all have to obey the same one:
//
//   list             detail                            param
//   ---------------------------------------------------------------------
//   /leave           /leave/:name                      Leave Application id
//   /requests        /requests/:name                   HR Request id
//   /approvals       /approvals/:kind/:name            kind = leave|timesheet|attendance|request
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
    meta: { deskOnly: true },
    component: () => import('@/pages/Dashboard.vue'),
  },
  {
    path: '/leave',
    name: 'Leave',
    component: () => import('@/pages/Leave.vue'),
  },
  {
    path: '/leave/:name',
    name: 'LeaveDetail',
    component: () => import('@/pages/Leave.vue'),
    props: true,
  },
  {
    path: '/attendance',
    name: 'Attendance',
    component: () => import('@/pages/Attendance.vue'),
  },
  {
    // P3-U1 step 7: resolves to the Attendance page until P3-U6 builds the
    // request detail.
    path: '/attendance/requests/:name',
    name: 'AttendanceRequestDetail',
    component: () => import('@/pages/Attendance.vue'),
    props: true,
  },
  {
    path: '/payslips',
    name: 'Payslips',
    component: () => import('@/pages/Payslips.vue'),
  },
  {
    path: '/payslips/:name',
    name: 'PayslipDetail',
    component: () => import('@/pages/Payslips.vue'),
    props: true,
  },
  {
    path: '/holidays',
    name: 'Holidays',
    component: () => import('@/pages/Holidays.vue'),
  },
  {
    path: '/team',
    name: 'Team',
    component: () => import('@/pages/Team.vue'),
  },
  {
    // Plan 2026-09-30-001 U9. `get_roster_week` is the server's own gate per
    // mode, so a desk-only HR session keeps the page (its only mode is
    // "everyone"); an employee asking for more gets AsyncState's
    // 'forbidden' region.
    path: '/roster',
    name: 'Roster',
    meta: { deskOnly: true },
    component: () => import('@/pages/Roster.vue'),
  },
  {
    path: '/directory',
    name: 'Directory',
    component: () => import('@/pages/Directory.vue'),
  },
  {
    path: '/timesheet',
    name: 'Timesheet',
    component: () => import('@/pages/Timesheet.vue'),
  },
  {
    path: '/timesheet/history',
    name: 'TimesheetHistory',
    component: () => import('@/pages/TimesheetHistory.vue'),
  },
  {
    // A week is addressed by its Monday, the same identity the server uses
    // (`helixhr.utils.get_week_bounds`, KTD10).
    path: '/timesheet/:weekStart(\\d{4}-\\d{2}-\\d{2})',
    name: 'TimesheetWeek',
    component: () => import('@/pages/Timesheet.vue'),
    props: true,
  },
  {
    path: '/requests',
    name: 'Requests',
    component: () => import('@/pages/Requests.vue'),
  },
  {
    path: '/requests/:name',
    name: 'RequestDetail',
    component: () => import('@/pages/Requests.vue'),
    props: true,
  },
  {
    path: '/documents',
    name: 'Documents',
    component: () => import('@/pages/Documents.vue'),
  },
  {
    path: '/notifications',
    name: 'Notifications',
    component: () => import('@/pages/Notifications.vue'),
  },
  {
    path: '/approvals',
    name: 'Approvals',
    component: () => import('@/pages/Approvals.vue'),
  },
  {
    path: '/approvals/:kind(leave|timesheet|attendance|request)/:name',
    name: 'ApprovalDetail',
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
    component: () => import('@/pages/Profile.vue'),
    props: true,
  },
  {
    path: '/settings',
    name: 'Settings',
    meta: { deskOnly: true },
    component: () => import('@/pages/Settings.vue'),
  },
  {
    // P5-U14: a section is opened directly (e.g. from a link elsewhere in
    // the portal) rather than only via in-page tabs. `get_portal_config` is
    // the server's own gate -- an employee hitting this route directly gets
    // AsyncState's 'forbidden' region, not a client-side redirect.
    path: '/settings/:section(categories|leave-types|holiday-lists|shift-types|celebrations)',
    name: 'SettingsSection',
    meta: { deskOnly: true },
    component: () => import('@/pages/Settings.vue'),
    props: true,
  },
  {
    // Plan 2026-10-02-001 U7. The page is U10's; every method behind it is
    // the server's own gate, same posture as /settings above.
    path: '/email-templates',
    name: 'EmailTemplates',
    meta: { deskOnly: true, notificationsOnly: true },
    component: () => import('@/pages/EmailTemplates.vue'),
  },
  {
    // P5-U15. `get_organisation_view` is the server's own gate -- a caller
    // hitting this route directly with no capability gets AsyncState's
    // 'forbidden' region, not a client-side redirect (same posture as
    // /settings above).
    path: '/organisation',
    name: 'Organisation',
    meta: { deskOnly: true },
    component: () => import('@/pages/Organisation.vue'),
  },
  {
    // P6-U5. `search_people` is the server's own gate (`resolve_admin_
    // scope`) -- same posture as /organisation and /settings above.
    path: '/people',
    name: 'People',
    meta: { deskOnly: true },
    component: () => import('@/pages/People.vue'),
  },
  {
    // The person's record is addressable directly, like every other detail
    // route (P2-R12): a reload or a shared link lands on the same person.
    path: '/people/:employee',
    name: 'PersonDetail',
    meta: { deskOnly: true },
    component: () => import('@/pages/People.vue'),
    props: true,
  },
  {
    // P6-U6. `?employee=<id>` pre-filters the launcher from a person's view
    // -- a filter, not a record, so it stays a query param rather than a
    // path segment (P6-R10).
    path: '/reports',
    name: 'Reports',
    meta: { deskOnly: true },
    component: () => import('@/pages/Reports.vue'),
  },
  {
    // Plan 2026-10-04-001 U4: one report, its filters/grouping/sort in the
    // query (lib/reportQuery.js) so a link reopens -- and re-runs -- it.
    path: '/reports/:reportKey',
    name: 'ReportView',
    meta: { deskOnly: true },
    component: () => import('@/pages/Reports.vue'),
    props: true,
  },
  {
    // P7-U5. `search_projects` is the server's own gate (`resolve_project_
    // scope`) -- same posture as /people, /organisation and /settings above.
    path: '/projects',
    name: 'Projects',
    meta: { deskOnly: true },
    component: () => import('@/pages/Projects.vue'),
  },
  {
    // The project's record is addressable directly, like every other detail
    // route (P2-R12): a reload or a shared link lands on the same project.
    path: '/projects/:project',
    name: 'ProjectDetail',
    meta: { deskOnly: true },
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
    meta: { shell: false },
  },
  {
    path: '/unavailable',
    name: 'Unavailable',
    component: () => import('@/pages/NotLinked.vue'),
    meta: { shell: false },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/pages/NotLinked.vue'),
    meta: { shell: false },
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

  // Plan 2026-10-02-001 U10: a caller without `can_manage_notifications`
  // (an HR Manager, say) is sent Home rather than shown a refusal. The
  // server's guard is still the real gate.
  if (to.meta.notificationsOnly && !session.canManageNotifications && session.status !== 'unavailable') {
    return { name: 'Dashboard' }
  }

  if (session.status === 'ready') {
    // Reaching a state page with a healthy session (a stale bookmark, or a
    // Back into /unavailable after a successful retry) means the state is
    // over; go where the user actually wanted to be.
    return STATE_ROUTES.includes(to.name) ? { name: 'Dashboard' } : true
  }

  // An HR or System Manager with no Employee record: the pages whose
  // server methods are scoped by role (`meta.deskOnly`), and Home. Every
  // other page reads "my" records and would only fail.
  // A Notification Manager with no Desk role (plan 2026-10-02-001 U7) has
  // one page in the portal, so every route lands on Email templates.
  if (session.status === 'desk-only') {
    if (session.canManageNotifications && !session.deskUrl) {
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

export default router
