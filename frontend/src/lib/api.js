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
    // P2-R25: carry *which* call failed on the error itself, so a section's
    // retry panel and anything that logs the failure can name it instead of
    // reporting an anonymous "something went wrong".
    if (error && !error.helixhrMethod) error.helixhrMethod = options?.url
    const requiresLogin =
      status === 401 ||
      LOGIN_REQUIRED_EXC_TYPES.includes(error?.exc_type) ||
      messages.some((m) => LOGIN_REQUIRED_MESSAGE.test(m))
    if (requiresLogin) {
      redirectToLogin()
      return Promise.reject(error)
    }
    // A stale CSRF token is the one failure a reload actually fixes, and
    // `exc_type` is the only reliable way to spot it. The `status === 417`
    // clause that used to sit here was inverted: Frappe's CSRFTokenError is
    // **400**, while 417 is plain ValidationError -- the status of every
    // `frappe.throw` this app makes. So every domain refusal ("this has
    // already been decided", "you do not have enough Casual Leave") reloaded
    // the page instead of being shown, which is exactly the "explain the
    // outcome" behaviour P2-R25 and P2-U7 step 4 ask for. Found while
    // building P2-U7's stale-decision path; belongs to P2-U2's api.js, fixed
    // here because no refusal message can reach any screen while it stands.
    if (error?.exc_type === 'CSRFTokenError') {
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

/**
 * Attach one private file to an HR Request the signed-in employee owns.
 *
 * frappe-ui's frappeRequest always JSON-encodes the body and forces a JSON
 * Content-Type header, so it can't carry multipart form data -- this goes
 * straight through fetch instead, letting the browser set its own multipart
 * boundary.
 *
 * P2-U8: the endpoint is the portal's own, not Frappe's generic
 * `upload_file`. That one gates on `write` permission for the target
 * document, and role Employee deliberately no longer has write on HR Request
 * -- so the ownership rule, the private flag, and the file type and size
 * policy the sheet promises all live in one session-scoped method (P2-R27).
 *
 * The failure carries `helixhrMethod` and a plain `messages` array, so a
 * failed attachment reads the same way in the UI as any other API failure.
 */
export async function attachToRequest(file, { name }) {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('name', name)

  const url = '/api/method/helixhr.api.attach_to_my_request'
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'X-Frappe-CSRF-Token': window.csrf_token,
    },
    body: formData,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const error = new Error(body?.exception || 'Upload failed')
    error.messages = body?._server_messages
      ? JSON.parse(body._server_messages).map((m) => {
          try {
            return JSON.parse(m).message
          } catch {
            return m
          }
        })
      : []
    error.exc_type = body?.exc_type
    error.response = response
    error.helixhrMethod = url
    throw error
  }
  return (await response.json()).message
}

/**
 * A write that must survive the page unloading immediately after it is
 * sent -- `fetch`'s own `keepalive` flag, which frappe-ui's resourceFetcher
 * does not set (frappe-ui/src/utils/request.js is a plain `fetch`).
 *
 * Reading an HR reply marks it read, and the very next thing a real person
 * does is often leave the page: opening the record it's about is an in-app
 * route change (safe -- the JS context and any pending fetch keep running),
 * but a refresh or a closed tab is a real navigation, which cancels an
 * ordinary in-flight fetch before the browser ever sends it. `keepalive`
 * is exactly the platform mechanism for "still deliver this even if the
 * document is gone" (confirmed: without it, the read-clearing call in
 * Requests.vue was reliably lost under a `page.goto` immediately after,
 * which is the same shape as a real refresh).
 */
export async function keepaliveRequest(method, params) {
  const response = await fetch('/api/method/' + method, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json; charset=utf-8',
      'X-Frappe-CSRF-Token': window.csrf_token,
    },
    body: JSON.stringify(params || {}),
    keepalive: true,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const error = new Error(body?.exception || response.statusText)
    error.exc_type = body?.exc_type
    error.response = response
    error.helixhrMethod = method
    throw error
  }
  return (await response.json()).message
}

// A dead session usually fails several in-flight requests at once. Without
// this latch each one reassigns window.location, and the destination the
// *last* one happened to compute is the one that wins -- so the requested
// page could be lost on the way to /login (P2-U2 scenario 3, 6).
let redirecting = false

function redirectToLogin() {
  if (redirecting) return
  redirecting = true
  // `redirect-to` is what preserves the destination through the login form:
  // Frappe's login page sends the user back here afterwards, and the full
  // portal path (including any exact-record route, P2-R12) is in it.
  const current = window.location.pathname + window.location.search
  window.location.href = `/login?redirect-to=${encodeURIComponent(current)}`
}
