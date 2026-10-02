import { describe, expect, it } from 'vitest'
import { NAV_GROUPS, groupNav, readCollapsed, writeCollapsed } from './navGroups'

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = String(value)
    },
    data,
  }
}

describe('groupNav', () => {
  const items = [
    { label: 'Home', group: 'pinned' },
    { label: 'Leave', group: 'work' },
    { label: 'People', group: 'hr' },
    { label: 'Timesheet', group: 'work' },
    { label: 'Profile', group: 'bottom' },
  ]

  it('orders groups by NAV_GROUPS and keeps item order inside a group', () => {
    const groups = groupNav(items)
    expect(groups.map((g) => g.id)).toEqual(['pinned', 'work', 'hr', 'bottom'])
    expect(groups[1].items.map((i) => i.label)).toEqual(['Leave', 'Timesheet'])
  })

  it('drops a group whose items were all gated away', () => {
    const employee = items.filter((item) => item.group !== 'hr')
    expect(groupNav(employee).some((g) => g.id === 'hr' || g.id === 'admin')).toBe(false)
  })

  it('marks only HR and Admin collapsible', () => {
    expect(NAV_GROUPS.filter((g) => g.collapsible).map((g) => g.id)).toEqual(['hr', 'admin'])
  })
})

describe('collapsed state storage', () => {
  it('round-trips the collapsed ids', () => {
    const storage = memoryStorage()
    writeCollapsed(['hr'], storage)
    expect(readCollapsed(storage)).toEqual(['hr'])
  })

  it('remembers an explicit "nothing collapsed"', () => {
    const storage = memoryStorage()
    writeCollapsed([], storage)
    expect(readCollapsed(storage)).toEqual([])
  })

  it('defaults to every collapsible group collapsed when nothing usable is stored', () => {
    expect(readCollapsed(memoryStorage())).toEqual(['hr', 'admin'])
    expect(readCollapsed(memoryStorage({ 'helixhr.nav.collapsed': '{nope' }))).toEqual(['hr', 'admin'])
    expect(readCollapsed(memoryStorage({ 'helixhr.nav.collapsed': '{"a":1}' }))).toEqual(['hr', 'admin'])
    const throwing = {
      getItem() {
        throw new Error('denied')
      },
      setItem() {
        throw new Error('denied')
      },
    }
    expect(readCollapsed(throwing)).toEqual(['hr', 'admin'])
    expect(() => writeCollapsed(['hr'], throwing)).not.toThrow()
  })
})
