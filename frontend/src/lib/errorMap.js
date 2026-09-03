// Plain-English translations for the ten most common Frappe/HRMS leave
// validation messages (R15, docs/design-system.md). Anything that doesn't
// match falls back to Frappe's own message with HTML tags stripped, so an
// unmapped error is still readable rather than a raw markup blob.

const PATTERNS = [
  {
    test: /insufficient leave balance for leave type\s*:?\s*"?([^"<]+)"?/i,
    message: (m) => `You do not have enough ${m[1].trim()} for these dates.`,
  },
  {
    test: /leave application .*already exists|overlaps? with/i,
    message: () => 'You already have a leave request that overlaps these dates.',
  },
  {
    test: /application period cannot be across two allocation records/i,
    message: () => 'These dates cross two leave years. Split the request into two.',
  },
  {
    test: /application period cannot be outside leave allocation period/i,
    message: () => 'These dates are outside your current leave year.',
  },
  {
    test: /there is no leave period/i,
    message: () => 'There is no leave calendar set up for these dates. Ask HR.',
  },
  {
    test: /total leave days is 0/i,
    message: () => 'This request has zero days. Check your start and end dates.',
  },
  {
    test: /half day date should be/i,
    message: () => 'The half-day date must fall within your leave dates.',
  },
  {
    test: /to date cannot be less than from date|to date .*before .*from date/i,
    message: () => 'The end date must be on or after the start date.',
  },
  {
    test: /cannot be before .*joining date/i,
    message: () => "You can't request leave from before your joining date.",
  },
  {
    test: /employee .*not active|not associated with any leave approver/i,
    message: () => "There's a setup issue with your leave approver. Ask HR.",
  },
]

function stripHtml(text) {
  return String(text || '')
    .replace(/<[^>]*>/g, '')
    .trim()
}

/** Map a raw Frappe error (an Error thrown by apiRequest, with `.messages`
 * and/or `.message`) to one plain sentence. */
export function toPlainLeaveError(error) {
  const raw = error?.messages?.[0] || error?.message || String(error || '')
  for (const { test, message } of PATTERNS) {
    const match = raw.match(test)
    if (match) return message(match)
  }
  return stripHtml(raw) || 'Something went wrong. Please try again.'
}
