import { describe, it, expect } from 'vitest'
import { icons, NEEDS_YOU_ICON } from './icons'

// The Dashboard queue draws one glyph per kind, and an unmapped kind falls
// back to the Requests glyph without complaining -- which is exactly how a
// sent-back leave shipped wearing the Requests icon. Asserting the whole set
// against the server's list is what stops the map drifting again.
describe('NEEDS_YOU_ICON', () => {
  // `helixhr.api._get_needs_you`, as of P2 plus the three attendance
  // request kinds P3-U5 adds (pinned here first, in P3-U1). A kind added
  // there fails here first.
  const SERVER_KINDS = [
    'timesheet_rejected',
    'leave_rejected',
    'request_answered',
    'approval_leave',
    'approval_timesheet',
    'leave_waiting',
    'approval_attendance',
    'attendance_request_rejected',
    'attendance_request_waiting',
  ]

  it('covers every kind the server emits, and nothing else', () => {
    expect(Object.keys(NEEDS_YOU_ICON).sort()).toEqual([...SERVER_KINDS].sort())
  })

  it('maps every kind to a glyph that exists', () => {
    for (const kind of SERVER_KINDS) {
      expect(icons[NEEDS_YOU_ICON[kind]], `${kind} -> ${NEEDS_YOU_ICON[kind]}`).toBeTruthy()
    }
  })

  it('sends a sent-back leave to the leave glyph, not the request one', () => {
    expect(NEEDS_YOU_ICON.leave_rejected).toBe('leave')
  })

  it('sends attendance request kinds to the attendance glyph, and the decision to approvals', () => {
    expect(NEEDS_YOU_ICON.attendance_request_rejected).toBe('attendance')
    expect(NEEDS_YOU_ICON.attendance_request_waiting).toBe('attendance')
    expect(NEEDS_YOU_ICON.approval_attendance).toBe('approvals')
  })
})
