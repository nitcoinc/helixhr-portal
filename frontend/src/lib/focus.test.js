import { describe, expect, it, vi } from 'vitest'
import { focusNear } from './focus'

const node = (name) => ({ name, focus: vi.fn() })

describe('focusNear', () => {
  it('focuses the node at the index', () => {
    const nodes = [node('a'), node('b'), node('c')]
    expect(focusNear(nodes, 1)).toBe(nodes[1])
    expect(nodes[1].focus).toHaveBeenCalledOnce()
  })

  it('clamps past the end to the last node and below zero to the first', () => {
    const nodes = [node('a'), node('b')]
    expect(focusNear(nodes, 5)).toBe(nodes[1])
    expect(focusNear(nodes, -1)).toBe(nodes[0])
  })

  it('falls back to the first fallback that exists when the list is empty', () => {
    const heading = node('heading')
    expect(focusNear([], 0, null, undefined, heading, node('main'))).toBe(heading)
    expect(heading.focus).toHaveBeenCalledOnce()
  })

  it('focuses nothing when nothing exists', () => {
    expect(focusNear([], 2, null)).toBeUndefined()
  })
})
