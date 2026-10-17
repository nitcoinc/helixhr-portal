import { describe, expect, it } from 'vitest'
import {
  employeeNumberAddsNothing,
  fieldOptions,
  filterByField,
  offFirst,
  sortRows,
  splitByActivity,
} from './lists'

describe('sortRows', () => {
  const rows = [
    { name: 'b', label: 'Project 10', joined: '2024-03-01' },
    { name: 'a', label: 'project 2', joined: null },
    { name: 'c', label: 'Alpha', joined: '2019-07-15' },
  ]

  it('sorts text case-insensitively with numbers in order', () => {
    expect(sortRows(rows, 'label').map((row) => row.name)).toEqual(['c', 'a', 'b'])
    expect(sortRows(rows, 'label', 'desc').map((row) => row.name)).toEqual(['b', 'a', 'c'])
  })
  it('sorts ISO dates by calendar and keeps blanks last both ways', () => {
    expect(sortRows(rows, 'joined').map((row) => row.name)).toEqual(['c', 'b', 'a'])
    expect(sortRows(rows, 'joined', 'desc').map((row) => row.name)).toEqual(['b', 'c', 'a'])
  })
  it('sorts numbers as numbers', () => {
    const counts = [{ n: 9 }, { n: 10 }, { n: 1 }]
    expect(sortRows(counts, 'n', 'desc').map((row) => row.n)).toEqual([10, 9, 1])
  })
  it('never mutates its input', () => {
    const copy = [...rows]
    sortRows(rows, 'label')
    expect(rows).toEqual(copy)
  })
})

describe('fieldOptions and filterByField', () => {
  const rows = [
    { department: 'Sales' },
    { department: 'Engineering' },
    { department: 'Sales' },
    { department: null },
  ]

  it('offers All, then each value A-Z with its count, then the blanks', () => {
    expect(fieldOptions(rows, 'department', { blankLabel: 'No department' })).toEqual([
      { value: null, label: 'All', count: 4 },
      { value: 'Engineering', label: 'Engineering', count: 1 },
      { value: 'Sales', label: 'Sales', count: 2 },
      { value: '', label: 'No department', count: 1 },
    ])
  })
  it('gives blanks no chip without a label for them', () => {
    expect(fieldOptions(rows, 'department').map((option) => option.value)).toEqual([
      null,
      'Engineering',
      'Sales',
    ])
  })
  it('narrows to one value, to the blanks, or not at all', () => {
    expect(filterByField(rows, 'department', 'Sales')).toHaveLength(2)
    expect(filterByField(rows, 'department', '')).toEqual([{ department: null }])
    expect(filterByField(rows, 'department', null)).toHaveLength(4)
  })
})

describe('employeeNumberAddsNothing', () => {
  it('drops a number that is only the name in lower-kebab case', () => {
    expect(employeeNumberAddsNothing({ employee_name: 'Bulk-leave-2302', employee_number: 'bulk-leave-2302' })).toBe(true)
    expect(employeeNumberAddsNothing({ employee_name: 'Zara Colleague', employee_number: 'zara-colleague' })).toBe(true)
  })
  it('keeps a real number, and treats a missing one as nothing to show', () => {
    expect(employeeNumberAddsNothing({ employee_name: 'Zara Colleague', employee_number: 'EMP-0042' })).toBe(false)
    expect(employeeNumberAddsNothing({ employee_name: 'Zara Colleague', employee_number: '' })).toBe(true)
  })
})

describe('offFirst', () => {
  it('puts anyone with leave first, keeping the order inside each half', () => {
    const rows = [
      { name: 'a', leaves: [] },
      { name: 'b', leaves: [{ waiting: true }] },
      { name: 'c', leaves: [] },
      { name: 'd', leaves: [{ waiting: false }] },
    ]
    expect(offFirst(rows).map((row) => row.name)).toEqual(['b', 'd', 'a', 'c'])
  })
})

describe('splitByActivity', () => {
  it('puts projects with hours or open tasks first, keeping the server order in each half', () => {
    const rows = [
      { name: 'a', hours_this_month: 6, tasks: [] },
      { name: 'b', hours_this_month: 0, tasks: [] },
      { name: 'c', hours_this_month: 0, tasks: [{ name: 't' }] },
      { name: 'd', hours_this_month: null },
      { name: 'e', hours_this_month: '1.5', tasks: [] },
    ]
    const { active, other } = splitByActivity(rows)
    expect(active.map((row) => row.name)).toEqual(['a', 'c', 'e'])
    expect(other.map((row) => row.name)).toEqual(['b', 'd'])
  })

  it('is two empty halves for nothing at all', () => {
    expect(splitByActivity(undefined)).toEqual({ active: [], other: [] })
  })
})
