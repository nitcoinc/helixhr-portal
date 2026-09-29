import { formatDate, isCalendarDate } from './dates'

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

/** "Date of Birth" -> "date of birth", but "PAN Number" -> "PAN number":
 * mid-sentence case, word by word, leaving acronyms alone. */
function inSentence(label) {
  return String(label || '')
    .trim()
    .split(/\s+/)
    .map((word) => (word.length > 1 && word === word.toUpperCase() ? word : word.toLowerCase()))
    .join(' ')
}

/** A value as the page prints it: "Not recorded" for nothing at all. */
export function displayValue(value, format = (v) => String(v)) {
  if (value === null || value === undefined || value === '') return NOT_RECORDED
  return format(value)
}

/** A profile value (a field or a table cell) as the page prints it: dates
 * in the user's own format, "Not recorded" for nothing. */
export function formatProfileValue(value) {
  return displayValue(value, (v) => (isCalendarDate(v) ? formatDate(v) : String(v)))
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
  if ((!display || display === NOT_RECORDED) && masked) {
    // Nothing on record to compare with -- and still never the number itself.
    return {
      subject,
      details:
        `My ${inSentence(label)} isn’t recorded on my profile.\n\n` +
        'HR will ask for your documents — please don’t type the full number here.',
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
