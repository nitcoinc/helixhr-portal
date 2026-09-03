import { createRouter, createWebHistory } from 'vue-router'
import { call } from './lib/api'

const routes = [
  {
    path: '/not-linked',
    name: 'NotLinked',
    component: () => import('@/pages/NotLinked.vue'),
  },
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('@/pages/Dashboard.vue'),
  },
  // Stubs for pages later units build (U5-U9). Named now so Dashboard's
  // links (R7) and quick actions resolve to a real route instead of a
  // dead link in the meantime; each name gets swapped to its real page
  // component when that unit lands, no route/link changes needed.
  {
    path: '/leave',
    name: 'Leave',
    component: () => import('@/pages/Leave.vue'),
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
    path: '/requests',
    name: 'Requests',
    component: () => import('@/pages/PageComingSoon.vue'),
    props: { title: 'Requests' },
  },
  {
    path: '/profile',
    name: 'Profile',
    component: () => import('@/pages/Profile.vue'),
  },
]

let router = createRouter({
  history: createWebHistory('/helixhr'),
  routes,
})

// Every route but NotLinked itself needs an active Employee record for
// the signed-in user (R3). apiRequest already sends an unauthenticated
// visitor to /login on a 401 (KTD20), so by the time this guard's call
// resolves the user is known to be logged in; a null/empty result here
// specifically means "no active Employee", not "not logged in".
router.beforeEach(async (to) => {
  if (to.name === 'NotLinked') return true

  try {
    const employee = await call('hrms.api.get_current_employee_info')
    if (!employee || !employee.name) {
      return { name: 'NotLinked' }
    }
  } catch {
    // apiRequest already redirected (auth) or reloaded (CSRF) the page
    // for the errors it knows how to handle; anything else here is an
    // unexpected failure. Show the same friendly page rather than a
    // broken shell or a raw error (R3).
    return { name: 'NotLinked' }
  }

  return true
})

export default router
