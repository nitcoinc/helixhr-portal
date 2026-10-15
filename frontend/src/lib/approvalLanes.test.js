// Plan 2026-10-15-002 U5: unit tests for approval lane helpers.
import { describe, expect, it } from 'vitest'
import { laneSections, groupByPerson, groupByApprover } from './approvalLanes'

describe('laneSections', () => {
  it('orders lanes: team, decided, hr, it', () => {
    const rows = [
      { lane: 'team', employee_name: 'Alice', kind: 'leave' },
      { lane: 'hr', employee_name: 'Bob', kind: 'leave' },
      { lane: 'it', employee_name: 'Carol', kind: 'request' },
    ]
    const sections = laneSections(rows, [])
    expect(sections).toHaveLength(3)
    expect(sections[0].key).toBe('team')
    expect(sections[1].key).toBe('hr')
    expect(sections[2].key).toBe('it')
  })

  it('drops empty pending lanes', () => {
    const rows = [{ lane: 'team', employee_name: 'Alice', kind: 'leave' }]
    const sections = laneSections(rows, [])
    expect(sections).toHaveLength(1)
    expect(sections[0].key).toBe('team')
  })

  it('includes decided whenever it has items', () => {
    const rows = [{ lane: 'team', employee_name: 'Alice', kind: 'leave' }]
    const decided = [{ kind: 'leave', name: 'L-001' }]
    const sections = laneSections(rows, decided)
    expect(sections).toHaveLength(2)
    expect(sections[0].key).toBe('team')
    expect(sections[1].key).toBe('decided')
    expect(sections[1].collapsed).toBe(true)
  })

  it('shows decided first when team is empty', () => {
    const rows = [{ lane: 'hr', employee_name: 'Bob', kind: 'leave' }]
    const decided = [{ kind: 'leave', name: 'L-001' }]
    const sections = laneSections(rows, decided)
    expect(sections[0].key).toBe('decided')
    expect(sections[1].key).toBe('hr')
  })
})

describe('groupByPerson', () => {
  it('groups rows by employee', () => {
    const rows = [
      { employee: 'E-001', employee_name: 'Alice', kind: 'leave' },
      { employee: 'E-001', employee_name: 'Alice', kind: 'timesheet' },
      { employee: 'E-002', employee_name: 'Bob', kind: 'leave' },
    ]
    const groups = groupByPerson(rows)
    expect(groups).toHaveLength(2)
    const alice = groups.find((g) => g.employee_name === 'Alice')
    expect(alice.count).toBe(2)
  })
})

describe('groupByApprover', () => {
  it('groups by approver_user, sorts by oldest_days desc', () => {
    const rows = [
      { approver_user: 'user-a', approver_name: 'Alice', age_days: 5 },
      { approver_user: 'user-b', approver_name: 'Bob', age_days: 10 },
    ]
    const groups = groupByApprover(rows)
    expect(groups).toHaveLength(2)
    expect(groups[0].approver_name).toBe('Bob') // 10 days first
  })
})