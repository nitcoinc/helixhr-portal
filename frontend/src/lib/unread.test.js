import { beforeEach, describe, expect, it, vi } from 'vitest'

// Review fix: markReadKeepalive moves the badge before the write answers,
// settles to the server's count when it sends one, and never rejects.
const { resource, keepaliveRequest } = vi.hoisted(() => {
  const resource = {
    data: null,
    setData: (value) => {
      resource.data = value
    },
    reload: () => {},
  }
  return { resource, keepaliveRequest: { impl: null } }
})

vi.mock('frappe-ui', () => ({ createResource: () => resource }))
vi.mock('./api', () => ({ keepaliveRequest: (...args) => keepaliveRequest.impl(...args) }))
vi.mock('./session', () => ({ session: { unread: 0 } }))

const { currentUnread, markReadKeepalive } = await import('./unread')

/** A write whose answer arrives when the test says so. */
function heldWrite() {
  let settle
  const promise = new Promise((resolve, reject) => {
    settle = { resolve, reject }
  })
  keepaliveRequest.impl = vi.fn(() => promise)
  return settle
}

describe('markReadKeepalive', () => {
  beforeEach(() => {
    resource.data = 5
    resource.reload = vi.fn()
  })

  it('drops the count by `decrement` before the write answers', async () => {
    const write = heldWrite()
    const done = markReadKeepalive('helixhr.api.mark_read', { name: 'N-1' }, 1)
    expect(currentUnread()).toBe(4)
    expect(keepaliveRequest.impl).toHaveBeenCalledWith('helixhr.api.mark_read', { name: 'N-1' })
    write.resolve({})
    await done
  })

  it("settles to the server's numeric unread", async () => {
    const write = heldWrite()
    const done = markReadKeepalive('m', {}, 2)
    expect(currentUnread()).toBe(3)
    write.resolve({ unread: 7 })
    await done
    expect(currentUnread()).toBe(7)
  })

  it('keeps the decremented count when the answer has no numeric unread', async () => {
    const write = heldWrite()
    const done = markReadKeepalive('m', {}, 1)
    write.resolve({ unread: '7' })
    await done
    expect(currentUnread()).toBe(4)
    expect(resource.reload).not.toHaveBeenCalled()
  })

  it('never goes below zero', async () => {
    const write = heldWrite()
    const done = markReadKeepalive('m', {}, 9)
    expect(currentUnread()).toBe(0)
    write.resolve(undefined)
    await done
    expect(currentUnread()).toBe(0)
  })

  it('does not reject when the write fails, and re-reads the count instead', async () => {
    const write = heldWrite()
    const done = markReadKeepalive('m', {}, 1)
    write.reject(new Error('offline'))
    await expect(done).resolves.toBeUndefined()
    expect(resource.reload).toHaveBeenCalledTimes(1)
    expect(currentUnread()).toBe(4)
  })
})
