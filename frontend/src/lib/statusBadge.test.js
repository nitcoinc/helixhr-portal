import { describe, it, expect } from 'vitest'
import { KINDS, TONE, resolveStatus } from './statusBadge'

// P3-U1 scenario 5: the first unit test for StatusBadge. The component is a
// one-line renderer over `resolveStatus`, so the mapping is what is held.
describe('resolveStatus', () => {
  it('maps every attendance request state to the portal vocabulary', () => {
    const cases = [
      ['Draft', 'Draft', TONE.resting],
      ['Pending Manager', 'Waiting for manager', TONE.waiting],
      ['Pending HR', 'Waiting for HR', TONE.waiting],
      ['Approved', 'Counted', TONE.done],
      ['Rejected', 'Sent back', TONE.sentBack],
    ]
    for (const [status, label, toneClass] of cases) {
      expect(resolveStatus({ kind: 'attendance', status }), status).toEqual({ label, toneClass })
    }
  })

  it('names the manager on the manager step only', () => {
    expect(
      resolveStatus({ kind: 'attendance', status: 'Pending Manager', approver: 'Priya' }).label,
    ).toBe('Waiting for Priya')
    expect(resolveStatus({ kind: 'attendance', status: 'Pending HR', approver: 'Priya' }).label).toBe(
      'Waiting for HR',
    )
  })

  it('keys Cancelled on docstatus 2 regardless of the state (P3-KTD6)', () => {
    for (const status of ['Approved', 'Pending HR', 'Draft', '']) {
      expect(resolveStatus({ kind: 'attendance', status, docstatus: 2 }), status).toEqual({
        label: 'Cancelled',
        toneClass: TONE.resting,
      })
    }
    // Leave keeps its own word for the same fact.
    expect(resolveStatus({ kind: 'leave', status: 'Approved', docstatus: 2 }).label).toBe('Withdrawn')
    // Any other docstatus leaves the state alone.
    expect(resolveStatus({ kind: 'attendance', status: 'Approved', docstatus: 1 }).label).toBe('Counted')
  })

  it('keeps the P2 vocabulary for the three older kinds', () => {
    expect(resolveStatus({ kind: 'leave', status: 'Open', approver: 'Priya' }).label).toBe(
      'Waiting for Priya',
    )
    expect(resolveStatus({ kind: 'leave', status: 'Open' }).label).toBe('Waiting')
    expect(resolveStatus({ kind: 'timesheet', status: 'Pending Approval' }).label).toBe(
      'Waiting for manager',
    )
    expect(resolveStatus({ kind: 'request', status: 'Rejected' }).label).toBe('Sent back')
    expect(resolveStatus({ kind: 'leave', status: 'Cancelled' }).label).toBe('Withdrawn')
  })

  it('shows an unmapped status as itself, at rest', () => {
    expect(resolveStatus({ kind: 'attendance', status: 'Escalated' })).toEqual({
      label: 'Escalated',
      toneClass: TONE.resting,
    })
    expect(resolveStatus({ kind: 'attendance', status: '' }).label).toBe('—')
  })

  it('lists attendance among the kinds the component validates', () => {
    expect(KINDS).toContain('attendance')
  })
})
