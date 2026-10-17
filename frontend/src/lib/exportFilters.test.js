import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { configureCalendar, resetCalendar } from './dates'
import { summarizeExportFilters } from './exportFilters'

// Pinned locale so the copy is stable; dates.test.js owns the calendar rules.
beforeEach(() => configureCalendar({ timeZone: 'UTC', systemTimeZone: 'UTC', locale: 'en-GB' }))
afterEach(() => resetCalendar())

describe('summarizeExportFilters', () => {
  it('collapses the open-ended 2000..2100 window to All dates', () => {
    expect(
      summarizeExportFilters({ company: '_Test Company', from_date: '2000-01-01', to_date: '2100-01-01' }),
    ).toBe('All dates, Company: _Test Company')
  })

  it('formats a real range and labels known keys', () => {
    expect(
      summarizeExportFilters({ from_date: '2026-10-01', to_date: '2026-10-31', employment_type: 'Full-time' }),
    ).toBe('1 Oct – 31 Oct 2026, Employment type: Full-time')
  })

  it('names a one-sided range', () => {
    expect(summarizeExportFilters({ from_date: '2000-01-01', to_date: '2026-10-31' })).toBe('Until 31 Oct 2026')
    expect(summarizeExportFilters({ from_date: '2026-10-01', to_date: '2100-01-01' })).toBe('From 1 Oct 2026')
  })

  it('prefers the project name, formats a month, and has no stray space before the group', () => {
    const text = summarizeExportFilters(
      { basis: 'All hours', month: '2016-03', project: 'PROJ-0016', project_name: 'Website relaunch' },
      'date',
    )
    expect(text).toMatch(/^Hours basis: All hours, Month: March 2016, Project: Website relaunch; grouped by date$/)
    expect(text).not.toContain('project name')
  })

  it('falls back to the project ID when no name was stored', () => {
    expect(summarizeExportFilters({ project: 'PROJ-0016' })).toBe('Project: PROJ-0016')
  })

  it('reads the combined-company sentinel and toggles in words', () => {
    expect(summarizeExportFilters({ company: '__all', project_members_only: 1 })).toBe(
      'Company: All my companies, Project members only: Yes',
    )
  })

  it('turns an unknown key into words and survives empty input', () => {
    expect(summarizeExportFilters({ cost_center: 'Main' })).toBe('Cost center: Main')
    expect(summarizeExportFilters(null)).toBe('')
    expect(summarizeExportFilters({}, 'employee, week_start')).toBe('Grouped by employee, week start')
  })
})
