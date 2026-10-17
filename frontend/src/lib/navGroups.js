// Plan 2026-10-02-001 U15 (R8, R9): the shell's navigation, sectioned.
// Order here is render order. A group with no `label` is pinned: it renders
// without a heading. Only `collapsible` groups get a toggle, and only in the
// desktop rail -- the phone More sheet always shows every group open.
// `role` marks a group a session holds because of what it does at work, not
// because it is an employee (plan 2026-10-17-001 U4).
export const NAV_GROUPS = [
  { id: 'pinned', label: '' },
  { id: 'work', label: 'My work' },
  { id: 'pay', label: 'Pay & policies' },
  { id: 'people', label: 'People & team' },
  { id: 'hr', label: 'HR', collapsible: true, role: true },
  { id: 'admin', label: 'Admin', collapsible: true, role: true },
  { id: 'bottom', label: '' },
]

/** Bucket already-gated nav items into `NAV_GROUPS` order, keeping each
 * item's own order and dropping groups the gates left empty (R8). */
export function groupNav(items, groups = NAV_GROUPS) {
  return groups
    .map((group) => ({ ...group, items: items.filter((item) => item.group === group.id) }))
    .filter((group) => group.items.length > 0)
}

/**
 * The role group that is this session's own work (R8, HR-06, AI-15), or null
 * for an employee, a manager or a routed worker. HR when the session holds
 * any HR destination -- an HR Manager or System Manager holding Admin too
 * still works in HR -- except a Portal Admin who is not HR, whose whole job
 * is Admin even when the export log puts Reports in the HR group.
 */
export function workGroup(session, groups) {
  const has = (id) => groups.some((group) => group.id === id)
  if (has('admin') && session.canAdminPortal && !session.canConfigure) return 'admin'
  if (has('hr')) return 'hr'
  return has('admin') ? 'admin' : null
}

/**
 * The desktop rail's groups for this session (R8): its own work group
 * directly under the pinned items, then the rest in `NAV_GROUPS` order.
 *
 * A session with a work group also gets "Pay & policies" as a collapsible
 * group -- the monthly errand, not the day's work -- which is what keeps the
 * longest rail inside 800px (row budget in AppShell.vue). An employee's rail
 * has no toggle at all, as before.
 */
export function railGroups(items, session) {
  const grouped = groupNav(items)
  const own = workGroup(session, grouped)
  const groups = grouped.map((group) => ({
    ...group,
    collapsible: !!group.collapsible || (own !== null && group.id === 'pay'),
  }))
  if (own) {
    const index = groups.findIndex((group) => group.id === own)
    const [mine] = groups.splice(index, 1)
    groups.splice(groups[0]?.id === 'pinned' ? 1 : 0, 0, mine)
  }
  return { groups, own }
}

/** First-visit collapsed groups: every collapsible group except the
 * session's own work group (MH-12, AI-15). A remembered state wins over this
 * (`readCollapsed`). */
export function defaultCollapsed(groups, own) {
  return groups.filter((group) => group.collapsible && group.id !== own).map((group) => group.id)
}

// KTD13: per-viewer, so a shared browser never hands one account's rail to
// the next. lib/viewerStorage.js clears and purges by this prefix.
export const NAV_COLLAPSED_PREFIX = 'helixhr.nav.collapsed:'

function storageKey(user) {
  return user ? `${NAV_COLLAPSED_PREFIX}${user}` : ''
}

/** Collapsed group ids this user left on this device, or `fallback` when
 * nothing usable is remembered -- first visit, no user, or storage absent,
 * throwing or garbled. Storage is resolved inside the try: with site data
 * blocked, merely reading `globalThis.localStorage` throws. */
export function readCollapsed(user, fallback, storage) {
  const key = storageKey(user)
  if (!key) return fallback
  try {
    const raw = (storage === undefined ? globalThis.localStorage : storage)?.getItem(key)
    if (raw == null) return fallback
    const value = JSON.parse(raw)
    return Array.isArray(value) ? value.filter((id) => typeof id === 'string') : fallback
  } catch {
    return fallback
  }
}

export function writeCollapsed(user, ids, storage) {
  const key = storageKey(user)
  if (!key) return
  try {
    const target = storage === undefined ? globalThis.localStorage : storage
    target?.setItem(key, JSON.stringify(ids))
  } catch {
    // Not remembered on this device; the toggle still works for this view.
  }
}
