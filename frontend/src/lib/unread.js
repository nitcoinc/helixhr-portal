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

export function watchUnread() {
  subscribers += 1
  if (!poll) {
    poll = setInterval(() => unreadCount.reload(), 60000)
  }
}

export function unwatchUnread() {
  subscribers = Math.max(0, subscribers - 1)
  if (subscribers === 0 && poll) {
    clearInterval(poll)
    poll = null
  }
}
