// Plan 2026-10-15-002 KTD9: the Approvals page's grouping as pure helpers, so
// the lane order and the empty-lane rules are unit-tested rather than living
// inline in `Approvals.vue`. The server tags each queue row with `lane`
// ("team", "hr" or "it", KTD1); these functions only group.

export const LANE_TITLES = {
  team: 'My team',
  decided: 'Decided by me',
  hr: 'HR',
}

/** R8/R11: why an approver group cannot be reminded, in words. Keys are the
 * server resolver's reasons (`helixhr.reminders.resolve_approvers`). */
export const APPROVER_REASON_TEXT = {
  inactive: 'Approver has left; set a new Reports to',
  no_login: 'Approver has no portal login',
  unmailable: 'Approver has no email address',
  no_approver: 'No approver set; set Reports to',
}

const PENDING_LANES = ['team', 'hr']

/** Plan 2026-10-04-003 U7 (R13, R19): inside a lane, change requests (never
 * batchable) first, then the rows the server flagged, then everything else.
 * Empty sub-sections are dropped. */
export function laneSubsections(rows) {
  return [
    {
      key: 'change',
      title: 'Change requests',
      note: 'One decision at a time, never in bulk.',
      rows: rows.filter((row) => row.kind === 'change'),
    },
    {
      key: 'flagged',
      title: 'Needs a look',
      note: 'Something on these rows is unusual. Decide each one on its own evidence.',
      rows: rows.filter((row) => row.kind !== 'change' && row.needs_look),
    },
    {
      key: 'normal',
      title: 'Looks normal',
      note: '',
      rows: rows.filter((row) => row.kind !== 'change' && !row.needs_look),
    },
  ].filter((section) => section.rows.length)
}

/**
 * R1/R2: the Approvals tab's lanes, in order My team, Decided by me, HR, IT.
 * A pending lane with no rows is dropped; Decided appears whenever
 * `decidedCount` is non-zero, so with My team empty it comes first. A row
 * with no lane tag counts as the caller's own team.
 */
export function laneSections(rows, decidedCount = 0) {
  const byLane = { team: [], hr: [] }
  for (const row of rows || []) (byLane[row.lane] || byLane.team).push(row)
  const lanes = []
  for (const key of PENDING_LANES) {
    if (byLane[key].length) {
      lanes.push({ key, title: LANE_TITLES[key], rows: byLane[key], sections: laneSubsections(byLane[key]) })
    }
    if (key === 'team' && decidedCount) lanes.push({ key: 'decided', title: LANE_TITLES.decided, rows: [], sections: [] })
  }
  return lanes
}

/** R14 (plan 2026-10-04-003): one group per person, in first-seen order --
 * the rows arrive oldest first, so each group leads with its oldest item. */
export function groupByPerson(rows) {
  const groups = []
  const index = {}
  for (const row of rows || []) {
    let group = index[row.employee]
    if (!group) {
      group = {
        employee: row.employee,
        employee_name: row.employee_name,
        initials: row.initials,
        photo_url: row.photo_url,
        oldest: row,
        rows: [],
      }
      index[row.employee] = group
      groups.push(group)
    }
    group.rows.push(row)
  }
  return groups
}
