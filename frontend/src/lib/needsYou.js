// Plan 2026-10-17-001 U6. The pure half of Home's "Needs you" region: where
// each list's "View all (N)" goes. N is the server's record count (KTD10
// collapses rows per record before it counts), so the page it opens has to
// be the one that owns those records for this person (EP-09).
import { HANDLER_INBOX } from './nav'

/** The queue's owner page by role. A plain employee has no Approvals page,
 * so their queue opens Notifications, every item addressed to them. */
export function queueDestination(session) {
  if (session?.canApprove) return '/approvals'
  if (session?.canHandleRequests || session?.canWorkRequests) {
    return HANDLER_INBOX
  }
  return '/notifications'
}

/** The page that owns one kind of row, by the kind's own prefix. */
export function kindPage(kind) {
  const value = String(kind || '')
  if (value.startsWith('attendance_request')) return '/attendance'
  if (value.startsWith('leave')) return '/leave'
  if (value.startsWith('request')) return '/requests'
  if (value.startsWith('timesheet')) return '/timesheet'
  return null
}

/** "Waiting on others" and "Decided" hold the employee's own leave,
 * attendance requests and requests. Their "View all" opens the page that
 * owns most of the rows shown, Leave when nothing says otherwise. */
export function sideListDestination(rows) {
  const tally = new Map()
  for (const row of rows || []) {
    const page = kindPage(row?.kind)
    if (page) tally.set(page, (tally.get(page) || 0) + 1)
  }
  let best = '/leave'
  let most = 0
  for (const [page, count] of tally) {
    if (count > most) {
      best = page
      most = count
    }
  }
  return best
}
