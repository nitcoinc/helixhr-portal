// Plan 2026-10-04-001 U3: a report's state <-> the route query, so a link
// reopens the same report (and saved views, U12, store the same shape).
//
//   ?<filter name>=<value>   one key per declared filter (People.vue's
//                            `?employee=` deep link is one of these)
//   ?group=a,b               up to two group-by fields, in order
//   ?sort=field | -field     ascending | descending
//   ?hide=a,b                hidden columns (resolved decision 10); absent =
//                            the report's defaults (ID columns beside a name),
//                            `?hide=` = every column shown
//
// Everything is validated against the catalog entry's own specs; unknown
// keys and malformed values are dropped, never passed through. The server
// validates again -- this only keeps the URL honest.

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/
const FIELD_RE = /^[A-Za-z0-9_]{1,64}$/
const MAX_VALUE = 140

function first(value) {
  return Array.isArray(value) ? value[0] : value
}

function list(value) {
  const raw = first(value)
  if (typeof raw !== 'string' || !raw) return []
  return [...new Set(raw.split(',').filter((field) => FIELD_RE.test(field)))]
}

/** A single filter value, cleaned for its spec, or undefined. */
export function cleanFilterValue(spec, value) {
  const raw = first(value)
  if (raw === undefined || raw === null || raw === '') return undefined
  if (spec.type === 'toggle') {
    if (raw === true || raw === 1 || raw === '1') return 1
    if (raw === false || raw === 0 || raw === '0') return 0
    return undefined
  }
  if (typeof raw !== 'string' || raw.length > MAX_VALUE) return undefined
  if (spec.type === 'date') return DATE_RE.test(raw) ? raw : undefined
  if (spec.type === 'month') return MONTH_RE.test(raw) ? raw : undefined
  if (spec.type === 'select') return (spec.options || []).includes(raw) ? raw : undefined
  return raw
}

/** Route query -> `{filters, groupBy, sort, hidden, hasState}` for `entry`.
 * `hasState` is whether the link carried any report state at all (an
 * opened link auto-runs; a bare open waits for Run). */
export function fromQuery(query, entry) {
  const filters = {}
  for (const spec of entry.filters) {
    const value = cleanFilterValue(spec, query[spec.name])
    if (value !== undefined) filters[spec.name] = value
  }
  const groupable = new Set((entry.group_by || []).map((group) => group.field))
  const groupBy = list(query.group).filter((field) => groupable.has(field)).slice(0, 2)

  let sort = null
  const rawSort = first(query.sort)
  if (typeof rawSort === 'string') {
    const desc = rawSort.startsWith('-')
    const field = desc ? rawSort.slice(1) : rawSort
    if (FIELD_RE.test(field)) sort = { field, order: desc ? 'desc' : 'asc' }
  }

  // null = no choice made yet: the page applies the columns' `default_hidden`.
  const hidden = query.hide === undefined ? null : list(query.hide)
  const hasState = Object.keys(filters).length > 0 || groupBy.length > 0 || sort !== null
  return { filters, groupBy, sort, hidden, hasState }
}

/** `{filters, groupBy, sort, hidden}` -> a route query object. Empty values
 * are omitted so the URL stays short. */
export function toQuery(state, entry) {
  const query = {}
  for (const spec of entry.filters) {
    const value = cleanFilterValue(spec, state.filters?.[spec.name])
    if (value !== undefined) query[spec.name] = String(value)
  }
  if (state.groupBy?.length) query.group = state.groupBy.slice(0, 2).join(',')
  if (state.sort?.field) query.sort = `${state.sort.order === 'desc' ? '-' : ''}${state.sort.field}`
  if (Array.isArray(state.hidden)) query.hide = state.hidden.join(',')
  return query
}
