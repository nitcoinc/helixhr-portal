// Plan 2026-10-17-001 U5 (R7, KTD2, KTD13). What the command palette offers,
// as plain functions so the gates are testable without a browser.
//
// Pages come from `allowedNav(session)` -- exactly the rail's gates -- plus the
// two answers only `router.beforeEach` gives: a session that is neither ready
// nor desk-only has no pages, and a templates-only Portal Admin (desk-only,
// no Desk) is pinned to Email templates. None of it is authorization; every
// page's server method refuses on its own.
//
// Loaded only by CommandPalette.vue, so none of this reaches the eager chunk.
import { allowedNav, askHrTarget } from './nav'
import { resolveStatus } from './statusBadge'
import { PALETTE_RECENTS_PREFIX } from './viewerStorage'

export const PALETTE_MIN_QUERY = 2
export const PALETTE_RECENTS_MAX = 5

const RECENT_TYPES = ['page', 'action', 'report', 'person', 'request', 'inbox']
const TYPE_ICONS = { person: 'users', request: 'requests', inbox: 'requests', report: 'reports' }

/** `{ path, query }` (or a plain path) as the one string a recent stores. */
function routeString(target) {
  if (typeof target === 'string') return target
  const query = new URLSearchParams(target.query || {}).toString()
  return query ? `${target.path}?${query}` : target.path
}

const basePath = (route) => route.split(/[?#]/)[0]

/** The offered page `route` lives under, if any. Home matches only itself. */
function pageFor(route, pages) {
  const base = basePath(route)
  return pages.find((page) =>
    page.path === '/' ? base === '/' : base === page.path || base.startsWith(`${page.path}/`),
  )
}

/** The pages this session's palette offers, in rail order. */
export function palettePages(session) {
  if (session.status !== 'ready' && session.status !== 'desk-only') return []
  const items = allowedNav(session)
  // router.beforeEach: a Portal Admin with no Employee and no Desk lands on
  // Email templates from every route, so that is the one page to offer.
  const pinned =
    session.status === 'desk-only' && session.canEditEmailTemplates && !session.deskUrl
  return items
    .filter((item) => !pinned || item.to === '/email-templates')
    .map((item) => ({
      id: `page:${item.to}`,
      type: 'page',
      label: item.label,
      icon: item.icon,
      path: item.to,
      route: routeString(item.query ? { path: item.to, query: item.query } : item.to),
    }))
}

/** The top actions, each offered only when its page is (no Check in yet). */
export function paletteActions(session) {
  const pages = new Set(palettePages(session).map((page) => page.path))
  const ask = askHrTarget(session)
  return [
    { label: 'Ask for leave', path: '/leave', icon: 'leave', route: '/leave?ask=1' },
    { label: 'Fill timesheet', path: '/timesheet', icon: 'timesheet', route: '/timesheet' },
    // The Requests page's own `?category` prefill, the same one Ask HR uses.
    { label: 'New request', path: '/requests', icon: 'requests', route: ask && routeString(ask) },
  ]
    .filter((action) => action.route && pages.has(action.path))
    .map((action) => ({ id: `action:${action.label}`, type: 'action', ...action }))
}

/** One `get_report_catalog` answer as palette items. */
export function paletteReports(catalog) {
  return (Array.isArray(catalog) ? catalog : []).map((entry) => ({
    id: `report:${entry.key}`,
    type: 'report',
    label: entry.label,
    hint: entry.question || '',
    icon: 'reports',
    route: `/reports/${encodeURIComponent(entry.key)}`,
  }))
}

const fold = (text) =>
  String(text || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLocaleLowerCase()

/** `items` whose label (or hint) contains `query`, best first: a label that
 * starts with it, then one with a word that does, then any label match,
 * then a hint match. Order within a rank is kept. */
export function matchItems(items, query) {
  const needle = fold(query).trim()
  if (!needle) return []
  const rank = (item) => {
    const label = fold(item.label)
    if (label.startsWith(needle)) return 0
    if (label.split(/[\s/·-]+/).some((word) => word.startsWith(needle))) return 1
    if (label.includes(needle)) return 2
    if (fold(item.hint).includes(needle)) return 3
    return -1
  }
  return items
    .map((item, index) => ({ item, index, score: rank(item) }))
    .filter((entry) => entry.score >= 0)
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .map((entry) => entry.item)
}

/** `portal_search`'s answer as palette groups. A person opens People for a
 * session that has it, otherwise the directory filtered to their name; an
 * own request opens the employee view and an inbox one the handler view
 * (the two views Notifications.vue resolves between). */
export function serverGroups(result, session) {
  const people = (result?.people || []).map((person) => ({
    id: `person:${person.name}`,
    type: 'person',
    label: person.employee_name || person.name,
    hint: [person.designation, person.department, person.company].filter(Boolean).join(' · '),
    icon: 'users',
    route: session.canSeePeople
      ? `/people/${encodeURIComponent(person.name)}`
      : `/directory?q=${encodeURIComponent(person.employee_name || '')}`,
  }))
  const request = (row, audience) => ({
    id: `${audience === 'handler' ? 'inbox' : 'request'}:${row.name}`,
    type: audience === 'handler' ? 'inbox' : 'request',
    label: row.subject || row.name,
    hint: [row.category, resolveStatus({ kind: 'request', status: row.status, audience }).label]
      .filter(Boolean)
      .join(' · '),
    icon: 'requests',
    route: `/requests/${encodeURIComponent(row.name)}${audience === 'handler' ? '?tab=work' : ''}`,
  })
  return [
    { id: 'people', heading: 'People', items: people },
    { id: 'requests', heading: 'Your requests', items: (result?.requests || []).map((row) => request(row, 'employee')) },
    { id: 'inbox', heading: 'Team inbox', items: (result?.inbox || []).map((row) => request(row, 'handler')) },
  ].filter((group) => group.items.length)
}

// --- recents (KTD13) --------------------------------------------------------

function storageKey(user) {
  return user ? `${PALETTE_RECENTS_PREFIX}${user}` : ''
}

// Resolved inside each try: with site data blocked, reading
// `window.localStorage` itself throws.
function resolve(storage) {
  return storage === undefined ? globalThis.localStorage : storage
}

function readRaw(user, storage) {
  const key = storageKey(user)
  if (!key) return []
  try {
    const value = JSON.parse(resolve(storage).getItem(key) || '[]')
    return Array.isArray(value)
      ? value.filter(
          (item) =>
            item &&
            RECENT_TYPES.includes(item.type) &&
            typeof item.route === 'string' &&
            item.route.startsWith('/') &&
            typeof item.label === 'string',
        )
      : []
  } catch {
    return []
  }
}

function write(user, list, storage) {
  const key = storageKey(user)
  if (!key) return
  try {
    resolve(storage).setItem(key, JSON.stringify(list.slice(0, PALETTE_RECENTS_MAX)))
  } catch {
    // Storage blocked or full: recents are a convenience, never a failure.
  }
}

/** Whether `session` still holds the group a recent came from: its page is
 * still offered, and a report or inbox recent still has its own flag. */
export function recentGranted(recent, session) {
  if (!pageFor(recent.route, palettePages(session))) return false
  if (recent.type === 'report') return !!session.canRunReports
  if (recent.type === 'inbox') return !!session.canHandleRequests
  return true
}

/** This viewer's recents the session still grants, as palette items. */
export function readRecents(session, storage) {
  const pages = palettePages(session)
  return readRaw(session.user, storage)
    .filter((recent) => recentGranted(recent, session))
    .map((recent) => ({
      ...recent,
      id: `recent:${recent.route}`,
      icon: TYPE_ICONS[recent.type] || pageFor(recent.route, pages)?.icon || 'search',
    }))
}

/** Moves `item` to the front of this viewer's recents. Only `{type, route,
 * label}` is stored -- never a status, an id beyond the route, or a hint. */
export function recordRecent(user, item, storage) {
  const entry = { type: item.type, route: item.route, label: item.label }
  write(user, [entry, ...readRaw(user, storage).filter((recent) => recent.route !== entry.route)], storage)
}

/** Drops the recent for `route` (it now lands on a refusal or not-found). */
export function forgetRecent(user, route, storage) {
  write(user, readRaw(user, storage).filter((recent) => recent.route !== route), storage)
}
