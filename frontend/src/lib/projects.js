// Plan 2026-10-31-001 U2/U3. The pure half of the Projects pages: the list's
// filters and their round trip through the route query, its sort, and the
// "paste several tasks" parser. `search_projects` returns the caller's whole
// list (KTD2), so all of it runs on what is loaded.
import { filterByField, sortRows } from './lists'

/** The list's filters with nothing chosen. `billable` is `'yes'`, `'no'` or
 * null; the rest are a row's own value or null. */
export const NO_FILTERS = Object.freeze({ q: '', status: null, type: null, billable: null, company: null })

const BILLABLE = ['yes', 'no']
// A query value is the user's own text from a URL, so it is bounded like the
// search field that writes it.
const QUERY_MAX = 140

function queryText(value) {
  const text = Array.isArray(value) ? value[0] : value
  return typeof text === 'string' ? text.slice(0, QUERY_MAX) : ''
}

/** Filters from a route query (`?q=&status=&type=&billable=&company=`).
 * Anything malformed reads as not chosen. */
export function filtersFromQuery(query = {}) {
  const pick = (key) => queryText(query[key]) || null
  const billable = pick('billable')
  return {
    q: queryText(query.q),
    status: pick('status'),
    type: pick('type'),
    billable: BILLABLE.includes(billable) ? billable : null,
    company: pick('company'),
  }
}

/** The route query for `filters`: only the chosen ones, so a list with no
 * filter has a bare URL. */
export function filtersToQuery(filters) {
  const query = {}
  const q = (filters?.q || '').trim()
  if (q) query.q = q
  for (const key of ['status', 'type', 'billable', 'company']) {
    if (filters?.[key]) query[key] = filters[key]
  }
  return query
}

/** True when any filter narrows the list. */
export function isFiltered(filters) {
  return Object.keys(filtersToQuery(filters)).length > 0
}

/** The company filter is only offered when the list spans companies. */
export function showCompanyFilter(rows) {
  return new Set((rows || []).map((row) => row?.company).filter(Boolean)).size > 1
}

/** Distinct non-blank values of `field`, A-Z, for a filter select. */
export function distinctValues(rows, field) {
  const values = new Set((rows || []).map((row) => row?.[field]).filter(Boolean))
  return [...values].sort((a, b) => String(a).localeCompare(String(b)))
}

/** `rows` narrowed by every chosen filter. Text matches the name or the
 * project code. A company filter is ignored while the list has one company,
 * since its control is not shown (a stale link must not hide everything). */
export function filterProjects(rows, filters) {
  const needle = (filters?.q || '').trim().toLowerCase()
  let out = needle
    ? (rows || []).filter((row) =>
        [row?.project_name, row?.name].some((value) => String(value || '').toLowerCase().includes(needle)),
      )
    : rows || []
  out = filterByField(out, 'status', filters?.status ?? null)
  out = filterByField(out, 'project_type', filters?.type ?? null)
  if (filters?.billable) out = out.filter((row) => !!row?.billable === (filters.billable === 'yes'))
  if (filters?.company && showCompanyFilter(rows)) out = filterByField(out, 'company', filters.company)
  return out
}

// "Everyone" outranks any member count, so it sorts as the largest.
const EVERYONE = Number.MAX_SAFE_INTEGER

/** The table's sortable columns: the row field each sorts by, and the order
 * a first click gives (text A-Z; counts and dates largest/latest first). */
export const PROJECT_SORTS = {
  name: { field: 'project_name', first: 'asc' },
  status: { field: 'status', first: 'asc' },
  members: { field: '_members', first: 'desc' },
  tasks: { field: 'open_task_count', first: 'desc' },
  end: { field: 'expected_end_date', first: 'desc' },
}

/** A sorted copy of `rows` for the table's `{ key, order }`. */
export function sortProjects(rows, sort) {
  const spec = PROJECT_SORTS[sort?.key] || PROJECT_SORTS.name
  const keyed =
    spec.field === '_members'
      ? (rows || []).map((row) => ({ ...row, _members: row?.open_to_all ? EVERYONE : row?.member_count }))
      : rows
  return sortRows(keyed, spec.field, sort?.order || spec.first)
}

/** The members cell: "Everyone" for an open project, else the count. A row
 * created on this page has no count yet, so it shows none. */
export function membersLabel(row) {
  if (row?.open_to_all) return 'Everyone'
  return row?.member_count == null ? '' : String(row.member_count)
}

export const TASK_PASTE_LIMIT = 100

/** One task per non-blank line of `text`: trimmed, blanks dropped, a line
 * repeating an earlier one (ignoring case) dropped. `subjects` is capped at
 * `TASK_PASTE_LIMIT`; `total` is how many distinct lines there were, so the
 * caller can refuse a paste that is too long rather than drop lines. */
export function parseTaskLines(text) {
  const seen = new Set()
  const unique = []
  let duplicates = 0
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const key = line.toLowerCase()
    if (seen.has(key)) {
      duplicates += 1
      continue
    }
    seen.add(key)
    unique.push(line)
  }
  return { subjects: unique.slice(0, TASK_PASTE_LIMIT), total: unique.length, duplicates }
}

/** `rows` whose `field` contains `needle`, ignoring case. */
export function matchRows(rows, needle, field) {
  const text = (needle || '').trim().toLowerCase()
  if (!text) return rows || []
  return (rows || []).filter((row) => String(row?.[field] || '').toLowerCase().includes(text))
}

// Plan 2026-11-01-001 U4 (R5). What the member picker offers. "Covered" is
// the server's own rule (`_covered_companies`): `get_project.open_companies`
// is non-empty only while the project is Open and "Open to everyone", and
// `add_project_members` skips the same people as "can already book".

/** The picker's people page, before members are dropped on the client. */
export const PICKER_PAGE = 20
/** `get_directory`'s own cap (`_DIRECTORY_MAX_PAGE`). */
export const DIRECTORY_MAX_PAGE = 200

/** How many `get_directory` rows to ask for so that, once members are
 * dropped, a page of `PICKER_PAGE` can still be left: `min(200, 20 + n)`. */
export function pickerSearchLimit(memberCount) {
  return Math.min(DIRECTORY_MAX_PAGE, PICKER_PAGE + Math.max(0, Number(memberCount) || 0))
}

/** "A", "A and B", "A, B and C". */
export function listWords(items) {
  const list = items || []
  if (list.length < 2) return list.join('')
  return `${list.slice(0, -1).join(', ')} and ${list.at(-1)}`
}

/** What to offer, and what was hidden and why. Departments whose company is
 * covered are left out (`hiddenCompanies` names those companies, A-Z).
 * People already on the project are left out and counted in
 * `hiddenMembers`. People results come from `get_directory`, the caller's
 * own company, so when `callerCompany` is covered every one of them can
 * already book: `peopleCovered`, and none is offered. `moreMatches` is true
 * when the server filled the page (`limit`), so a narrower search may find
 * people this one did not. */
export function pickerOffer({
  departments = [],
  people = [],
  memberIds = [],
  openCompanies = [],
  callerCompany = null,
  limit = 0,
} = {}) {
  const covered = new Set(openCompanies || [])
  const offeredDepartments = []
  const hidden = new Set()
  for (const row of departments || []) {
    if (row?.company && covered.has(row.company)) hidden.add(row.company)
    else offeredDepartments.push(row)
  }
  const peopleCovered = !!callerCompany && covered.has(callerCompany)
  const members = new Set(memberIds || [])
  const notMembers = (people || []).filter((person) => !members.has(person?.name))
  return {
    departments: offeredDepartments,
    hiddenDepartments: (departments || []).length - offeredDepartments.length,
    hiddenCompanies: [...hidden].sort((a, b) => String(a).localeCompare(String(b))),
    people: peopleCovered ? [] : notMembers,
    hiddenMembers: (people || []).length - notMembers.length,
    peopleCovered,
    moreMatches: limit > 0 && (people || []).length >= limit,
  }
}
