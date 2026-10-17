import { describe, it, expect } from 'vitest'
import { kindPage, queueDestination, sideListDestination } from './needsYou'

describe('queueDestination (EP-09)', () => {
  it('sends an approver to Approvals', () => {
    expect(queueDestination({ canApprove: true, canHandleRequests: true })).toBe('/approvals')
  })

  it('sends a request handler to the Team inbox', () => {
    const inbox = { path: '/requests', query: { tab: 'work' } }
    expect(queueDestination({ canHandleRequests: true })).toEqual(inbox)
    expect(queueDestination({ canWorkRequests: true })).toEqual(inbox)
  })

  it('never sends a plain employee to a page they do not have', () => {
    expect(queueDestination({})).toBe('/notifications')
    expect(queueDestination(null)).toBe('/notifications')
  })
})

describe('kindPage', () => {
  it('maps each employee-side kind to the page that owns its record', () => {
    expect(kindPage('leave_waiting')).toBe('/leave')
    expect(kindPage('leave_final_rejected')).toBe('/leave')
    expect(kindPage('attendance_request_waiting')).toBe('/attendance')
    expect(kindPage('attendance_request_final_rejected')).toBe('/attendance')
    expect(kindPage('request_decided')).toBe('/requests')
    expect(kindPage('timesheet_rejected')).toBe('/timesheet')
    expect(kindPage('approval_leave')).toBeNull()
  })
})

describe('sideListDestination', () => {
  it('opens the page that owns most of the rows', () => {
    const rows = [
      { kind: 'attendance_request_waiting' },
      { kind: 'attendance_request_waiting' },
      { kind: 'leave_waiting' },
    ]
    expect(sideListDestination(rows)).toBe('/attendance')
  })

  it('falls back to Leave', () => {
    expect(sideListDestination([])).toBe('/leave')
    expect(sideListDestination(undefined)).toBe('/leave')
  })
})
