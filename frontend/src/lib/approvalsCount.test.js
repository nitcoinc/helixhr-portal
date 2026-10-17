import { beforeEach, describe, expect, it } from 'vitest'
import {
  approvalsLabel,
  currentApprovals,
  decrementApprovals,
  setApprovalsCount,
} from './approvalsCount'

describe('approvals count (KTD8)', () => {
  beforeEach(() => setApprovalsCount(0, false))

  it('is seeded by the bootstrap and set by the Approvals page', () => {
    setApprovalsCount(7, false)
    expect(approvalsLabel()).toBe('7')
    setApprovalsCount(4, false)
    expect(currentApprovals()).toBe(4)
  })

  it('says "N+" only when the count is a floor', () => {
    setApprovalsCount(150, true)
    expect(approvalsLabel()).toBe('150+')
    decrementApprovals()
    expect(approvalsLabel()).toBe('149+')
  })

  it('takes decisions off locally and never goes below zero', () => {
    setApprovalsCount(2, false)
    decrementApprovals()
    expect(approvalsLabel()).toBe('1')
    decrementApprovals(3)
    expect(currentApprovals()).toBe(0)
    expect(approvalsLabel()).toBe('')
  })

  it('reads a missing or garbled answer as zero', () => {
    setApprovalsCount(undefined, undefined)
    expect(currentApprovals()).toBe(0)
    setApprovalsCount('nope')
    expect(approvalsLabel()).toBe('')
  })
})
