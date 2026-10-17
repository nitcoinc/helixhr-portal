// Plan 2026-10-17-001 U8. The pure half of the Requests workspaces.

/** Bytes as a person reads them. Only ever used next to a file name. */
export function fileSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** KTD12, the one "owed by the employee" rule Home also applies: the
 * request waits on them, or holds an unread message from the team that
 * handles it. An unread status change alone is an update, not an ask.
 * The server groups by the same rule (`_my_request_groups`); the page uses
 * this to tell whether a row it has just read still belongs under Needs you. */
export function isOwed(row) {
  return row?.status === 'Waiting on Employee' || Boolean(row?.unread_reply)
}

const GROUPS = [
  ['needs_you', 'Needs you'],
  ['open', 'Open'],
  ['closed', 'Closed'],
]

/** Needs you / Open / Closed, in that order, empty groups dropped, from the
 * server's `groups` (`get_my_requests`). Each group arrives already ordered
 * and counted over every matching request -- Closed most recently closed
 * first -- so this only labels them and says how many more each holds beyond
 * the rows it shows. */
export function myRequestSections(groups) {
  return GROUPS.map(([key, label]) => {
    const rows = groups?.[key]?.rows || []
    const more = Math.max(0, (groups?.[key]?.total || 0) - rows.length)
    return { key, label, rows, more, limit: groups?.[key]?.limit || 0 }
  }).filter((section) => section.rows.length)
}

/** The inbox's SLA pill (AI-17): how far past, when the server says. */
export function slaOverLabel(days) {
  return days > 0 ? `${days} d over` : 'Past SLA'
}

/** Wrap a frappe-ui `resourceFetcher` so overlapping calls all settle with
 * the newest call's answer (or its error). createResource keeps whichever
 * response lands last, so a slow Show more could otherwise overwrite the
 * search typed after it. A superseded call's own failure is dropped too. */
export function latestOnly(fetcher) {
  let latest = null
  return async (options) => {
    let mine = fetcher(options)
    latest = mine
    for (;;) {
      try {
        const data = await mine
        if (mine === latest) return data
      } catch (error) {
        if (mine === latest) throw error
      }
      mine = latest
    }
  }
}
