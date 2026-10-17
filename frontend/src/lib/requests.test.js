import { describe, expect, it } from 'vitest'
import { fileSize, isOwed, latestOnly, myRequestSections, slaOverLabel } from './requests'

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

describe('myRequestSections', () => {
  const group = (names, total = names.length, limit = 20) => ({
    rows: names.map((name) => row(name, 'Open')),
    total,
    limit,
  })

  it('labels the server groups in order and keeps their row order', () => {
    const sections = myRequestSections({
      closed: group(['c2', 'c1']),
      open: group(['o1']),
      needs_you: group(['w2', 'w1'], 2, 100),
    })
    expect(sections.map((s) => [s.key, s.label, s.rows.map((r) => r.name)])).toEqual([
      ['needs_you', 'Needs you', ['w2', 'w1']],
      ['open', 'Open', ['o1']],
      ['closed', 'Closed', ['c2', 'c1']],
    ])
    expect(sections[0].limit).toBe(100)
  })
  it('says how many more each group holds, never negative', () => {
    const sections = myRequestSections({ open: group(['o1', 'o2'], 25), closed: group(['c1'], 0) })
    expect(sections.map((s) => [s.key, s.more])).toEqual([
      ['open', 23],
      ['closed', 0],
    ])
  })
  it('drops empty groups, and answers nothing without groups', () => {
    expect(myRequestSections({ needs_you: group([]), open: group(['o']), closed: group([], 4) }).map((s) => s.key)).toEqual(['open'])
    expect(myRequestSections(undefined)).toEqual([])
    expect(myRequestSections({})).toEqual([])
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

describe('latestOnly', () => {
  // A fetcher whose calls resolve or reject only when the test says so.
  function deferredFetcher() {
    const calls = []
    const fetcher = (options) =>
      new Promise((resolve, reject) => calls.push({ options, resolve, reject }))
    return { calls, fetcher }
  }

  it('settles an overtaken call with the newest answer, whichever lands first', async () => {
    const { calls, fetcher } = deferredFetcher()
    const fetch = latestOnly(fetcher)
    const first = fetch({ q: '' })
    const second = fetch({ q: 'laptop' })
    calls[1].resolve('newest')
    calls[0].resolve('stale')
    await expect(first).resolves.toBe('newest')
    await expect(second).resolves.toBe('newest')
  })

  it('waits for the newest call when the overtaken one lands first', async () => {
    const { calls, fetcher } = deferredFetcher()
    const fetch = latestOnly(fetcher)
    const first = fetch({})
    const second = fetch({})
    let settled = false
    first.then(() => (settled = true))
    calls[0].resolve('stale')
    await Promise.resolve()
    await Promise.resolve()
    expect(settled).toBe(false)
    calls[1].resolve('newest')
    await expect(first).resolves.toBe('newest')
    await expect(second).resolves.toBe('newest')
  })

  it("drops an overtaken call's failure and reports only the newest one's", async () => {
    const { calls, fetcher } = deferredFetcher()
    const fetch = latestOnly(fetcher)
    const first = fetch({})
    const second = fetch({})
    calls[0].reject(new Error('stale'))
    calls[1].reject(new Error('newest'))
    await expect(first).rejects.toThrow('newest')
    await expect(second).rejects.toThrow('newest')
  })

  it('passes a lone call straight through', async () => {
    const fetch = latestOnly(async (options) => options.n * 2)
    await expect(fetch({ n: 2 })).resolves.toBe(4)
  })
})
