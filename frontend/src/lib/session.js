import { reactive, readonly } from 'vue'
import { apiRequest, call } from './api'
import { configureCalendar } from './dates'

// P2-U2 / P2-R20 / P2-R21 / KTD7. One portal bootstrap per hard load.
//
// Before this, `router.beforeEach` called `hrms.api.get_current_employee_info`
// on *every* navigation and the shell separately counted direct reports, so a
// seven-page session paid seven identity round trips to learn something that
// cannot change while the tab is open. `helixhr.api.get_portal_bootstrap` now
// answers identity, approval capability, the initial unread count and the
// authoritative calendar in one request; route changes read this module.
//
// None of it is authorization. `canApprove` decides whether a nav item is
// drawn and nothing else -- every domain method resolves the session user
// server-side and is refused by Frappe permissions on its own. A session that
// dies mid-use is still caught by the next domain call, which `lib/api.js`
// turns into a redirect to /login.
const state = reactive({
  /**
   * 'idle'        nothing asked yet
   * 'loading'     bootstrap in flight
   * 'ready'       signed in, with an active Employee
   * 'not-linked'  signed in, no active Employee -- HR has to link them
   * 'unavailable' the request failed. NOT the same thing as 'not-linked',
   *               which is the whole point: a service failure used to be
   *               rendered as "your account is not set up" (P2-U2 sc. 3).
   */
  status: 'idle',
  employee: null,
  canApprove: false,
  unread: 0,
  /** The authoritative calendar (P2-R5). Mirrored into lib/dates.js. */
  timeZone: null,
  systemTimeZone: null,
  today: null,
  /** The failure behind status 'unavailable', for the retry panel. */
  error: null,
})

export const session = readonly(state)

let inFlight = null

/** Resolve the bootstrap, at most once per hard load. Concurrent callers
 * (the router guard racing a component) share the one request. */
export function ensureBootstrap() {
  if (state.status === 'ready' || state.status === 'not-linked') {
    return Promise.resolve(session)
  }
  if (!inFlight) inFlight = load()
  return inFlight
}

/** Explicit user-driven retry after a failed bootstrap (P2-R25). Nothing
 * else may force a refetch -- that would put the repeated identity lookup
 * straight back. */
export function retryBootstrap() {
  inFlight = null
  state.status = 'idle'
  state.error = null
  return ensureBootstrap()
}

async function load() {
  state.status = 'loading'
  try {
    apply(await call('helixhr.api.get_portal_bootstrap'))
  } catch (error) {
    // lib/api.js has already redirected a Guest to /login and reloaded on a
    // stale CSRF token. Anything still arriving here is a real failure of
    // the portal service, and must be shown as one.
    state.status = 'unavailable'
    state.error = error
  } finally {
    inFlight = null
  }
  return session
}

function apply(boot) {
  const employee = boot?.employee || null
  state.employee = employee
  state.canApprove = !!boot?.can_approve
  state.unread = boot?.unread_notifications ?? 0
  state.timeZone = boot?.time_zone || null
  state.systemTimeZone = boot?.system_time_zone || null
  state.today = boot?.today || null
  state.error = null
  // The server's timezone answer, not the browser's, is what every date on
  // screen is rendered against from here on (P2-AE3).
  configureCalendar({
    timeZone: state.timeZone,
    systemTimeZone: state.systemTimeZone,
    today: state.today,
  })
  state.status = employee?.name ? 'ready' : 'not-linked'
}

export async function signOut() {
  // POST, explicitly. Frappe's `logout` is a POST-only whitelisted method and
  // refuses a GET with `PermissionError: Not permitted` -- and `call()` only
  // upgrades to POST when it is given params, so `call('logout')` sent a GET.
  // The old `.catch(() => {})` then swallowed that 403 and redirected anyway
  // with the session still alive, at which point /login 301s a logged-in user
  // to their Desk home page, which an Employee Self Service user has no
  // permission for. Signing out showed a Frappe "Not permitted" dialog.
  try {
    await apiRequest({ url: 'logout', method: 'POST' })
  } catch (error) {
    // Landing on /login with a live session is exactly the loop above, so say
    // so instead of pretending the sign-out worked.
    console.error('Sign out failed; your session may still be open.', error)
    throw error
  }
  window.location.href = '/login'
}
