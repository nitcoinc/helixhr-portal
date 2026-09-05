import { createRouter, createWebHistory } from 'vue-router'
import { ensureBootstrap, session } from './lib/session'

// P2-U2 / P2-R12. The exact-detail route convention, in one place, because
// U4-U8 all have to obey the same one:
//
//   list             detail                            param
//   ---------------------------------------------------------------------
//   /leave           /leave/:name                      Leave Application id
//   /requests        /requests/:name                   HR Request id
//   /approvals       /approvals/:kind/:name            kind = leave|timesheet
//   /timesheet       /timesheet/:weekStart             Monday, YYYY-MM-DD
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
    path: '/approvals/:kind(leave|timesheet)/:name',
    name: 'ApprovalDetail',
    component: () => import('@/pages/Approvals.vue'),
    props: true,
  },
  {
    path: '/profile',
    name: 'Profile',
    component: () => import('@/pages/Profile.vue'),
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
//   service failure   /unavailable, with Retry -- never "not set up".
//   unknown route     /:pathMatch -> Not found, with a way Home.
//   permission denied stays an in-app error on the page that asked; it
//                     never reaches this guard.
router.beforeEach(async (to) => {
  // An unknown URL is answerable without knowing who is asking, and asking
  // would only turn a typo into a login round trip.
  if (to.name === 'NotFound') return true

  await ensureBootstrap()

  if (session.status === 'ready') {
    // Reaching a state page with a healthy session (a stale bookmark, or a
    // Back into /unavailable after a successful retry) means the state is
    // over; go where the user actually wanted to be.
    return STATE_ROUTES.includes(to.name) ? { name: 'Dashboard' } : true
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
