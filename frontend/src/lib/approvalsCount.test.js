import { beforeEach, describe, expect, it } from 'vitest'
import {
  approvalsLabel,
  decrementApprovals,
  setApprovalsCount,
} from './approvalsCount'

describe('approvals count (KTD8)', () => {
  beforeEach(() => setApprovalsCount(0, false))

  it('is seeded by the bootstrap and set by the Approvals page', () => {
    setApprovalsCount(7, false)
    expect(approvalsLabel()).toBe('7')
    setApprovalsCount(4, false)
    expect(approvalsLabel()).toBe('4')
  })

  it('says "N+" only when the count is a floor', () => {
    setApprovalsCount(150, true)
    expect(approvalsLabel()).toBe('150+')
    decrementApprovals()
    expect(approvalsLabel()).toBe('149+')
  })

  it('keeps the current floor flag when a count comes without one', () => {
    setApprovalsCount(150, true)
    setApprovalsCount(149)
    expect(approvalsLabel()).toBe('149+')
    setApprovalsCount(7, false)
    setApprovalsCount(6)
    expect(approvalsLabel()).toBe('6')
  })

  it('takes decisions off locally and never goes below zero', () => {
    setApprovalsCount(2, false)
    decrementApprovals()
    expect(approvalsLabel()).toBe('1')
    decrementApprovals(3)
    expect(approvalsLabel()).toBe('')
  })

  it('reads a missing or garbled answer as zero', () => {
    setApprovalsCount(undefined, undefined)
    expect(approvalsLabel()).toBe('')
    setApprovalsCount('nope')
    expect(approvalsLabel()).toBe('')
  })
})
