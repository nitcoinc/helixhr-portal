// Plan 2026-10-15-002 U5: the Approvals page's lane helpers.
import { describe, expect, it } from 'vitest'
import { groupByPerson, laneSections, laneSubsections } from './approvalLanes'

const row = (lane, name, extra = {}) => ({ lane, name, employee: `E-${name}`, kind: 'leave', ...extra })

describe('laneSections', () => {
  it('orders team, decided, hr with no it lane', () => {
    const lanes = laneSections([row('it', 'c'), row('hr', 'b'), row('team', 'a')], 2)
    expect(lanes.map((lane) => lane.key)).toEqual(['team', 'decided', 'hr'])
    expect(lanes.map((lane) => lane.title)).toEqual(['My team', 'Decided by me', 'HR'])
  })

  it('drops empty pending lanes', () => {
    expect(laneSections([row('team', 'a')], 0).map((lane) => lane.key)).toEqual(['team'])
    expect(laneSections([], 0)).toEqual([])
  })

  it('keeps Decided, first, when My team is empty', () => {
    expect(laneSections([row('hr', 'b')], 1).map((lane) => lane.key)).toEqual(['decided', 'hr'])
    expect(laneSections([], 3).map((lane) => lane.key)).toEqual(['decided'])
  })

  it('treats an untagged row as the caller\'s team', () => {
    expect(laneSections([{ name: 'x', kind: 'leave' }])[0].key).toBe('team')
  })

  it('keeps change rows first within a lane, then flagged, then normal', () => {
    const [lane] = laneSections([
      row('team', 'n'),
      row('team', 'f', { needs_look: true }),
      row('team', 'c', { kind: 'change' }),
    ])
    expect(lane.sections.map((section) => section.key)).toEqual(['change', 'flagged', 'normal'])
    expect(lane.sections[0].rows.map((r) => r.name)).toEqual(['c'])
  })
})

describe('laneSubsections', () => {
  it('drops empty sub-sections', () => {
    expect(laneSubsections([row('team', 'n')]).map((section) => section.key)).toEqual(['normal'])
  })
})

describe('groupByPerson', () => {
  it('groups by employee in first-seen order, oldest row leading', () => {
    const rows = [
      { employee: 'E-1', employee_name: 'Alice', name: 'a1' },
      { employee: 'E-2', employee_name: 'Bob', name: 'b1' },
      { employee: 'E-1', employee_name: 'Alice', name: 'a2' },
    ]
    const groups = groupByPerson(rows)
    expect(groups.map((group) => group.employee)).toEqual(['E-1', 'E-2'])
    expect(groups[0].rows.map((r) => r.name)).toEqual(['a1', 'a2'])
    expect(groups[0].oldest.name).toBe('a1')
  })
})
