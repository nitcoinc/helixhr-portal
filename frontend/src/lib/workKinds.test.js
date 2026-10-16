import { describe, it, expect } from 'vitest'
import { getKindTag, getActionButtons, getReasonCopy } from './workKinds'

describe('workKinds', () => {
  describe('getKindTag', () => {
    it('returns the IT request tag for a request routed to IT Team', () => {
      expect(getKindTag('request', 'IT Team')).toEqual({
        label: 'IT request',
        icon: 'itRequest',
      })
      expect(getKindTag({ kind: 'request', routed_to_role: 'IT Team' })).toEqual({
        label: 'IT request',
        icon: 'itRequest',
      })
    })

    it('returns the HR request tag for a request routed to HR Manager', () => {
      expect(getKindTag('request', 'HR Manager')).toEqual({
        label: 'HR request',
        icon: 'requests',
      })
      expect(getKindTag({ kind: 'request', routed_to_role: 'HR Manager' })).toEqual({
        label: 'HR request',
        icon: 'requests',
      })
      expect(getKindTag('request')).toEqual({
        label: 'HR request',
        icon: 'requests',
      })
    })

    it('returns tags for timesheet, change, leave, and attendance', () => {
      expect(getKindTag('timesheet')).toEqual({ label: 'Timesheet', icon: 'timesheet' })
      expect(getKindTag('change')).toEqual({ label: 'Timesheet change', icon: 'timesheetChange' })
      expect(getKindTag('leave')).toEqual({ label: 'Leave', icon: 'leave' })
      expect(getKindTag('attendance')).toEqual({ label: 'Attendance', icon: 'attendance' })
    })
  })

  describe('getActionButtons', () => {
    it('produces ordered buttons for server actions Accept and Decline on a change', () => {
      const buttons = getActionButtons('change', ['Accept', 'Decline'])
      expect(buttons).toEqual([
        { action: 'Accept', label: 'Approve change', variant: 'solid' },
        { action: 'Decline', label: 'Decline', variant: 'subtle' },
      ])
    })

    it('drops actions the map does not know', () => {
      const buttons = getActionButtons('change', ['Accept', 'SomethingUnknown'])
      expect(buttons).toEqual([
        { action: 'Accept', label: 'Approve change', variant: 'solid' },
      ])
    })

    it('drops words whose action is missing from the server list', () => {
      const buttons = getActionButtons('change', ['Decline'])
      expect(buttons).toEqual([
        { action: 'Decline', label: 'Decline', variant: 'subtle' },
      ])
    })

    it('returns empty list for no server actions', () => {
      expect(getActionButtons('timesheet', [])).toEqual([])
    })
  })

  describe('getReasonCopy', () => {
    it('returns copy for known actions', () => {
      expect(getReasonCopy('Send Back').heading).toBe('Send back with a reason')
      expect(getReasonCopy('Decline').heading).toBe('Decline with a reason')
      expect(getReasonCopy('Need info').heading).toBe('Ask a question')
      expect(getReasonCopy('Reject').heading).toBe('Reject with a reason')
    })

    it('falls back to Send Back copy for unknown action', () => {
      expect(getReasonCopy('Unknown')).toEqual(getReasonCopy('Send Back'))
    })
  })
})
