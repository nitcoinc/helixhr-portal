import { describe, expect, it } from 'vitest'

import { fromQuery, toQuery, withCompany } from './reportQuery'

const ENTRY = {
  key: 'hours_by_project',
  filters: [
    { name: 'employee', type: 'employee' },
    { name: 'from_date', type: 'date' },
    { name: 'to_date', type: 'date' },
    { name: 'month', type: 'month' },
    { name: 'status', type: 'select', options: ['Active', 'Left'] },
    { name: 'include_pending', type: 'toggle' },
  ],
  group_by: [{ field: 'employee' }, { field: 'project' }, { field: 'task' }],
}

describe('reportQuery', () => {
  it('round-trips filters, dates, grouping arrays, sort and hidden columns', () => {
    const state = {
      filters: { employee: 'HR-EMP-0001', from_date: '2026-01-01', to_date: '2026-01-31', include_pending: 1 },
      groupBy: ['project', 'employee'],
      sort: { field: 'hours', order: 'desc' },
      hidden: ['task_subject', 'billing_hours'],
    }
    const query = toQuery(state, ENTRY)
    expect(query).toEqual({
      employee: 'HR-EMP-0001',
      from_date: '2026-01-01',
      to_date: '2026-01-31',
      include_pending: '1',
      group: 'project,employee',
      sort: '-hours',
      hide: 'task_subject,billing_hours',
    })
    expect(fromQuery(query, ENTRY)).toEqual({ ...state, hasState: true })
  })

  it('drops unknown keys and malformed values', () => {
    const parsed = fromQuery(
      {
        run_as: 'Administrator',
        company: 'Other Co',
        from_date: '01/02/2026',
        month: '2026-13',
        status: 'Suspended',
        include_pending: 'yes',
        group: 'employee,salary,project,task',
        sort: 'hours;drop',
        hide: 'a,b c',
      },
      ENTRY,
    )
    expect(parsed).toEqual({
      filters: {},
      groupBy: ['employee', 'project'],
      sort: null,
      hidden: ['a'],
      hasState: true,
    })
  })

  it('tells "never chose columns" (defaults) from "show every column"', () => {
    expect(fromQuery({}, ENTRY).hidden).toBe(null)
    expect(toQuery({ hidden: null }, ENTRY)).toEqual({})
    const all = toQuery({ hidden: [] }, ENTRY)
    expect(all).toEqual({ hide: '' })
    expect(fromQuery(all, ENTRY).hidden).toEqual([])
  })

  it('a bare link carries no state', () => {
    expect(fromQuery({}, ENTRY).hasState).toBe(false)
  })

  it('keeps the first of a repeated key', () => {
    expect(fromQuery({ employee: ['A', 'B'] }, ENTRY).filters).toEqual({ employee: 'A' })
  })

  describe('company (plan 2026-10-14-001 U10)', () => {
    const MULTI = {
      ...ENTRY,
      filters: [
        ...ENTRY.filters,
        { name: 'project', type: 'project' },
        { name: 'task', type: 'task' },
        { name: 'department', type: 'department' },
        { name: 'branch', type: 'select_link' },
      ],
      company: 'Alpha Ltd',
      companies: [
        { company: 'Alpha Ltd', can_export: true },
        { company: 'Beta Ltd', can_export: false },
      ],
    }

    it('keeps a company the entry offers and round-trips it', () => {
      const parsed = fromQuery({ company: 'Beta Ltd' }, MULTI)
      expect(parsed.filters).toEqual({ company: 'Beta Ltd' })
      expect(parsed.hasState).toBe(true)
      expect(toQuery({ filters: parsed.filters }, MULTI)).toEqual({ company: 'Beta Ltd' })
    })

    it('drops an unknown company from the query string', () => {
      expect(fromQuery({ company: 'Gamma Ltd' }, MULTI).filters).toEqual({})
      expect(toQuery({ filters: { company: 'Gamma Ltd' } }, MULTI)).toEqual({})
      // An entry with no companies list never carries one.
      expect(fromQuery({ company: 'Alpha Ltd' }, ENTRY).filters).toEqual({})
    })

    it('switching company drops employee, project, task and department values', () => {
      const filters = {
        company: 'Alpha Ltd',
        employee: 'HR-EMP-0001',
        project: 'PROJ-0001',
        task: 'TASK-0001',
        department: 'Ops - A',
        branch: 'Head Office',
        status: 'Active',
        from_date: '2026-01-01',
      }
      expect(withCompany(filters, MULTI, 'Beta Ltd')).toEqual({
        company: 'Beta Ltd',
        branch: 'Head Office',
        status: 'Active',
        from_date: '2026-01-01',
      })
      expect(filters.employee).toBe('HR-EMP-0001')
    })

    it('re-picking the same company keeps every filter', () => {
      const filters = { company: 'Alpha Ltd', employee: 'HR-EMP-0001' }
      expect(withCompany(filters, MULTI, 'Alpha Ltd')).toBe(filters)
    })
  })
})
