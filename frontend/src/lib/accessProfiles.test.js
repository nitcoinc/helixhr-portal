// Plan 2026-10-15-001 R10: unit tests for profile matching.
import { describe, expect, it } from 'vitest'
import { matchProfiles, getProfiles } from './accessProfiles'

describe('matchProfiles', () => {
  it('returns Employee for no roles and no grant', () => {
    const matches = matchProfiles([], null)
    expect(matches).toHaveLength(1)
    expect(matches[0].key).toBe('employee')
  })

  it('returns IT when IT Team is held', () => {
    const matches = matchProfiles(['IT Team'], null)
    expect(matches.some((p) => p.key === 'it')).toBe(true)
    expect(matches.some((p) => p.key === 'employee')).toBe(false)
  })

  it('returns IT and Delivery Manager when both roles are held', () => {
    const matches = matchProfiles(['IT Team', 'HelixHR Delivery Manager'], null)
    expect(matches.some((p) => p.key === 'it')).toBe(true)
    expect(matches.some((p) => p.key === 'delivery-manager')).toBe(true)
  })

  it('returns only Custom when a role no profile names is held', () => {
    expect(matchProfiles(['IT Team', 'Some Unknown Role'], null).map((p) => p.key)).toEqual(['custom'])
  })

  it('returns only Custom for an unknown grant level', () => {
    expect(matchProfiles([], 'Everything').map((p) => p.key)).toEqual(['custom'])
  })

  it('returns Leadership when grant is All data', () => {
    const matches = matchProfiles([], 'All data')
    expect(matches.some((p) => p.key === 'leadership')).toBe(true)
  })

  it('returns Reports across companies for Reports only grant', () => {
    const matches = matchProfiles([], 'Reports only')
    expect(matches.some((p) => p.key === 'cross-company-reports')).toBe(true)
  })

  it('returns both IT chip and Leadership for role+grant', () => {
    const matches = matchProfiles(['IT Team'], 'All data')
    expect(matches.some((p) => p.key === 'it')).toBe(true)
    expect(matches.some((p) => p.key === 'leadership')).toBe(true)
    expect(matches.some((p) => p.key === 'employee')).toBe(false)
  })

  it('Leadership followed by cross-company leaves only cross-company', () => {
    // Both set the same grant field; the last one wins.
    const matches = matchProfiles([], 'Reports only')
    expect(matches.some((p) => p.key === 'cross-company-reports')).toBe(true)
    expect(matches.some((p) => p.key === 'leadership')).toBe(false)
  })
})

describe('getProfiles', () => {
  it('includes HR profiles for System Manager', () => {
    const profiles = getProfiles(true)
    expect(profiles.some((p) => p.key === 'hr-user')).toBe(true)
    expect(profiles.some((p) => p.key === 'hr-manager')).toBe(true)
  })

  it('excludes HR profiles for non-System Manager', () => {
    const profiles = getProfiles(false)
    expect(profiles.some((p) => p.key === 'hr-user')).toBe(false)
    expect(profiles.some((p) => p.key === 'hr-manager')).toBe(false)
  })
})