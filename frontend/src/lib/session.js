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
   * 'desk-only'   signed in, no active Employee, but an HR or System
   *               Manager (`desk_url` set): the admin pages and a way to
   *               Desk, never the "not set up" page
   * 'unavailable' the request failed. NOT the same thing as 'not-linked',
   *               which is the whole point: a service failure used to be
   *               rendered as "your account is not set up" (P2-U2 sc. 3).
   */
  status: 'idle',
  employee: null,
  user: null,
  canApprove: false,
  /** P3-KTD11: gates the Team nav item. Direct reports, not approval
   * capability -- a leave approver with no reports has no team to show. */
  hasReports: false,
  /** P5-U11: a routed-role holder (IT Team today) has requests to work even
   * when `canApprove`'s queue-derived half is momentarily empty -- the same
   * "present regardless of a pending row" rule P4-R13 already gives HR. */
  canWorkRequests: false,
  /** Plan 2026-10-04-002 U4/KTD6: gates the Requests page's "To work on"
   * tab -- wider than `canWorkRequests` (Approvals nav), which only covers a
   * non-HR routed role. Mirrors the server's `_is_hr() or
   * _holds_routed_role()` gate exactly, so the tab and the feed's refusal
   * can never disagree. */
  canHandleRequests: false,
  /** P5-U14: gates the Settings nav item. Mirrors the exact predicate
   * `helixhr.api.get_portal_config` itself enforces server-side, so the nav
   * item and the server's own gate can never disagree. */
  canConfigure: false,
  canManageNotifications: false,
  /** P5-U15: gates the Organisation nav item. Mirrors the exact predicate
   * `helixhr.api.get_organisation_view` itself enforces server-side, so the
   * nav item and the server's own gate can never disagree. */
  canSeeOrganisation: false,
  /** P6-U4: gates the People nav item. Mirrors `resolve_admin_scope`'s own
   * gate on `search_people`/`get_person`. */
  canSeePeople: false,
  /** P7-U5: gates the Projects nav item. Mirrors `resolve_project_scope`'s own
   * gate on `search_projects`/`get_project`. */
  canSeeProjects: false,
  /** Plan 2026-10-04-001 U4: gates the Reports nav item. True when
   * `get_report_catalog` would list at least one entry for this caller. */
  canRunReports: false,
  /** Portal Admin, HR Manager or System Manager: the access matrix, export
   * log and Portal roles. Mirrors `can_admin_portal`, which those endpoints
   * enforce. */
  canAdminPortal: false,
  /** P6-U4: whether this caller can actually reach Desk (a System User
   * holding a `desk_access` role) -- decides whether a Desk link is drawn
   * anywhere in the portal, never whether one works: every method that
   * hands one out checks this again itself (P6-KTD4). */
  canOpenDesk: false,
  /** Where the shell's "Open Desk" button goes, or null to draw none. The
   * server sets it for HR and System Manager roles only -- an ordinary
   * employee is a System User too, and must not be shown a door to Desk. */
  deskUrl: null,
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
  if (['ready', 'not-linked', 'desk-only'].includes(state.status)) {
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
  state.user = boot?.user || null
  state.canApprove = !!boot?.can_approve
  state.hasReports = !!boot?.has_reports
  state.canWorkRequests = !!boot?.can_work_requests
  state.canHandleRequests = !!boot?.can_handle_requests
  state.canConfigure = !!boot?.can_configure
  state.canManageNotifications = !!boot?.can_manage_notifications
  state.canSeeOrganisation = !!boot?.can_see_organisation
  state.canSeePeople = !!boot?.can_see_people
  state.canSeeProjects = !!boot?.can_see_projects
  state.canRunReports = !!boot?.can_run_reports
  state.canAdminPortal = !!boot?.can_admin_portal
  state.canOpenDesk = !!boot?.can_open_desk
  state.deskUrl = boot?.desk_url || null
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
  if (employee?.name) state.status = 'ready'
  // Plan 2026-10-02-001 U7: a Notification Manager has no Desk, but the
  // Email templates page is scoped by role, not by Employee.
  else if (state.deskUrl || state.canManageNotifications) state.status = 'desk-only'
  else state.status = 'not-linked'
}

/** Plan 2026-09-30-001 U5: Profile changed the caller's own photo, so the
 * shell avatar follows without another bootstrap. `url` is the versioned
 * URL `upload_my_photo` returned, or null after `remove_my_photo`. */
export function setMyPhoto(url) {
  if (state.employee) state.employee = { ...state.employee, photo_url: url || null }
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
