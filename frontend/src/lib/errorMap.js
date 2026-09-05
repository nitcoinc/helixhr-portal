// Plain-English translations for the most common Frappe/HRMS leave
// validation messages (R15, docs/design-system.md). Anything that doesn't
// match falls back to Frappe's own message with HTML tags stripped, so an
// unmapped error is still readable rather than a raw markup blob.
//
// P2-U5 widened where this is used: it now also translates the refusals from
// `helixhr.api.get_leave_day_count` and `helixhr.api.withdraw_my_leave`.
// Those are written as plain sentences already, so they fall through the
// fallback unchanged and deliberately have no pattern of their own -- a
// mapping table that restates its own strings is a second place for them to
// drift.

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
  // HR Settings' leave_approver_mandatory_in_leave_application (P2-U1 step
  // 3). apply_for_leave refuses before the document exists, so this is the
  // second line of defence -- a direct caller, or an approver unset between
  // the form loading and Send.
  {
    test: /leave approver is mandatory|leave approver.*mandatory/i,
    message: () =>
      "You don't have a leave approver yet, so this can't be sent. Ask HR to set one.",
  },
  {
    test: /leave type is mandatory|please select a leave type/i,
    message: () => 'Pick a leave type first.',
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
