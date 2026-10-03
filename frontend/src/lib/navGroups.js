// Plan 2026-10-02-001 U15 (R8, R9): the shell's navigation, sectioned.
// Order here is render order. A group with no `label` is pinned: it renders
// without a heading. Only `collapsible` groups get a toggle, and only in the
// desktop rail -- the phone More sheet always shows every group open.
export const NAV_GROUPS = [
  { id: 'pinned', label: '' },
  { id: 'work', label: 'My work' },
  { id: 'pay', label: 'Pay & policies' },
  { id: 'people', label: 'People & team' },
  { id: 'hr', label: 'HR', collapsible: true },
  { id: 'admin', label: 'Admin', collapsible: true },
  { id: 'bottom', label: '' },
]

/** Bucket already-gated nav items into `NAV_GROUPS` order, keeping each
 * item's own order and dropping groups the gates left empty (R8). */
export function groupNav(items, groups = NAV_GROUPS) {
  return groups
    .map((group) => ({ ...group, items: items.filter((item) => item.group === group.id) }))
    .filter((group) => group.items.length > 0)
}

const STORAGE_KEY = 'helixhr.nav.collapsed'

/** Collapsed group ids remembered on this device. With nothing remembered
 * -- first visit, or storage absent, throwing or garbled -- every
 * collapsible group starts collapsed: that is what keeps an HR plus System
 * Manager rail inside an 800px-tall window without scrolling. */
export function readCollapsed(storage = globalThis.localStorage, groups = NAV_GROUPS) {
  const fallback = groups.filter((group) => group.collapsible).map((group) => group.id)
  try {
    const raw = storage?.getItem(STORAGE_KEY)
    if (raw == null) return fallback
    const value = JSON.parse(raw)
    return Array.isArray(value) ? value.filter((id) => typeof id === 'string') : fallback
  } catch {
    return fallback
  }
}

export function writeCollapsed(ids, storage = globalThis.localStorage) {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(ids))
  } catch {
    // Not remembered on this device; the toggle still works for this view.
  }
}
