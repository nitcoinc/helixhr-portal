// Plan 2026-11-03-001 U9. Bank & IDs edit mode as pure functions: what is
// staged, where each group goes, and where a server refusal is shown.
//
// The server (`helixhr.api.submit_my_profile_changes`) is the rule for all of
// it; this only previews it so the employee knows before they press Send.
// Each field the page can edit carries `change: {group, type, options}` from
// `get_my_profile`, so the groups and inputs follow what the site has.

/** Review B6: a first fill of these groups must include these fields. */
export const FILL_CORE_FIELDS = {
  bank: ['bank_name', 'bank_ac_no', 'ifsc_code'],
  passport: ['passport_number', 'date_of_issue', 'valid_upto', 'place_of_issue'],
}

/** `{group: [field, ...]}` for the editable fields this site has. */
export function groupFields(fields) {
  const groups = {}
  for (const field of fields || []) {
    if (!field.change) continue
    ;(groups[field.change.group] ||= []).push(field.fieldname)
  }
  return groups
}

/** `{group: "blank" | "filled"}`: blank only when nothing in it is on file.
 * A masked value arrives as its last four, which still counts as on file. */
export function groupStates(fields) {
  const states = {}
  for (const field of fields || []) {
    if (!field.change) continue
    const { group } = field.change
    const filled = field.value !== null && field.value !== undefined && field.value !== ''
    states[group] = states[group] === 'filled' || filled ? 'filled' : 'blank'
  }
  return states
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

/** `{group: "fill" | "hr"}` for every group with a staged change. */
export function routes(staged, fields) {
  const byField = Object.fromEntries((fields || []).filter((f) => f.change).map((f) => [f.fieldname, f.change.group]))
  const states = groupStates(fields)
  const out = {}
  for (const { field } of staged) {
    const group = byField[field]
    if (group) out[group] = states[group] === 'blank' ? 'fill' : 'hr'
  }
  return out
}

/** `{group: state}` for the touched groups, as the server's `seen`. */
export function seenStates(staged, fields) {
  const states = groupStates(fields)
  return Object.fromEntries(Object.keys(routes(staged, fields)).map((group) => [group, states[group]]))
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

/** `{group: [fieldname, ...]}` core fields a first fill still lacks. */
export function missingCore(staged, fields) {
  const present = groupFields(fields)
  const stagedNames = new Set(staged.map((change) => change.field))
  const out = {}
  for (const [group, route] of Object.entries(routes(staged, fields))) {
    if (route !== 'fill') continue
    const missing = (FILL_CORE_FIELDS[group] || []).filter(
      (name) => present[group]?.includes(name) && !stagedNames.has(name),
    )
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
