// Plan 2026-10-17-001 U11 (HR-16, KTD13). The reports this viewer ran most
// recently, newest first, on this device only. Report keys only: the label
// and the access come from the server's catalog every time, so a report the
// viewer can no longer run simply drops out of the strip.
//
// The key includes the user so a shared browser never shows one account's
// recents to the next. Sign-out clearing and the bootstrap purge of other
// users' keys are U4/U5's; they find these keys by `RECENT_REPORTS_PREFIX`.
// Every storage access is in try/catch: `null` means "storage unavailable",
// and the strip hides rather than guessing.
export const RECENT_REPORTS_PREFIX = 'helixhr.reports.recent:'
export const RECENT_REPORTS_MAX = 4

function storageKey(user) {
  return user ? `${RECENT_REPORTS_PREFIX}${user}` : ''
}

// Resolved inside each try: in a browser with site data blocked, merely
// reading `window.localStorage` throws.
function resolve(storage) {
  return storage === undefined ? globalThis.localStorage : storage
}

/** This user's recent report keys, or `null` when there is no user or the
 * storage cannot be read. */
export function readRecentReports(user, storage) {
  const key = storageKey(user)
  if (!key) return null
  let raw
  try {
    raw = resolve(storage).getItem(key)
  } catch {
    return null
  }
  if (raw == null) return []
  try {
    const value = JSON.parse(raw)
    return Array.isArray(value)
      ? value.filter((item) => typeof item === 'string').slice(0, RECENT_REPORTS_MAX)
      : []
  } catch {
    // Garbled, but the storage works: start over, and the next run repairs it.
    return []
  }
}

/** Moves `reportKey` to the front and returns the new list, or `null` when
 * the write failed (the strip then hides, as on a failed read). */
export function recordRecentReport(user, reportKey, storage) {
  const current = readRecentReports(user, storage)
  if (current === null || !reportKey) return current
  const next = [reportKey, ...current.filter((item) => item !== reportKey)].slice(0, RECENT_REPORTS_MAX)
  try {
    resolve(storage).setItem(storageKey(user), JSON.stringify(next))
    return next
  } catch {
    return null
  }
}
