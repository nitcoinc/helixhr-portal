import { describe, expect, it } from 'vitest'
import {
  addOrgs,
  addRole,
  applyLeadership,
  diffDraft,
  draftFromPerson,
  draftRows,
  exportState,
  leadershipApplied,
  needsReason,
  reasonValid,
  removeOrg,
  removeRole,
  restoreRole,
  rolesWithoutOrgs,
  setAllOrgs,
  setExport,
} from './accessProfiles'

const LABELS = {
  'HR Manager': 'HR Manager',
  'HR User': 'HR User',
  'HelixHR Delivery Manager': 'Delivery Manager',
  'HelixHR Report Manager': 'Reports',
  'HelixHR Access Admin': 'Manage access',
  'IT Team': 'IT Admin',
}

function row(role, company, extra = {}) {
  return {
    name: `${role}-${company || 'all'}`,
    role,
    label: LABELS[role],
    all_orgs: !company,
    company: company || null,
    allow_export: false,
    editable: true,
    missing_in_frappe: false,
    ...extra,
  }
}

function person(rows, held = []) {
  return { rows, held_roles_without_rows: held.map((role) => ({ role, label: LABELS[role] })) }
}

const PERSON = person([
  row('HR Manager', 'Org A'),
  row('HR Manager', 'Org B'),
  row('HelixHR Delivery Manager', 'Org A'),
  row('HelixHR Report Manager', 'Org A', { allow_export: true }),
])

describe('draftFromPerson and draftRows', () => {
  it('groups rows per role and saves back to the same rows', () => {
    const draft = draftFromPerson(PERSON)
    expect(draft.entries.map((entry) => [entry.role, entry.companies])).toEqual([
      ['HR Manager', ['Org A', 'Org B']],
      ['HelixHR Delivery Manager', ['Org A']],
      ['HelixHR Report Manager', ['Org A']],
    ])
    expect(diffDraft(PERSON, draft, LABELS)).toEqual({ gains: [], losses: [] })
  })

  it('shows a role set outside HelixHR as an entry with no orgs, saving nothing', () => {
    const draft = draftFromPerson(person([], ['HR Manager']))
    expect(draft.entries).toHaveLength(1)
    expect(draft.entries[0]).toMatchObject({ role: 'HR Manager', outside: true, companies: [] })
    expect(draftRows(draft)).toEqual([])
    expect(rolesWithoutOrgs(draft)).toEqual([])
  })

  it('keeps a role saved on All orgs and named orgs at once unchanged', () => {
    const mixed = person([row('HR User', null), row('HR User', 'Org A')])
    expect(diffDraft(mixed, draftFromPerson(mixed), LABELS)).toEqual({ gains: [], losses: [] })
  })
})

describe('applyLeadership', () => {
  it('changes only the HR Manager row', () => {
    const next = applyLeadership(draftFromPerson(PERSON))
    expect(leadershipApplied(next)).toBe(true)
    const diff = diffDraft(PERSON, next, LABELS)
    expect(diff.gains).toEqual([{ text: 'HR Manager · All orgs', highRisk: true }])
    expect(diff.losses.map((item) => item.text)).toEqual(['HR Manager · Org A', 'HR Manager · Org B'])
    const others = (rows) => rows.filter((item) => item.role !== 'HR Manager')
    expect(others(draftRows(next))).toEqual(others(draftRows(draftFromPerson(PERSON))))
  })

  it('adds HR Manager on All orgs when the person has none', () => {
    const plain = person([row('IT Team', 'Org A')])
    const next = applyLeadership(draftFromPerson(plain))
    expect(draftRows(next)).toContainEqual({ role: 'HR Manager', all_orgs: 1, company: '', allow_export: 0 })
    expect(draftRows(next)).toContainEqual({ role: 'IT Team', all_orgs: 0, company: 'Org A', allow_export: 0 })
  })
})

describe('org chips and the All orgs switch', () => {
  it('removing a chip loses only that org', () => {
    const next = removeOrg(draftFromPerson(PERSON), 'HR Manager', 'Org B')
    expect(diffDraft(PERSON, next, LABELS)).toEqual({
      gains: [],
      losses: [{ text: 'HR Manager · Org B', highRisk: false }],
    })
  })

  it('removing the last chip leaves a role with no orgs, which cannot be saved', () => {
    const next = removeOrg(draftFromPerson(PERSON), 'HelixHR Delivery Manager', 'Org A')
    expect(rolesWithoutOrgs(next).map((entry) => entry.role)).toEqual(['HelixHR Delivery Manager'])
    expect(rolesWithoutOrgs(addOrgs(next, 'HelixHR Delivery Manager', ['Org C']))).toEqual([])
  })

  it('switching All orgs off restores the named chips the draft had', () => {
    let draft = removeOrg(draftFromPerson(PERSON), 'HR Manager', 'Org B')
    draft = setAllOrgs(draft, 'HR Manager', true)
    expect(draftRows(draft).filter((item) => item.role === 'HR Manager')).toEqual([
      { role: 'HR Manager', all_orgs: 1, company: '', allow_export: 0 },
    ])
    draft = setAllOrgs(draft, 'HR Manager', false)
    expect(draft.entries[0].companies).toEqual(['Org A'])
    expect(draftRows(draft).filter((item) => item.role === 'HR Manager')).toEqual([
      { role: 'HR Manager', all_orgs: 0, company: 'Org A', allow_export: 0 },
    ])
  })

  it('a locked chip cannot be removed and survives All orgs and Remove role', () => {
    const locked = person([row('HelixHR Delivery Manager', 'Org A'), row('HelixHR Delivery Manager', 'Org B', { editable: false })])
    let draft = removeOrg(draftFromPerson(locked), 'HelixHR Delivery Manager', 'Org B')
    expect(draft.entries[0].companies).toEqual(['Org A', 'Org B'])
    draft = setAllOrgs(draft, 'HelixHR Delivery Manager', true)
    expect(draft.entries[0].allOrgs).toBe(false)
    draft = removeRole(draft, 'HelixHR Delivery Manager')
    expect(draftRows(draft)).toEqual([{ role: 'HelixHR Delivery Manager', all_orgs: 0, company: 'Org B', allow_export: 0 }])
  })

  it('a new role starts on the given org, or on none when there is no own org', () => {
    let draft = addRole(draftFromPerson(person([])), { role: 'IT Team', label: 'IT Admin' }, 'Org A')
    expect(draftRows(draft)).toEqual([{ role: 'IT Team', all_orgs: 0, company: 'Org A', allow_export: 0 }])
    draft = addRole(draft, { role: 'HR User', label: 'HR User' })
    expect(rolesWithoutOrgs(draft).map((entry) => entry.role)).toEqual(['HR User'])
    draft = addRole(draft, { role: 'HelixHR Access Admin', label: 'Manage access' }, 'Org A')
    expect(draftRows(draft)).toContainEqual({ role: 'HelixHR Access Admin', all_orgs: 1, company: '', allow_export: 0 })
  })
})

describe('export', () => {
  it('turns export on for every org of a Reports role, as a high-risk gain', () => {
    const plain = person([row('HelixHR Report Manager', 'Org A'), row('HelixHR Report Manager', 'Org B', { allow_export: true })])
    let draft = draftFromPerson(plain)
    expect(exportState(draft.entries[0])).toBe('some')
    draft = setExport(draft, 'HelixHR Report Manager', true)
    expect(exportState(draft.entries[0])).toBe('on')
    const diff = diffDraft(plain, draft, LABELS)
    expect(diff.gains).toEqual([{ text: 'Export on Reports · Org A', highRisk: true }])
    expect(needsReason(diff)).toBe(true)
  })

  it('is not offered on other roles', () => {
    const draft = setExport(draftFromPerson(PERSON), 'HR Manager', true)
    expect(draftRows(draft).filter((item) => item.allow_export)).toHaveLength(1)
  })
})

describe('warning actions', () => {
  it('Remove role on a role set outside HelixHR removes the Frappe role', () => {
    const drift = person([], ['HR Manager'])
    const next = removeRole(draftFromPerson(drift), 'HR Manager')
    expect(next.sync).toEqual(['HR Manager'])
    expect(draftRows(next)).toEqual([])
    const diff = diffDraft(drift, next, LABELS)
    expect(diff).toEqual({ gains: [], losses: [{ text: 'HR Manager, set outside HelixHR', highRisk: false }] })
    expect(reasonValid(diff, '')).toBe(true)
  })

  it('Give All orgs on Manage access set outside HelixHR saves one All-orgs row', () => {
    const drift = person([], ['HelixHR Access Admin'])
    const next = setAllOrgs(draftFromPerson(drift), 'HelixHR Access Admin', true)
    expect(draftRows(next)).toEqual([{ role: 'HelixHR Access Admin', all_orgs: 1, company: '', allow_export: 0 }])
    const diff = diffDraft(drift, next, LABELS)
    expect(diff.gains).toEqual([{ text: 'Manage access · All orgs', highRisk: true }])
    // Never off, and never on for a saved all-orgs-only role.
    expect(draftRows(setAllOrgs(next, 'HelixHR Access Admin', false))).toEqual(draftRows(next))
    const saved = draftFromPerson(person([row('HelixHR Access Admin', null)]))
    expect(setAllOrgs(saved, 'HelixHR Access Admin', false)).toEqual(saved)
  })

  it('Restore role keeps the rows and gives the role back; an HR role is high-risk', () => {
    const missing = person([row('HR User', 'Org A', { missing_in_frappe: true })])
    const next = restoreRole(draftFromPerson(missing), 'HR User')
    expect(draftRows(next)).toEqual([{ role: 'HR User', all_orgs: 0, company: 'Org A', allow_export: 0 }])
    expect(diffDraft(missing, next, LABELS).gains).toEqual([{ text: 'HR User restored in Frappe', highRisk: true }])
  })
})

describe('the reason', () => {
  it('is required only for a high-risk line, and never over 500 characters', () => {
    const delivery = diffDraft(person([]), addRole(draftFromPerson(person([])), { role: 'HelixHR Delivery Manager' }, 'Org A'), LABELS)
    expect(needsReason(delivery)).toBe(false)
    expect(reasonValid(delivery, '')).toBe(true)
    expect(reasonValid(delivery, 'x'.repeat(501))).toBe(false)

    const hr = diffDraft(person([]), addRole(draftFromPerson(person([])), { role: 'HR User' }, 'Org A'), LABELS)
    expect(needsReason(hr)).toBe(true)
    expect(reasonValid(hr, '   ')).toBe(false)
    expect(reasonValid(hr, 'Covers payroll in Org A')).toBe(true)
  })
})
