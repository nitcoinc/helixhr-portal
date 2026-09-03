import { createResource } from 'frappe-ui'

// One shared unread count with one poller. The bell (mobile app bar) and
// the sidebar's Notifications item both show this number; before the app
// shell existed only the bell needed it, and a per-component resource
// would now mean two components polling the same query every minute.
//
// frappe.client.get_list already scopes Notification Log to the session
// user via its own `for_user` permission -- no explicit filter needed.
export const unreadCount = createResource({
  url: 'frappe.client.get_list',
  params: {
    doctype: 'Notification Log',
    filters: { read: 0 },
    fields: ['name'],
    limit_page_length: 0,
  },
  // Deliberately not `auto: true`: frappe-ui hangs auto-fetch off the
  // owning component's onMounted, and a resource created at module scope
  // has no owning component -- it would silently never fetch (the badge
  // stayed blank with five unread notifications sitting in the API).
  // watchUnread() below does the first fetch explicitly.
  transform: (rows) => rows.length,
})

let poll = null
let subscribers = 0

export function watchUnread() {
  subscribers += 1
  unreadCount.fetch()
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
