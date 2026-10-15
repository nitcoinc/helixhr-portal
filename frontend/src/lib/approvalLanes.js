// Plan 2026-10-15-002 U5: lane helpers for the Approvals page.
// Each row from `get_my_approvals` carries a `lane` key: "team", "hr", or "it".
// `laneSections(rows)` builds the ordered lane structure.

const LANE_ORDER = ['team', 'decided', 'hr', 'it']

const LANE_LABELS = {
  team: 'My team',
  decided: 'Decided by me',
  hr: 'HR',
  it: 'IT',
}

/** Group rows by lane. Returns ordered sections; empty pending lanes are
 * omitted. Decided is included whenever it has items. */
export function laneSections(rows, decided) {
  const lanes = { team: [], hr: [], it: [] }
  for (const row of rows || []) {
    const lane = row.lane || 'team'
    if (lanes[lane] !== undefined) lanes[lane].push(row)
  }

  const sections = []
  for (const key of LANE_ORDER) {
    if (key === 'decided') {
      if (decided?.length) {
        sections.push({ key, label: LANE_LABELS[key], rows: decided, collapsed: true })
      }
      continue
    }
    if (lanes[key].length) {
      sections.push({ key, label: LANE_LABELS[key], rows: lanes[key], collapsed: false })
    }
  }

  return sections
}

/** Group rows by employee (person). Returns array of {employee, employee_name,
 * initials, photo_url, count, rows} sorted by oldest wait first. */
export function groupByPerson(pending) {
  const people = []
  const byEmployee = {}
  for (const row of pending) {
    const emp = row.employee || row.employee_name
    let entry = byEmployee[emp]
    if (!entry) {
      entry = {
        employee: emp,
        employee_name: row.employee_name,
        initials: row.initials || (row.employee_name || '?').split(' ').map((s) => s[0]).join('').toUpperCase().slice(0, 2),
        photo_url: row.photo_url,
        count: 0,
        rows: [],
      }
      byEmployee[emp] = entry
      people.push(entry)
    }
    entry.count++
    entry.rows.push(row)
  }
  return people
}

/** Group pending items by approver_user. Returns array of {approver_user,
 * approver_name, count, oldest_days, rows} for oversight views. */
export function groupByApprover(rows) {
  const groups = []
  const byUser = {}
  for (const row of rows || []) {
    const key = row.approver_user || '__none__'
    let group = byUser[key]
    if (!group) {
      group = {
        approver_user: row.approver_user,
        approver_name: row.approver_name || 'Unassigned',
        count: 0,
        oldest_days: 0,
        rows: [],
      }
      byUser[key] = group
      groups.push(group)
    }
    group.count++
    group.rows.push(row)
    if (row.age_days && row.age_days > group.oldest_days) group.oldest_days = row.age_days
  }
  return groups.sort((a, b) => b.oldest_days - a.oldest_days)
}