// Plan 2026-10-17-001 U17 (HR-14, HR-15, HR-18, MH-16). The pure half of
// the HR list pages -- People, Projects, Team and Roster. Every list here is
// one server response (People asks for up to 200 rows, Projects and the team
// week are whole), so sorting and filtering happen on what is loaded.

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

function blank(value) {
  return value === null || value === undefined || value === ''
}

/** A sorted copy of `rows` by `field`, `order` `'asc'` or `'desc'`. Blank
 * values go last in both orders; text compares case-insensitively with
 * numbers in order ("Project 2" before "Project 10"); ISO dates compare as
 * text, which is calendar order. */
export function sortRows(rows, field, order = 'asc') {
  const direction = order === 'desc' ? -1 : 1
  return [...(rows || [])].sort((a, b) => {
    const left = a?.[field]
    const right = b?.[field]
    if (blank(left) || blank(right)) return blank(left) - blank(right)
    if (typeof left === 'number' && typeof right === 'number') return (left - right) * direction
    return collator.compare(String(left), String(right)) * direction
  })
}

/** Filter-chip options for `field`: "All" first, then each value A-Z with
 * how many rows carry it. A blank value becomes one `blankLabel` chip
 * (value `''`), last; with no `blankLabel` blanks get no chip. */
export function fieldOptions(rows, field, { allLabel = 'All', blankLabel = '' } = {}) {
  const counts = new Map()
  let blanks = 0
  for (const row of rows || []) {
    const value = row?.[field]
    if (blank(value)) blanks += 1
    else counts.set(value, (counts.get(value) || 0) + 1)
  }
  const options = [...counts.keys()]
    .sort((a, b) => collator.compare(String(a), String(b)))
    .map((value) => ({ value, label: String(value), count: counts.get(value) }))
  if (blanks && blankLabel) options.push({ value: '', label: blankLabel, count: blanks })
  return [{ value: null, label: allLabel, count: (rows || []).length }, ...options]
}

/** `rows` narrowed to `field === value`; `null` keeps every row and `''`
 * keeps the blanks (the `blankLabel` chip above). */
export function filterByField(rows, field, value) {
  if (value === null || value === undefined) return rows || []
  return (rows || []).filter((row) => (value === '' ? blank(row?.[field]) : row?.[field] === value))
}

/** True when an employee number is only the name again in lower-kebab case
 * ("Bulk-leave-2302" / "bulk-leave-2302", HR-18), so the list can drop it. */
export function employeeNumberAddsNothing(row) {
  const number = String(row?.employee_number || '').trim().toLowerCase()
  if (!number) return true
  const slug = String(row?.employee_name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
  return number === slug
}

/** People with any leave in view first -- approved or still waiting --
 * keeping the server's own order inside each half (MH-16). */
export function offFirst(rows) {
  const list = rows || []
  return [...list.filter((row) => row?.leaves?.length), ...list.filter((row) => !row?.leaves?.length)]
}

/** My projects with something of mine in them this month -- hours booked or
 * an open task -- and the rest, each half in the server's order (EP-23). */
export function splitByActivity(projects) {
  const active = []
  const other = []
  for (const project of projects || []) {
    const busy = Number(project?.hours_this_month) > 0 || project?.tasks?.length > 0
    if (busy) active.push(project)
    else other.push(project)
  }
  return { active, other }
}
