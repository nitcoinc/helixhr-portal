import { describe, expect, it } from 'vitest'

import { roundHours } from './hours'

describe('roundHours', () => {
  it('leaves a whole number whole', () => {
    expect(roundHours(8)).toBe(8)
  })

  it('keeps one decimal place', () => {
    // The reported defect: these rendered as 7 and 8 in the day-total row.
    expect(roundHours(7.1)).toBe(7.1)
    expect(roundHours(7.9)).toBe(7.9)
  })

  it('keeps a quarter hour, which the stepper produces', () => {
    expect(roundHours(4.25)).toBe(4.25)
    expect(roundHours(7.75)).toBe(7.75)
  })

  it('clears the drift that summing floats introduces', () => {
    expect(roundHours(0.1 + 0.2)).toBe(0.3)
    expect(roundHours(8 + 7.1 + 7.9)).toBe(23)
  })

  it('treats a missing or unparseable value as no hours', () => {
    expect(roundHours(null)).toBe(0)
    expect(roundHours(undefined)).toBe(0)
    expect(roundHours('')).toBe(0)
    expect(roundHours(Number.NaN)).toBe(0)
  })

  it('reads a numeric string, which is what a number input hands back', () => {
    expect(roundHours('7.1')).toBe(7.1)
  })
})
