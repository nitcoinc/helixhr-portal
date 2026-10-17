// Plan 2026-10-17-001 KTD13. Per-viewer conveniences live in localStorage
// under keys that end in `session.user`. This module is the one list of them,
// so sign-out can clear every one and a bootstrap can drop what another
// account left on a shared browser.
import { NAV_COLLAPSED_PREFIX } from './navGroups'

// Declared here rather than beside their readers, which load lazily: this
// module rides in the eager chunk with lib/session.js, and importing the
// palette's or Approvals' modules for one string would pull them in too.
/** U11: the Reports page's recently run strip (lib/recentReports.js). */
export const RECENT_REPORTS_PREFIX = 'helixhr.reports.recent:'
/** U5: the command palette's recents (lib/paletteSources.js). */
export const PALETTE_RECENTS_PREFIX = 'helixhr.palette.recent:'
/** U7: the Approvals keyboard-triage opt-in. */
export const APPROVALS_SHORTCUTS_PREFIX = 'helixhr.approvals.shortcuts:'

export const VIEWER_KEY_PREFIXES = [
  NAV_COLLAPSED_PREFIX,
  RECENT_REPORTS_PREFIX,
  PALETTE_RECENTS_PREFIX,
  APPROVALS_SHORTCUTS_PREFIX,
]

// Written before keys carried a user (plan 2026-10-02-001 U15): nobody's, so
// always purged.
const UNKEYED = ['helixhr.nav.collapsed']

/** The per-viewer keys to drop: every one when `keep` is null, otherwise
 * those that do not belong to `keep`. */
function viewerKeys(storage, keep) {
  const keys = []
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index)
    if (UNKEYED.includes(key)) {
      keys.push(key)
      continue
    }
    const prefix = VIEWER_KEY_PREFIXES.find((candidate) => key?.startsWith(candidate))
    if (prefix && (keep === null || key.slice(prefix.length) !== keep)) keys.push(key)
  }
  return keys
}

function remove(keep, storage) {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    if (!target) return
    for (const key of viewerKeys(target, keep)) target.removeItem(key)
  } catch {
    // Storage blocked or gone: there is nothing of ours in it to clear.
  }
}

/** Sign-out: every viewer's conveniences leave this browser. */
export function clearViewerStorage(storage) {
  remove(null, storage)
}

/** Bootstrap load: keep only `user`'s keys. A guest or an expired session
 * (`user` empty) keeps none. */
export function purgeOtherViewers(user, storage) {
  remove(user || null, storage)
}
