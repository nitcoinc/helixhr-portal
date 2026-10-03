import { describe, expect, it } from 'vitest'

import { fromQuery, toQuery } from './reportQuery'

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

  it('a bare link carries no state', () => {
    expect(fromQuery({}, ENTRY).hasState).toBe(false)
  })

  it('keeps the first of a repeated key', () => {
    expect(fromQuery({ employee: ['A', 'B'] }, ENTRY).filters).toEqual({ employee: 'A' })
  })
})
