import { describe, it, expect } from 'vitest'
import { toPlainLeaveError } from './errorMap'

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
})
