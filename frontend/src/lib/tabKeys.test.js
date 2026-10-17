import { describe, expect, it } from 'vitest'
import { tabIndexForKey } from './tabKeys'

describe('tabIndexForKey', () => {
  it('moves one tab with the arrow keys and wraps at both ends', () => {
    expect(tabIndexForKey('ArrowRight', 0, 3)).toBe(1)
    expect(tabIndexForKey('ArrowRight', 2, 3)).toBe(0)
    expect(tabIndexForKey('ArrowLeft', 1, 3)).toBe(0)
    expect(tabIndexForKey('ArrowLeft', 0, 3)).toBe(2)
  })

  it('jumps to the ends with Home and End', () => {
    expect(tabIndexForKey('Home', 2, 5)).toBe(0)
    expect(tabIndexForKey('End', 0, 5)).toBe(4)
  })

  it('ignores every other key', () => {
    for (const key of ['ArrowUp', 'ArrowDown', 'Enter', ' ', 'Tab', 'a']) {
      expect(tabIndexForKey(key, 1, 3), key).toBe(-1)
    }
  })

  it('starts from the ends when nothing is selected yet', () => {
    expect(tabIndexForKey('ArrowRight', -1, 4)).toBe(0)
    expect(tabIndexForKey('ArrowLeft', -1, 4)).toBe(3)
    expect(tabIndexForKey('ArrowRight', 9, 4)).toBe(0)
  })

  it('answers -1 for an empty strip', () => {
    expect(tabIndexForKey('ArrowRight', 0, 0)).toBe(-1)
    expect(tabIndexForKey('Home', 0, 0)).toBe(-1)
  })
})
