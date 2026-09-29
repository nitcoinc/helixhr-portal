// Plan 2026-09-29-001 U4. What "Request a correction" pre-fills, as pure
// functions so the wording is held by a test rather than by a template.
//
// The draft carries only what the page already shows. For a masked
// identifier that is the last four characters: the server never sent more,
// and the draft asks what is wrong rather than for the full number, which
// would otherwise sit in plain text on a request the whole HR queue reads.

export const NOT_RECORDED = 'Not recorded'
const MASK = '••••'
// HR Request's subject field (helixhr/doctype/hr_request).
export const SUBJECT_MAX = 140

/** "Date of birth" -> "date of birth", but "PAN" stays "PAN". */
function inSentence(label) {
  const text = String(label || '').trim()
  if (text.length > 1 && text[1] === text[1].toLowerCase()) {
    return text[0].toLowerCase() + text.slice(1)
  }
  return text
}

/** A value as the page prints it: "Not recorded" for nothing at all. */
export function displayValue(value, format = (v) => String(v)) {
  if (value === null || value === undefined || value === '') return NOT_RECORDED
  return format(value)
}

/** Screen readers read "••••1234" as a string of bullets. */
export function spokenValue(display) {
  if (typeof display === 'string' && display.startsWith(MASK)) {
    const tail = display.slice(MASK.length)
    return tail ? `ending in ${tail}` : 'hidden'
  }
  return display
}

/**
 * The subject and details a correction request starts with.
 *
 * @param {{label: string, display?: string, masked?: boolean, table?: boolean}} field
 *   `display` is the value exactly as the page shows it; `table` marks a
 *   whole section (education, work history) rather than one field.
 */
export function correctionDraft({ label, display, masked = false, table = false }) {
  const subject = `Correct my ${inSentence(label)}`.slice(0, SUBJECT_MAX)

  if (table) {
    return {
      subject,
      details: `My ${inSentence(label)} is missing something or has a mistake:\n`,
    }
  }
  if (!display || display === NOT_RECORDED) {
    return {
      subject,
      details: `My ${inSentence(label)} isn’t recorded on my profile. It should be:\n`,
    }
  }
  if (masked) {
    return {
      subject,
      details:
        `My ${inSentence(label)} is shown as ${spokenValue(display)}. What’s wrong:\n\n` +
        'HR will check this against your documents — please don’t type the full number here.',
    }
  }
  return {
    subject,
    details: `My ${inSentence(label)} is shown as “${display}”. It should be:\n`,
  }
}
