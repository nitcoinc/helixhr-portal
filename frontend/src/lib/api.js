import { frappeRequest } from 'frappe-ui'

// frappe-ui's own `error.status` reads a property off the *body text
// string*, not the Response (see frappe-ui/src/utils/frappeRequest.js),
// so it's always undefined. Read the real HTTP status off
// `error.response`.
//
// A Guest calling an `allow_guest=False` whitelisted method (the normal
// case, and every method this app calls) gets a 403 `PermissionError`
// with the message "...Login to access...", NOT a 401 `AuthenticationError`
// -- Frappe has no separate "not authenticated" state, Guest is just
// another session. A *logged-in* user who lacks rights for a specific
// action ALSO gets a 403 PermissionError, but without that phrase --
// that one must NOT redirect to login, it's a real in-app error (e.g.
// R25/R26's "wrong manager" case). Match on the phrase, not the status
// code, to tell the two apart. Verified against this app's real 403
// response during U3 (see plan Appendix).
const LOGIN_REQUIRED_EXC_TYPES = ['AuthenticationError']
const LOGIN_REQUIRED_MESSAGE = /login to access/i

export function apiRequest(options) {
  return frappeRequest(options).catch((error) => {
    const status = error?.response?.status
    const messages = error?.messages || []
    const requiresLogin =
      status === 401 ||
      LOGIN_REQUIRED_EXC_TYPES.includes(error?.exc_type) ||
      messages.some((m) => LOGIN_REQUIRED_MESSAGE.test(m))
    if (requiresLogin) {
      redirectToLogin()
      return Promise.reject(error)
    }
    if (status === 417 || error?.exc_type === 'CSRFTokenError') {
      window.location.reload()
      return Promise.reject(error)
    }
    throw error
  })
}

/** One-off call to a whitelisted method, outside component context
 * (router guards, etc). Prefer frappe-ui's createResource/useDoc/useList
 * inside components for reactive state. */
export function call(method, params) {
  return apiRequest({
    url: method,
    params,
    method: params ? 'POST' : 'GET',
  })
}

function redirectToLogin() {
  const current = window.location.pathname + window.location.search
  window.location.href = `/login?redirect-to=${encodeURIComponent(current)}`
}
