// Plan 2026-10-17-001 U8. The pure half of the Requests workspaces.

/** Bytes as a person reads them. Only ever used next to a file name. */
export function fileSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const CLOSED = ['Done', 'Rejected']

/** KTD12, the one "owed by the employee" rule Home also applies: the
 * request waits on them, or holds an unread message from the team that
 * handles it. An unread status change alone is an update, not an ask. */
export function isOwed(row) {
  return row?.status === 'Waiting on Employee' || Boolean(row?.unread_reply)
}

/** Most recently closed first: rows with a `closed_on` come first, newest
 * first; one closed before the date was stamped falls back to `creation`,
 * after the dated ones. The server's timestamps sort as strings. Stable. */
export function byClosedNewest(a, b) {
  const dated = Number(Boolean(b.closed_on)) - Number(Boolean(a.closed_on))
  if (dated) return dated
  const at = (row) => row.closed_on || row.creation || ''
  return at(b).localeCompare(at(a)) || (b.creation || '').localeCompare(a.creation || '')
}

/** Needs you / Open / Closed, in that order, empty groups dropped. A row
 * that is unread but not owed stays in its natural group with its dot
 * (ED-01, EP-04). Needs you and Open keep the server's order (newest sent
 * first); Closed reads most recently closed first, so a long-ago request
 * closed yesterday is not buried under ones sent since. */
export function myRequestGroups(rows) {
  const list = rows || []
  const rest = list.filter((row) => !isOwed(row))
  return [
    { key: 'needs-you', label: 'Needs you', rows: list.filter(isOwed) },
    { key: 'open', label: 'Open', rows: rest.filter((row) => !CLOSED.includes(row.status)) },
    { key: 'closed', label: 'Closed', rows: rest.filter((row) => CLOSED.includes(row.status)).sort(byClosedNewest) },
  ].filter((group) => group.rows.length)
}

/** The inbox's SLA pill (AI-17): how far past, when the server says. */
export function slaOverLabel(days) {
  return days > 0 ? `${days} d over` : 'Past SLA'
}
