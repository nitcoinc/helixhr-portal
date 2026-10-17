import { beforeEach, describe, expect, it } from 'vitest'
import { dismiss, MAX_TOASTS, notify, toasts } from './notify'

describe('notify', () => {
  beforeEach(() => toasts.splice(0))

  it('queues in order with distinct ids, success by default', () => {
    const first = notify('Saved.')
    const second = notify.error('That did not save.', { detail: 'Try again.' })
    expect(second).toBeGreaterThan(first)
    expect(toasts.map(({ tone, text, detail }) => [tone, text, detail])).toEqual([
      ['success', 'Saved.', ''],
      ['error', 'That did not save.', 'Try again.'],
    ])
  })

  it('reads an unknown tone as success, never as a third style', () => {
    notify('Done.', { tone: 'warning' })
    expect(toasts[0].tone).toBe('success')
  })

  it('drops the oldest once the queue is full', () => {
    for (let n = 1; n <= MAX_TOASTS + 2; n++) notify.success(`Message ${n}`)
    expect(toasts.map((toast) => toast.text)).toEqual(
      Array.from({ length: MAX_TOASTS }, (_, i) => `Message ${i + 3}`),
    )
  })

  it('drops a success before an older error, since an error waits to be dismissed', () => {
    notify.error('Failed')
    for (let n = 1; n <= MAX_TOASTS; n++) notify.success(`Message ${n}`)
    expect(toasts.map((toast) => toast.text)).toEqual(['Failed', 'Message 2', 'Message 3'])
  })

  it('dismisses one message by id and ignores an id already gone', () => {
    const keep = notify.success('Keep')
    const drop = notify.error('Drop')
    dismiss(drop)
    dismiss(drop)
    expect(toasts.map((toast) => toast.id)).toEqual([keep])
  })
})
