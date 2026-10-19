// Plan 2026-10-19-001 U7: the People & access editor's draft, as pure
// functions so the rules a person can get wrong are unit tested.
//
// The server keeps one row per (person, role, org-or-All). The editor groups
// them per role -- one entry with org chips, or an All orgs badge -- and
// turns the entries back into the whole row set `save_person_access` takes.
// Every rule is the server's again on save (R16); these only keep the draft
// honest and say, in words, what a save gives and takes.

const ALL = '__all'
export const LEADERSHIP_ROLE = 'HR Manager'
export const HR_ROLES = ['HR Manager', 'HR User']
export const ACCESS_ADMIN_ROLE = 'HelixHR Access Admin'
// Mirrors `ALL_ORGS_ONLY_ROLES` and `EXPORT_ROLES` in helixhr/utils.py.
export const ALL_ORGS_ONLY_ROLES = ['HelixHR Notification Manager', ACCESS_ADMIN_ROLE]
export const EXPORT_ROLES = ['HelixHR Report Manager', 'HelixHR Org Viewer']

/** R19's words, keyed as `get_elevated_access` and `get_person_access` send
 * them. The kind (problem or info) comes from the server. */
export const WARNING_LABELS = {
  employee_left: 'Employee record Left or Inactive',
  user_disabled: 'User disabled',
  no_login: 'No login',
  role_outside_helixhr: 'Role set outside HelixHR',
  role_missing_in_frappe: 'Role missing in Frappe',
  own_org_not_covered: 'Own org not covered by their roles',
  desk_hr_reach_all_orgs: 'Desk HR reach: all orgs',
  all_orgs: 'All orgs',
  can_manage_access: 'Can manage access',
  export_on: 'Export on',
}

export function warningLabel(key) {
  return WARNING_LABELS[key] || key
}

function scopeKey(row) {
  return row.all_orgs ? ALL : row.company
}

function rowKey(row) {
  return `${row.role}|${scopeKey(row)}`
}

function entryKeys(entry) {
  return entry.allOrgs ? [ALL] : entry.companies
}

/** The editor's draft from `get_person_access`: one entry per role, in the
 * server's row order, then each role held in Frappe with no row (R9 "Role
 * set outside HelixHR", an entry with no orgs). `sync` lists roles whose
 * Frappe role the save should bring in step without a row change. */
export function draftFromPerson(person) {
  const entries = []
  const byRole = {}
  for (const row of person?.rows || []) {
    let entry = byRole[row.role]
    if (!entry) {
      entry = {
        role: row.role,
        label: row.label,
        allOrgs: false,
        companies: [],
        export: {},
        locked: [],
        missing: false,
        outside: false,
        // A role saved on All orgs and on named orgs at once keeps both
        // until somebody touches its All orgs switch.
        keepNamed: false,
      }
      byRole[row.role] = entry
      entries.push(entry)
    }
    if (row.all_orgs) entry.allOrgs = true
    else entry.companies.push(row.company)
    entry.export[scopeKey(row)] = !!row.allow_export
    if (!row.editable) entry.locked.push(scopeKey(row))
    if (row.missing_in_frappe) entry.missing = true
  }
  for (const entry of entries) entry.keepNamed = entry.allOrgs && entry.companies.length > 0
  for (const held of person?.held_roles_without_rows || []) {
    entries.push({
      role: held.role,
      label: held.label,
      allOrgs: false,
      companies: [],
      export: {},
      locked: [],
      missing: false,
      outside: true,
      keepNamed: false,
    })
  }
  return { entries, sync: [] }
}

function clone(draft) {
  return {
    entries: draft.entries.map((entry) => ({
      ...entry,
      companies: [...entry.companies],
      export: { ...entry.export },
      locked: [...entry.locked],
    })),
    sync: [...draft.sync],
  }
}

function update(draft, role, change) {
  const next = clone(draft)
  const entry = next.entries.find((item) => item.role === role)
  if (entry) change(entry, next)
  return next
}

/** The whole row set to save, in `save_person_access`'s shape. Named rows
 * under an All orgs switch are dropped, except the ones this caller may not
 * change (the server keeps those whatever the payload says). */
export function draftRows(draft) {
  const rows = []
  for (const entry of draft.entries) {
    if (entry.allOrgs) {
      rows.push({ role: entry.role, all_orgs: 1, company: '', allow_export: entry.export[ALL] ? 1 : 0 })
    }
    for (const company of entry.companies) {
      if (entry.allOrgs && !entry.keepNamed && !entry.locked.includes(company)) continue
      rows.push({ role: entry.role, all_orgs: 0, company, allow_export: entry.export[company] ? 1 : 0 })
    }
  }
  return rows
}

/** Adds ``role`` with its first org: ``company``, or none yet (the person has
 * no Employee, so the editor asks). All-orgs-only roles start on All orgs. */
export function addRole(draft, { role, label }, company = null) {
  const next = clone(draft)
  if (next.entries.some((entry) => entry.role === role)) return next
  const allOrgs = ALL_ORGS_ONLY_ROLES.includes(role)
  next.entries.push({
    role,
    label,
    allOrgs,
    companies: !allOrgs && company ? [company] : [],
    export: {},
    locked: [],
    missing: false,
    outside: false,
    keepNamed: false,
  })
  next.sync = next.sync.filter((name) => name !== role)
  return next
}

/** "Remove role": every row of it this caller may change goes. A role set
 * outside HelixHR has no rows, so its Frappe role is what goes (`sync`). */
export function removeRole(draft, role) {
  const next = clone(draft)
  const entry = next.entries.find((item) => item.role === role)
  if (!entry) return next
  next.sync = next.sync.filter((name) => name !== role)
  if (entry.outside) {
    next.entries = next.entries.filter((item) => item !== entry)
    next.sync.push(role)
    return next
  }
  if (!entry.locked.length) {
    next.entries = next.entries.filter((item) => item !== entry)
    return next
  }
  entry.allOrgs = entry.locked.includes(ALL)
  entry.companies = entry.companies.filter((company) => entry.locked.includes(company))
  return next
}

/** "Restore role" on a role missing in Frappe: the rows stay, and the save
 * gives the Frappe role back. */
export function restoreRole(draft, role) {
  const next = clone(draft)
  if (!next.sync.includes(role)) next.sync.push(role)
  return next
}

export function removeOrg(draft, role, company) {
  return update(draft, role, (entry) => {
    if (entry.locked.includes(company)) return
    entry.companies = entry.companies.filter((name) => name !== company)
  })
}

export function addOrgs(draft, role, companies) {
  return update(draft, role, (entry) => {
    for (const company of companies) if (!entry.companies.includes(company)) entry.companies.push(company)
  })
}

/** The All orgs switch. On, one badge stands for every org; the named chips
 * are kept underneath, so switching back restores them as they were. An
 * all-orgs-only role is always All orgs: the switch only turns one set
 * outside HelixHR on ("Give All orgs"), never off. */
export function setAllOrgs(draft, role, on) {
  return update(draft, role, (entry) => {
    if (entry.allOrgs === on || entry.locked.length) return
    if (ALL_ORGS_ONLY_ROLES.includes(role) && !(on && entry.outside)) return
    entry.keepNamed = false
    if (on && entry.export[ALL] === undefined) {
      entry.export[ALL] = entry.companies.length > 0 && entry.companies.every((company) => entry.export[company])
    }
    entry.allOrgs = on
  })
}

/** Export on or off for every org of a Reports or Org viewer role. */
export function setExport(draft, role, on) {
  return update(draft, role, (entry) => {
    if (!EXPORT_ROLES.includes(role)) return
    for (const key of entryKeys(entry)) if (!entry.locked.includes(key)) entry.export[key] = on
  })
}

/** "On", "off" or "some" (saved per org, not all alike). */
export function exportState(entry) {
  const values = entryKeys(entry).map((key) => !!entry.export[key])
  if (!values.length || values.every((value) => !value)) return 'off'
  return values.every(Boolean) ? 'on' : 'some'
}

/** R6: the Leadership preset is HR Manager on All orgs. It replaces only
 * the HR Manager row; every other role is left as it is. */
export function applyLeadership(draft, label = 'HR Manager') {
  const next = clone(draft)
  const entry = next.entries.find((item) => item.role === LEADERSHIP_ROLE)
  if (entry) {
    entry.allOrgs = true
    entry.keepNamed = false
  } else {
    next.entries.push({
      role: LEADERSHIP_ROLE,
      label,
      allOrgs: true,
      companies: [],
      export: {},
      locked: [],
      missing: false,
      outside: false,
      keepNamed: false,
    })
  }
  next.sync = next.sync.filter((name) => name !== LEADERSHIP_ROLE)
  return next
}

export function leadershipApplied(draft) {
  return draft.entries.some((entry) => entry.role === LEADERSHIP_ROLE && entry.allOrgs)
}

/** Roles in the draft with no org at all, which cannot be saved -- except a
 * role set outside HelixHR left as it was found. */
export function rolesWithoutOrgs(draft) {
  return draft.entries.filter((entry) => !entry.outside && !entry.allOrgs && !entry.companies.length)
}

function line(row, label) {
  const where = row.all_orgs ? 'All orgs' : row.company
  return `${label} · ${where}${row.allow_export ? ', with export' : ''}`
}

/** R15: what makes a gained row high-risk. */
function risky(row) {
  return !!row.all_orgs || !!row.allow_export || row.role === ACCESS_ADMIN_ROLE || HR_ROLES.includes(row.role)
}

/** What saving ``draft`` over ``person``'s saved rows gives and takes, in
 * words (R22): ``{gains, losses}``, each ``{text, highRisk}``. ``labels``
 * maps a role to its R2 label, as the server sent it. */
export function diffDraft(person, draft, labels = {}) {
  const label = (role) => labels[role] || role
  const saved = new Map(
    (person?.rows || []).map((row) => [
      rowKey(row),
      { role: row.role, all_orgs: row.all_orgs ? 1 : 0, company: row.all_orgs ? '' : row.company, allow_export: row.allow_export ? 1 : 0 },
    ]),
  )
  const wanted = new Map(draftRows(draft).map((row) => [rowKey(row), row]))
  const gains = []
  const losses = []
  for (const [key, row] of wanted) {
    const before = saved.get(key)
    if (!before) gains.push({ text: line(row, label(row.role)), highRisk: risky(row) })
    else if (before.allow_export !== row.allow_export) {
      const text = `Export on ${line({ ...row, allow_export: 0 }, label(row.role))}`
      if (row.allow_export) gains.push({ text, highRisk: true })
      else losses.push({ text, highRisk: false })
    }
  }
  for (const [key, row] of saved) {
    if (!wanted.has(key)) losses.push({ text: line(row, label(row.role)), highRisk: false })
  }
  for (const role of draft.sync) {
    if (draft.entries.some((entry) => entry.role === role)) {
      gains.push({ text: `${label(role)} restored in Frappe`, highRisk: HR_ROLES.includes(role) })
    } else {
      losses.push({ text: `${label(role)}, set outside HelixHR`, highRisk: false })
    }
  }
  const byText = (a, b) => a.text.localeCompare(b.text)
  return { gains: gains.sort(byText), losses: losses.sort(byText) }
}

/** Whether any line of a `diffDraft` answer is high-risk (R15): the save
 * then needs a reason. */
export function needsReason(diff) {
  return [...diff.gains, ...diff.losses].some((item) => item.highRisk)
}

export const REASON_MAX = 500

/** The review's Confirm: a high-risk save needs a reason of at most 500
 * characters; any other save may carry one within the same limit. */
export function reasonValid(diff, reason) {
  const text = (reason || '').trim()
  if (text.length > REASON_MAX) return false
  return !needsReason(diff) || text.length > 0
}
