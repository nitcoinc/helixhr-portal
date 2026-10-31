import { describe, expect, it } from 'vitest'
import {
  NO_FILTERS,
  filterProjects,
  filtersFromQuery,
  filtersToQuery,
  isFiltered,
  matchRows,
  membersLabel,
  parseTaskLines,
  showCompanyFilter,
  sortProjects,
} from './projects'

const rows = [
  { name: 'PROJ-0001', project_name: 'Alpha build', status: 'Open', project_type: 'External', billable: true, company: 'Inc', member_count: 3, open_task_count: 5, expected_end_date: '2026-12-01' },
  { name: 'PROJ-0002', project_name: 'Bench time', status: 'Open', project_type: 'Internal', billable: false, company: 'Inc', member_count: 1, open_to_all: true, open_task_count: 0, expected_end_date: null },
  { name: 'PROJ-0003', project_name: 'Archive', status: 'Completed', project_type: 'External', billable: true, company: 'Inc', member_count: 9, open_task_count: 2, expected_end_date: '2025-01-31' },
  { name: 'PROJ-0004', project_name: 'Alpha support', status: 'Open', project_type: null, billable: true, company: 'Inc', member_count: 2, open_task_count: 1, expected_end_date: '2027-03-01' },
]
const names = (list) => list.map((row) => row.name)

describe('filterProjects', () => {
  it('keeps everything with no filter', () => {
    expect(names(filterProjects(rows, NO_FILTERS))).toEqual(names(rows))
  })
  it('combines text, status, type and billable', () => {
    const filters = { ...NO_FILTERS, q: 'alpha', status: 'Open', type: 'External', billable: 'yes' }
    expect(names(filterProjects(rows, filters))).toEqual(['PROJ-0001'])
    expect(names(filterProjects(rows, { ...NO_FILTERS, billable: 'no' }))).toEqual(['PROJ-0002'])
    expect(names(filterProjects(rows, { ...NO_FILTERS, q: 'alpha', billable: 'yes' }))).toEqual(['PROJ-0001', 'PROJ-0004'])
  })
  it('matches the project code as well as the name', () => {
    expect(names(filterProjects(rows, { ...NO_FILTERS, q: 'proj-0003' }))).toEqual(['PROJ-0003'])
  })
  it('ignores a company filter while the list has one company', () => {
    expect(showCompanyFilter(rows)).toBe(false)
    expect(filterProjects(rows, { ...NO_FILTERS, company: 'Outsourcing' })).toHaveLength(4)
  })
  it('applies the company filter when the list spans companies', () => {
    const mixed = [...rows, { name: 'PROJ-0005', project_name: 'Other', status: 'Open', company: 'Outsourcing' }]
    expect(showCompanyFilter(mixed)).toBe(true)
    expect(names(filterProjects(mixed, { ...NO_FILTERS, company: 'Outsourcing' }))).toEqual(['PROJ-0005'])
  })
})

describe('query round trip', () => {
  it('restores the same filters', () => {
    const filters = { q: 'alpha', status: 'Open', type: 'External', billable: 'no', company: 'Inc' }
    expect(filtersFromQuery(filtersToQuery(filters))).toEqual(filters)
  })
  it('writes only what is chosen, and nothing for no filter', () => {
    expect(filtersToQuery(NO_FILTERS)).toEqual({})
    expect(filtersToQuery({ ...NO_FILTERS, q: '  ', billable: 'yes' })).toEqual({ billable: 'yes' })
    expect(isFiltered(NO_FILTERS)).toBe(false)
    expect(isFiltered({ ...NO_FILTERS, type: 'Internal' })).toBe(true)
  })
  it('reads malformed values as not chosen', () => {
    expect(filtersFromQuery({ billable: 'maybe', status: ['Open', 'Completed'], q: 42 })).toEqual({
      ...NO_FILTERS,
      status: 'Open',
    })
    expect(filtersFromQuery({ q: 'x'.repeat(500) }).q).toHaveLength(140)
  })
})

describe('sortProjects', () => {
  it('sorts by name A-Z by default', () => {
    expect(names(sortProjects(rows, { key: 'name' }))).toEqual(['PROJ-0001', 'PROJ-0004', 'PROJ-0003', 'PROJ-0002'])
  })
  it('counts an open project as the most people', () => {
    expect(names(sortProjects(rows, { key: 'members', order: 'desc' }))[0]).toBe('PROJ-0002')
    expect(names(sortProjects(rows, { key: 'members', order: 'asc' }))[0]).toBe('PROJ-0004')
  })
  it('keeps a blank end date last', () => {
    expect(names(sortProjects(rows, { key: 'end', order: 'asc' })).at(-1)).toBe('PROJ-0002')
  })
  it('labels the members cell', () => {
    expect(membersLabel(rows[1])).toBe('Everyone')
    expect(membersLabel(rows[0])).toBe('3')
    expect(membersLabel({})).toBe('')
  })
})

describe('parseTaskLines', () => {
  it('splits on newlines, trims, drops blanks and repeats', () => {
    expect(parseTaskLines('  Design \r\n\nBuild\n   \ndesign\nShip')).toEqual({
      subjects: ['Design', 'Build', 'Ship'],
      total: 3,
      duplicates: 1,
    })
  })
  it('reads one line as one task and nothing as none', () => {
    expect(parseTaskLines('Kickoff').subjects).toEqual(['Kickoff'])
    expect(parseTaskLines('\n  \n').subjects).toEqual([])
  })
  it('caps at 100 and reports the whole count', () => {
    const text = Array.from({ length: 120 }, (_, index) => `Task ${index + 1}`).join('\n')
    const parsed = parseTaskLines(text)
    expect(parsed.subjects).toHaveLength(100)
    expect(parsed.subjects.at(-1)).toBe('Task 100')
    expect(parsed.total).toBe(120)
  })
})

describe('matchRows', () => {
  it('narrows by text ignoring case, and keeps all for no text', () => {
    const tasks = [{ subject: 'Write spec' }, { subject: 'Review' }]
    expect(matchRows(tasks, 'SPEC', 'subject')).toEqual([{ subject: 'Write spec' }])
    expect(matchRows(tasks, ' ', 'subject')).toHaveLength(2)
  })
})
