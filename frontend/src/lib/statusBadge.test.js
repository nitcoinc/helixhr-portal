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
      // P4-KTD1: two states, two words, one measured tone pair.
      ['Sent Back', 'Sent back', TONE.sentBack],
      ['Rejected', 'Rejected', TONE.sentBack],
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

  // P4-U4 / P4-KTD4. One HRMS status, two outcomes, told apart by docstatus:
  // unsubmitted is the recoverable send-back, submitted is terminal. Getting
  // this wrong words a final no as "we'd like a change please".
  it('tells leave send-back from leave rejection by docstatus', () => {
    expect(resolveStatus({ kind: 'leave', status: 'Rejected', docstatus: 0 })).toEqual({
      label: 'Sent back',
      toneClass: TONE.sentBack,
    })
    expect(resolveStatus({ kind: 'leave', status: 'Rejected', docstatus: 1 })).toEqual({
      label: 'Rejected',
      toneClass: TONE.sentBack,
    })
    // No docstatus at all still reads as the recoverable one, which is the
    // state a caller that has not been taught about the difference means.
    expect(resolveStatus({ kind: 'leave', status: 'Rejected' }).label).toBe('Sent back')
    // Cancelled still outranks both (P3-KTD6).
    expect(resolveStatus({ kind: 'leave', status: 'Rejected', docstatus: 2 }).label).toBe('Withdrawn')
  })

  // P4-R5, P4-R7. The word for a request a manager handed to HR, and for a
  // leave type that starts there. Leave has no Workflow, so Leave.vue hands
  // the stage in under the same key the other two kinds use.
  it('words the HR stage the same on all three kinds', () => {
    for (const kind of ['leave', 'timesheet', 'attendance']) {
      expect(resolveStatus({ kind, status: 'Pending HR' }), kind).toEqual({
        label: 'Waiting for HR',
        toneClass: TONE.waiting,
      })
    }
  })

  // P4-KTD1/P4-KTD2. The rename patch moved every docstatus-0 `Rejected`
  // timesheet to `Sent Back`, and no transition can produce `Rejected` on a
  // week any more -- so one that somehow exists must look unfamiliar rather
  // than be re-worded into a send-back the employee could act on.
  it('words the timesheet send-back and leaves a stray Rejected week alone', () => {
    expect(resolveStatus({ kind: 'timesheet', status: 'Sent Back' })).toEqual({
      label: 'Sent back',
      toneClass: TONE.sentBack,
    })
    expect(resolveStatus({ kind: 'timesheet', status: 'Rejected' })).toEqual({
      label: 'Rejected',
      toneClass: TONE.resting,
    })
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
