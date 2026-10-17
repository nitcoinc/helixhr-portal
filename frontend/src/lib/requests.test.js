import { describe, expect, it } from 'vitest'
import { byClosedNewest, fileSize, isOwed, myRequestGroups, slaOverLabel } from './requests'

const row = (name, status, extra = {}) => ({ name, status, unread: false, unread_reply: false, ...extra })

describe('isOwed (KTD12)', () => {
  it('owes a request waiting on the employee, read or not', () => {
    expect(isOwed(row('a', 'Waiting on Employee'))).toBe(true)
  })
  it('owes a request holding an unread message from the team', () => {
    expect(isOwed(row('a', 'Done', { unread: true, unread_reply: true }))).toBe(true)
  })
  it('does not owe an unread status change', () => {
    expect(isOwed(row('a', 'Done', { unread: true }))).toBe(false)
    expect(isOwed(row('a', 'Open', { unread: true }))).toBe(false)
  })
})

describe('myRequestGroups', () => {
  it('keeps an unread Done with no message in Closed, not Needs you', () => {
    const groups = myRequestGroups([
      row('done', 'Done', { unread: true }),
      row('reply', 'In Progress', { unread: true, unread_reply: true }),
      row('wait', 'Waiting on Employee'),
      row('open', 'Open', { unread: true }),
      row('declined', 'Rejected'),
    ])
    expect(groups.map((group) => [group.key, group.rows.map((r) => r.name)])).toEqual([
      ['needs-you', ['reply', 'wait']],
      ['open', ['open']],
      ['closed', ['done', 'declined']],
    ])
  })
  it('lists Closed most recently closed first, whatever order the rows were sent in', () => {
    const groups = myRequestGroups([
      row('sent-new-closed-old', 'Done', { creation: '2026-10-10 09:00:00', closed_on: '2026-10-11 09:00:00' }),
      row('open', 'Open', { creation: '2026-10-09 09:00:00' }),
      row('sent-old-closed-new', 'Rejected', { creation: '2026-01-05 09:00:00', closed_on: '2026-10-16 17:30:00' }),
      row('undated-new', 'Done', { creation: '2026-09-01 09:00:00' }),
      row('sent-mid-closed-mid', 'Done', { creation: '2026-05-01 09:00:00', closed_on: '2026-10-12 08:00:00' }),
      row('undated-old', 'Rejected', { creation: '2025-12-01 09:00:00' }),
    ])
    expect(groups.find((group) => group.key === 'closed').rows.map((r) => r.name)).toEqual([
      'sent-old-closed-new',
      'sent-mid-closed-mid',
      'sent-new-closed-old',
      'undated-new',
      'undated-old',
    ])
    expect(groups.find((group) => group.key === 'open').rows.map((r) => r.name)).toEqual(['open'])
  })
  it('does not reorder Needs you or Open', () => {
    const groups = myRequestGroups([
      row('b', 'Open', { creation: '2026-10-01 09:00:00' }),
      row('a', 'Open', { creation: '2026-10-05 09:00:00' }),
      row('w2', 'Waiting on Employee', { creation: '2026-09-01 09:00:00' }),
      row('w1', 'Waiting on Employee', { creation: '2026-10-02 09:00:00' }),
    ])
    expect(groups.map((group) => [group.key, group.rows.map((r) => r.name)])).toEqual([
      ['needs-you', ['w2', 'w1']],
      ['open', ['b', 'a']],
    ])
  })
  it('drops empty groups', () => {
    expect(myRequestGroups([row('open', 'Open')]).map((group) => group.key)).toEqual(['open'])
    expect(myRequestGroups(null)).toEqual([])
  })
})

describe('byClosedNewest', () => {
  it('puts dated rows first, newest first, then undated by creation', () => {
    const rows = [
      { name: 'u', creation: '2026-10-15 00:00:00' },
      { name: 'old', creation: '2026-01-01 00:00:00', closed_on: '2026-02-01 00:00:00' },
      { name: 'new', creation: '2026-01-02 00:00:00', closed_on: '2026-10-01 00:00:00' },
    ]
    expect([...rows].sort(byClosedNewest).map((r) => r.name)).toEqual(['new', 'old', 'u'])
  })
  it('breaks a closed_on tie by creation, newest first', () => {
    const same = '2026-10-01 10:00:00'
    const rows = [
      { name: 'older', creation: '2026-01-01 00:00:00', closed_on: same },
      { name: 'newer', creation: '2026-03-01 00:00:00', closed_on: same },
    ]
    expect([...rows].sort(byClosedNewest).map((r) => r.name)).toEqual(['newer', 'older'])
  })
})

describe('slaOverLabel', () => {
  it('says how far past, or falls back to the plain pill', () => {
    expect(slaOverLabel(2)).toBe('2 d over')
    expect(slaOverLabel(null)).toBe('Past SLA')
    expect(slaOverLabel(0)).toBe('Past SLA')
  })
})

describe('fileSize', () => {
  it('reads bytes as a person does', () => {
    expect(fileSize(0)).toBe('')
    expect(fileSize(512)).toBe('512 B')
    expect(fileSize(2048)).toBe('2 KB')
    expect(fileSize(1.5 * 1024 * 1024)).toBe('1.5 MB')
  })
})
