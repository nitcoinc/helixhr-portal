import { describe, it, expect } from 'vitest'
import { toPlainLeaveError, toPlainMessage } from './errorMap'

function err(message) {
  return { messages: [message] }
}

describe('toPlainLeaveError', () => {
  it('maps insufficient balance', () => {
    expect(toPlainLeaveError(err('Insufficient leave balance for Leave Type "Casual Leave"'))).toBe(
      'You do not have enough Casual Leave for these dates.',
    )
  })

  it('maps overlapping leave', () => {
    expect(toPlainLeaveError(err('Leave application HR-LAP-2026-00001 already exists'))).toBe(
      'You already have a leave request that overlaps these dates.',
    )
  })

  it('maps across-allocation-records', () => {
    expect(
      toPlainLeaveError(err('Application period cannot be across two allocation records')),
    ).toBe('These dates cross two leave years. Split the request into two.')
  })

  it('maps outside-allocation-period', () => {
    expect(
      toPlainLeaveError(err('Application period cannot be outside leave allocation period')),
    ).toBe('These dates are outside your current leave year.')
  })

  it('maps no leave period', () => {
    expect(toPlainLeaveError(err('There is no leave period in between these dates'))).toBe(
      'There is no leave calendar set up for these dates. Ask HR.',
    )
  })

  it('maps zero total leave days', () => {
    expect(toPlainLeaveError(err('Total leave days is 0. There is no need to apply for leave.'))).toBe(
      'This request has zero days. Check your start and end dates.',
    )
  })

  it('maps half day date', () => {
    expect(toPlainLeaveError(err('Half day date should be between from date and to date'))).toBe(
      'The half-day date must fall within your leave dates.',
    )
  })

  it('maps to-date-before-from-date', () => {
    expect(toPlainLeaveError(err('To Date cannot be less than From Date'))).toBe(
      'The end date must be on or after the start date.',
    )
  })

  it('maps before-joining-date', () => {
    expect(toPlainLeaveError(err('Leave cannot be before employee\'s joining Date'))).toBe(
      "You can't request leave from before your joining date.",
    )
  })

  it('maps no leave approver', () => {
    expect(toPlainLeaveError(err('Employee HR-EMP-00001 is not associated with any Leave Approver'))).toBe(
      "There's a setup issue with your leave approver. Ask HR.",
    )
  })

  it('falls back to stripped HTML for an unmapped message', () => {
    expect(toPlainLeaveError(err('<div>Some <b>other</b> Frappe error</div>'))).toBe(
      'Some other Frappe error',
    )
  })

  // P4-U4. The three refusals `act_on_approval` added. A throw raised inside
  // `apply_workflow` reaches the browser wrapped in HRMS's own markup, so
  // matching on the sentence is what gets the plain line back to the manager
  // rather than a paragraph of it.
  it('maps the four-outcome refusals back to their plain sentences', () => {
    expect(
      toPlainLeaveError(
        err("<div>Error: That isn't something you can do to this request.</div>"),
      ),
    ).toBe("That isn't something you can do to this request.")
    expect(toPlainLeaveError(err('<b>Say why before rejecting this.</b>'))).toBe(
      'Say why before rejecting this.',
    )
    expect(
      toPlainLeaveError(
        err('Some of those days now have attendance; send this to HR instead.'),
      ),
    ).toBe('Some of those days now have attendance; send this to HR instead.')
  })
})

// P2-U5. The two messages the leave lifecycle added, plus the promise that
// the portal's own plain refusals survive the map untouched.
describe('P2-U5 leave lifecycle messages', () => {
  it('names the next step when no approver is set', () => {
    expect(
      toPlainLeaveError({ messages: ['Leave Approver is mandatory in Leave Application'] }),
    ).toBe("You don't have a leave approver yet, so this can't be sent. Ask HR to set one.")
  })

  it('asks for a leave type rather than repeating the field name', () => {
    expect(toPlainLeaveError({ messages: ['Leave Type is mandatory'] })).toBe(
      'Pick a leave type first.',
    )
  })

  it('passes the portal\'s own plain refusals through unchanged', () => {
    const refusal =
      'This leave has already been decided, so it can\'t be withdrawn. Ask HR to cancel it.'
    expect(toPlainLeaveError({ messages: [refusal] })).toBe(refusal)
  })

  it('passes the reversed-range refusal through unchanged', () => {
    expect(
      toPlainLeaveError({ messages: ['The end date must be on or after the start date.'] }),
    ).toBe('The end date must be on or after the start date.')
  })
})

// P3-U6 step 0 / P3-U6 scenario 7. HRMS reports two of its Attendance
// Request refusals in shapes that are not a plain sentence, and both reach a
// manager mid-decision.
describe('toPlainMessage', () => {
  it('flattens an as_table msgprint, which frappe-ui hands over as a list of lists', () => {
    expect(
      toPlainMessage([
        ['Date', 'Reason', 'Action'],
        ['03-09-2026', 'Holiday', 'Skip'],
      ]),
    ).toBe('Date · Reason · Action. 03-09-2026 · Holiday · Skip')
  })

  it('flattens the same table when it arrives as markup', () => {
    expect(
      toPlainMessage(
        '<table><tr><td>Date</td><td>Reason</td></tr><tr><td>03-09-2026</td><td>Holiday</td></tr></table>',
      ),
    ).toBe('Date · Reason. 03-09-2026 · Holiday')
  })

  it("reads HRMS's overlap error as one sentence", () => {
    expect(
      toPlainMessage(
        'Employee <b>HR-EMP-00001</b> already has an Attendance Request ' +
          '<a href="/app/attendance-request/HR-ARQ-00001">HR-ARQ-00001</a> that overlaps with this period',
      ),
    ).toBe(
      'Employee HR-EMP-00001 already has an Attendance Request HR-ARQ-00001 that overlaps with this period',
    )
  })

  it('leaves a plain sentence exactly as written, full stop included', () => {
    expect(toPlainMessage('This has already been decided. Reload to see the result.')).toBe(
      'This has already been decided. Reload to see the result.',
    )
  })

  it('has an answer for nothing at all', () => {
    expect(toPlainMessage(null)).toBe('')
  })
})
