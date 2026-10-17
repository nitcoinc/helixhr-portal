// Plan 2026-10-15-002 KTD9: the Approvals page's grouping as pure helpers, so
// the lane order and the empty-lane rules are unit-tested rather than living
// inline in `Approvals.vue`. The server tags each queue row with `lane`
// ("team", "hr" or "it", KTD1); these functions only group.

import { trimNumber } from './hours'
import { APPROVALS_SHORTCUTS_PREFIX, resolveStorage, viewerKey } from './viewerStorage'

const LANE_TITLES = {
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

/** Approvals search: the Decided rows one tab shows. Unsearched, the 14-day
 * receipt belongs to Leave & attendance (`queue`) only, as it always has;
 * searched, the all-time matches split by tab -- timesheet decisions on
 * Timesheets, every other kind on Leave & attendance. */
export function decidedForTab(rows, view, searched) {
  if (!searched) return view === 'queue' ? rows || [] : []
  return (rows || []).filter((row) => (row.kind === 'timesheet') === (view === 'timesheets'))
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

// --- batch approval (plan 2026-10-17-001 U7, MH-04 / MH-06 / HR-12) -------

/** The server's own cap on one `approve_clean_items` call (`_BULK_MAX_ITEMS`);
 * the call is also rate-limited to 10 per 60s. */
export const BULK_MAX_ITEMS = 60

/** The rows `approve_clean_items` takes: timesheets and leave the server
 * still calls clean (R17). Attendance requests and change requests are
 * decided one at a time, and the server refuses them in a batch anyway.
 * Clean means the server said so (`needs_look: false`): an HR-lane timesheet
 * or stalled leave carries no flags (`null`) and no stale-write token, and
 * the batch endpoint would re-flag it and refuse. */
export function isBatchable(row) {
  return !!row && (row.kind === 'timesheet' || row.kind === 'leave') && row.needs_look === false
}

/** MH-04: the "Approve all" batch for one lane's rows. Built from the rows
 * themselves, never from checkbox selection (there is none on a phone). The
 * rows arrive oldest first, so a cut keeps the oldest; `eligible` is the
 * uncapped count. */
export function approveAllBatch(rows, max = BULK_MAX_ITEMS) {
  const eligible = (rows || []).filter(isBatchable)
  return { rows: eligible.slice(0, max), eligible: eligible.length }
}

/** The button's words: "Approve all 2 that look normal", or "Approve the
 * oldest 60 of 75" past the cap; '' when nothing is eligible. */
export function approveAllLabel(batch) {
  if (!batch?.rows?.length) return ''
  return batch.eligible > batch.rows.length
    ? `Approve the oldest ${batch.rows.length} of ${batch.eligible}`
    : `Approve all ${batch.eligible} that look normal`
}

/** One row as `approve_clean_items` takes it: the concurrency token the row
 * was drawn from travels with it, so a row that moved is refused. */
export function batchItem(row) {
  return {
    doctype: row.doctype,
    name: row.name,
    expected_modified: row.token_modified,
    expected_state: row.status,
  }
}


/** "Approve 8 items · 312 h · 3 days leave" -- the one sentence the bar and
 * the confirm share, so they can never disagree (R17). Hours are the
 * timesheets'; days are the leave applications' own day counts. */
export function batchLabel(rows) {
  const list = rows || []
  let hours = 0
  let days = 0
  for (const row of list) {
    if (row.kind === 'timesheet') hours += Number(row.total_hours || 0)
    if (row.kind === 'leave') days += Number(row.total_days || 0)
  }
  const parts = [`${list.length} ${list.length === 1 ? 'item' : 'items'}`]
  if (hours) parts.push(`${trimNumber(hours)} h`)
  if (days) parts.push(`${trimNumber(days)} ${days === 1 ? 'day' : 'days'} leave`)
  return `Approve ${parts.join(' · ')}`
}

/** MH-06 / HR-12: a tri-state select-all over `rows`' batchable members --
 * 'all', 'some' or 'none' of them are in `selectedNames`. */
export function selectionState(rows, selectedNames) {
  const names = (rows || []).filter(isBatchable).map((row) => row.name)
  const chosen = names.filter((name) => selectedNames.includes(name)).length
  if (!names.length || !chosen) return 'none'
  return chosen === names.length ? 'all' : 'some'
}

// --- keyboard triage opt-in (KTD13) ---------------------------------------

// Per-viewer: the key ends in `session.user`. `viewerStorage.js` owns the
// prefix, clears it on sign-out and drops another account's on bootstrap.

/** Whether this user turned keyboard triage on, on this device. Off when
 * there is no user or the storage cannot be read. */
export function readShortcuts(user, storage) {
  const key = viewerKey(APPROVALS_SHORTCUTS_PREFIX, user)
  if (!key) return false
  try {
    return resolveStorage(storage)?.getItem(key) === '1'
  } catch {
    return false
  }
}

/** Store the flag; `false` when it could not be stored (the toggle then
 * still works for this visit). */
export function writeShortcuts(user, on, storage) {
  const key = viewerKey(APPROVALS_SHORTCUTS_PREFIX, user)
  if (!key) return false
  try {
    const target = resolveStorage(storage)
    if (on) target.setItem(key, '1')
    else target.removeItem(key)
    return true
  } catch {
    return false
  }
}
