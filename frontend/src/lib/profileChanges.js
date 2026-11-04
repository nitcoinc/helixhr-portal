// Plan 2026-11-03-001 U9. Bank & IDs edit mode as pure functions: what is
// staged, where each group goes, and where a server refusal is shown.
//
// The server (`helixhr.api.submit_my_profile_changes`) is the rule for all of
// it; this only previews it so the employee knows before they press Send.
// Each field the page can edit carries `change: {group, type, options}` from
// `get_my_profile`, so the groups and inputs follow what the site has. The
// group facts -- `groups: {group: {state, core, identity}}` -- come from the
// server too (fix plan 2026-11-04-001 KTD3): state and core fields are never
// worked out here.

/** `{group: [field, ...]}` for the editable fields this site has. */
export function groupFields(fields) {
  const groups = {}
  for (const field of fields || []) {
    if (!field.change) continue
    ;(groups[field.change.group] ||= []).push(field.fieldname)
  }
  return groups
}

/** The editable form's starting values. A masked field starts empty: the
 * page holds only its last four, which is a hint, never a value. */
export function initialForm(fields) {
  const form = {}
  for (const field of fields || []) {
    if (!field.change) continue
    form[field.fieldname] = { value: field.masked ? '' : String(field.value ?? ''), confirm: '' }
  }
  return form
}

/** `[{field, value, confirm}]` for what actually changed. Blank never
 * stages: clearing a detail is HR's job. */
export function stagedChanges(fields, form) {
  const out = []
  for (const field of fields || []) {
    const entry = field.change && form[field.fieldname]
    if (!entry) continue
    const value = String(entry.value ?? '').trim()
    if (!value) continue
    if (field.masked) {
      out.push({ field: field.fieldname, value, confirm: String(entry.confirm ?? '').trim() })
    } else if (value !== String(field.value ?? '').trim()) {
      out.push({ field: field.fieldname, value })
    }
  }
  return out
}

/** `{group: "fill" | "hr" | "both"}` for every group with a staged change.
 * A blank group fills now, except a non-identity field (salary mode) already
 * on file, which goes to HR on its own (review C2). */
export function routes(staged, fields, groups) {
  const byName = Object.fromEntries((fields || []).filter((f) => f.change).map((f) => [f.fieldname, f]))
  const out = {}
  for (const { field } of staged) {
    const meta = byName[field]
    const facts = meta && groups?.[meta.change.group]
    if (!facts) continue
    const onFile = meta.value !== null && meta.value !== undefined && meta.value !== ''
    const to = facts.state === 'blank' && (facts.identity.includes(field) || !onFile) ? 'fill' : 'hr'
    const group = meta.change.group
    out[group] = !out[group] || out[group] === to ? to : 'both'
  }
  return out
}

/** `{group: state}` for the touched groups, as the server's `seen`. */
export function seenStates(staged, fields, groups) {
  return Object.fromEntries(Object.keys(routes(staged, fields, groups)).map((group) => [group, groups[group].state]))
}

/** `{field: message}` for double entries that don't match yet. Nothing is
 * said before the second box has anything in it. */
export function entryErrors(staged) {
  const out = {}
  for (const change of staged) {
    if (!('confirm' in change)) continue
    if (!change.confirm) out[change.field] = 'Type the new value again to confirm it.'
    else if (change.confirm !== change.value) {
      out[change.field] = 'The two entries don’t match. Type the new value again in both boxes.'
    }
  }
  return out
}

/** `{group: [fieldname, ...]}` core fields a first fill still lacks. Asked
 * only once an identity field is staged (KTD2): salary mode alone needs none. */
export function missingCore(staged, fields, groups) {
  const stagedNames = new Set(staged.map((change) => change.field))
  const out = {}
  for (const group of Object.keys(routes(staged, fields, groups))) {
    const { state, core, identity } = groups[group]
    if (state !== 'blank' || !identity.some((name) => stagedNames.has(name))) continue
    const missing = core.filter((name) => !stagedNames.has(name))
    if (missing.length) out[group] = missing
  }
  return out
}

/** A refusal's errors, split to where each is shown: a field, a group, or
 * the top of the form. A field the page doesn't render goes to the top. */
export function placeErrors(errors, known = []) {
  const placed = { fields: {}, groups: {}, form: [] }
  const shown = new Set(known)
  for (const error of errors || []) {
    if (error.field && shown.has(error.field)) {
      placed.fields[error.field] = [...(placed.fields[error.field] || []), error.message]
    } else if (!error.field && error.group) {
      placed.groups[error.group] = [...(placed.groups[error.group] || []), error.message]
    } else {
      placed.form.push(error.message)
    }
  }
  return placed
}

/** A v4 UUID, one per send attempt (`create_my_request`'s pattern). */
export function newOperationKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
