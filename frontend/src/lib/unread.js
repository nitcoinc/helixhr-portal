import { createResource } from 'frappe-ui'
import { session } from './session'

// One shared unread count. The bell (mobile app bar) and the sidebar's
// Notifications item both show this number, so it lives here rather than in
// either component.
//
// P2-U4 / P2-R21. Two things changed here, both about round trips:
//
//   * No first fetch. `get_portal_bootstrap` already answers "how many
//     unread" (P2-KTD7) and the shell falls back to `session.unread` while
//     `data` is null, so the badge is right on the first painted frame. The
//     fetch this module used to make on mount was the *third* application
//     request on a cold Dashboard load, against a budget of two.
//   * A count query, not a list. It asked for every unread row's name and
//     counted the array in a transform -- 250 rows to learn one integer on
//     the U0 baseline profile (P2-R22: count-only UI uses count queries).
//
// frappe.client.get_count scopes Notification Log to the session user
// through the doctype's own permission query (`for_user`), so no explicit
// filter for that is needed.
export const unreadCount = createResource({
  url: 'frappe.client.get_count',
  params: {
    doctype: 'Notification Log',
    filters: { read: 0 },
  },
})

/** The count as the shell renders it: the poll's answer if it has one,
 * otherwise the bootstrap's. */
export function currentUnread() {
  return unreadCount.data ?? session.unread ?? 0
}

/**
 * Set the count from something that just happened, without waiting for the
 * next poll (P2-R13). Reading one notification and Mark all read both have
 * to move the badge inside the same interaction.
 */
export function setUnread(count) {
  unreadCount.setData(Math.max(0, count))
}

let poll = null
let subscribers = 0
let listening = false
let wasVisible = true

// P2-U9 step 2. A background tab is not a user, and a 60s timer that keeps
// firing in one is pure cost: on a phone it is the thing that wakes the
// radio while the portal is not on screen, and on a desktop with the portal
// parked in a tab it is a request a minute forever for a number nobody is
// reading.
//
// So: the interval only exists while the document is visible, and coming
// back reloads *once* rather than waiting up to a minute for the badge to
// become true again (P2-R13). `visibilitychange` is the event that actually
// fires on a phone -- `focus` alone misses an app switch on iOS -- and
// `focus` covers the desktop case of returning to an already-visible tab
// from another window. Both funnel into the same two functions, and both
// `startPolling` and `unwatchUnread` are idempotent, so a second call can
// never leave two timers running, and the catch-up read is gated on the
// hidden -> visible *transition* rather than on the event: a phone that
// delivers both `visibilitychange` and `focus` on one return still costs
// exactly one request (P2-U9 scenario 3).
const POLL_MS = 60000

function isVisible() {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden'
}

function startPolling() {
  if (poll || !subscribers || !isVisible()) return
  poll = setInterval(() => unreadCount.reload(), POLL_MS)
}

function stopPolling() {
  if (!poll) return
  clearInterval(poll)
  poll = null
}

function onVisibilityChange() {
  if (!subscribers) return
  const visible = isVisible()
  if (visible === wasVisible) return
  wasVisible = visible
  if (!visible) {
    stopPolling()
    return
  }
  // One catch-up read, then resume the timer from now rather than from
  // whenever the hidden interval would have fired.
  unreadCount.reload()
  startPolling()
}

export function watchUnread() {
  subscribers += 1
  wasVisible = isVisible()
  if (!listening && typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('focus', onVisibilityChange)
    listening = true
  }
  startPolling()
}

export function unwatchUnread() {
  subscribers = Math.max(0, subscribers - 1)
  if (subscribers > 0) return
  stopPolling()
  if (listening && typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    window.removeEventListener('focus', onVisibilityChange)
    listening = false
  }
}
