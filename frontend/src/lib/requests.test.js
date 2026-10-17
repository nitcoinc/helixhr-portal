import { describe, expect, it } from 'vitest'
import { fileSize, isOwed, myRequestGroups, slaOverLabel } from './requests'

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
  it('drops empty groups', () => {
    expect(myRequestGroups([row('open', 'Open')]).map((group) => group.key)).toEqual(['open'])
    expect(myRequestGroups(null)).toEqual([])
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
