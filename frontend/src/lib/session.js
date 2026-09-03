import { reactive, readonly } from 'vue'
import { apiRequest, call } from './api'

// The router guard already calls `hrms.api.get_current_employee_info` on
// every navigation to decide between the portal and NotLinked (R3). The
// app shell needs exactly the same record for its identity block, so it
// reads the guard's result from here instead of firing a second request
// per page load. The guard stays the authority on freshness -- nothing is
// cached past a navigation, so a session that dies mid-use is still
// caught on the next route change.
const state = reactive({
  employee: null,
  /** Direct reports, used to decide whether Approvals appears in the nav
   * (design system: "Nav item only appears for users with at least one
   * pending item or at least one report"). Null until first resolved. */
  reportCount: null,
})

export const session = readonly(state)

export function setEmployee(employee) {
  state.employee = employee || null
  if (!employee?.name) {
    state.reportCount = null
    return
  }
  // Chained off the guard rather than left to the shell's onMounted: the
  // app mounts before the first navigation guard resolves, so a shell
  // that asked for the count on mount always saw a null employee, gave
  // up, and never retried -- managers lost their Approvals nav item.
  ensureReportCount()
}

/** Counted once per session, not per navigation: who reports to whom
 * doesn't change while someone is using the portal, and the answer only
 * gates a nav item. */
export async function ensureReportCount() {
  if (state.reportCount !== null || !state.employee?.name) return
  try {
    state.reportCount = await call('frappe.client.get_count', {
      doctype: 'Employee',
      filters: { reports_to: state.employee.name, status: 'Active' },
    })
  } catch {
    // A failure here must not break navigation -- it only means the
    // Approvals item stays hidden for this session.
    state.reportCount = 0
  }
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
