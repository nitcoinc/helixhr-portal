import { describe, expect, it } from 'vitest'
import { copyWeekNote, copyWeekRows } from './copyWeek'

// Last week is Mon 12 - Sun 18 October 2026; the week being filled is
// Mon 19 - Sun 25.
const SOURCE = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16']
const TARGET = '2026-10-19'
const PROJECTS = [
  { name: 'PROJ-1', project_name: 'Apollo', tasks: [{ name: 'TASK-1', subject: 'Build' }] },
]
const WEEKEND = { '2026-10-24': true, '2026-10-25': true }
const SOURCE_WEEKEND = { '2026-10-17': true, '2026-10-18': true }

function weekOf(project = 'PROJ-1', task = 'TASK-1', extra = {}) {
  return SOURCE.map((date) => ({ date, project, task, hours: 8, note: 'n', ...extra }))
}

function copy(overrides = {}) {
  return copyWeekRows({
    rows: weekOf(),
    projects: PROJECTS,
    targetMonday: TARGET,
    targetNonWorking: WEEKEND,
    sourceNonWorking: SOURCE_WEEKEND,
    ...overrides,
  })
}

const dates = (result) => result.rows.map((row) => row.date)

describe('copyWeekRows', () => {
  it('re-dates every bookable row by seven days and skips nothing', () => {
    const result = copy()
    expect(dates(result)).toEqual([
      '2026-10-19',
      '2026-10-20',
      '2026-10-21',
      '2026-10-22',
      '2026-10-23',
    ])
    expect(result.rows[0]).toMatchObject({ project: 'PROJ-1', task: 'TASK-1', hours: 8, note: 'n' })
    expect(result.skipped).toEqual({ projects: [], tasks: [], days: [], emptyDays: [] })
    expect(copyWeekNote(result.skipped)).toBe('')
  })

  it('puts no hours on a public holiday Wednesday this week, and names it', () => {
    const result = copy({ targetNonWorking: { ...WEEKEND, '2026-10-21': true } })
    expect(dates(result)).not.toContain('2026-10-21')
    expect(result.rows).toHaveLength(4)
    expect(result.skipped.days).toEqual(['Wed'])
    expect(copyWeekNote(result.skipped)).toBe('Not copied: Wed (not a working day this week).')
  })

  it('puts no hours on approved leave Thursday and Friday', () => {
    const result = copy({
      targetNonWorking: { ...WEEKEND, '2026-10-22': true, '2026-10-23': true },
    })
    expect(dates(result)).toEqual(['2026-10-19', '2026-10-20', '2026-10-21'])
    expect(copyWeekNote(result.skipped)).toBe('Not copied: Thu and Fri (not working days this week).')
  })

  it('puts no hours before an employee who joined on Wednesday started', () => {
    const result = copy({
      targetNonWorking: { ...WEEKEND, '2026-10-19': true, '2026-10-20': true },
    })
    expect(dates(result)).toEqual(['2026-10-21', '2026-10-22', '2026-10-23'])
    expect(result.skipped.days).toEqual(['Mon', 'Tue'])
  })

  it('skips a closed task on an open project and names the task', () => {
    const rows = [
      ...weekOf().slice(0, 2),
      { date: '2026-10-14', project: 'PROJ-1', task: 'TASK-9', task_subject: 'Design review', hours: 4 },
    ]
    const result = copy({ rows })
    expect(dates(result)).toEqual(['2026-10-19', '2026-10-20'])
    expect(result.skipped.tasks).toEqual(['Design review'])
    expect(result.skipped.projects).toEqual([])
    expect(copyWeekNote(result.skipped)).toBe(
      'Not copied: task Design review (no longer open for time).',
    )
  })

  it('skips a project no longer open to the employee, named once however many rows it had', () => {
    const rows = [...weekOf(), ...weekOf('PROJ-OLD', '', { project_name: 'Hermes' })]
    const result = copy({ rows })
    expect(result.rows).toHaveLength(5)
    expect(result.rows.every((row) => row.project === 'PROJ-1')).toBe(true)
    expect(result.skipped.projects).toEqual(['Hermes'])
  })

  it('says a working Wednesday stays empty because last Wednesday was a holiday', () => {
    const rows = weekOf().filter((row) => row.date !== '2026-10-14')
    const result = copy({ rows, sourceNonWorking: { ...SOURCE_WEEKEND, '2026-10-14': true } })
    expect(dates(result)).not.toContain('2026-10-21')
    expect(result.skipped.emptyDays).toEqual(['Wed'])
    expect(copyWeekNote(result.skipped)).toBe('Wed had no hours last week.')
  })

  it('copies hours worked on last week\'s holiday onto a working day this week', () => {
    const result = copy({ sourceNonWorking: { ...SOURCE_WEEKEND, '2026-10-14': true } })
    expect(dates(result)).toContain('2026-10-21')
    expect(result.skipped.emptyDays).toEqual([])
  })

  it('does not report an empty day that is also off this week', () => {
    const rows = weekOf().filter((row) => row.date !== '2026-10-14')
    const result = copy({
      rows,
      sourceNonWorking: { ...SOURCE_WEEKEND, '2026-10-14': true },
      targetNonWorking: { ...WEEKEND, '2026-10-21': true },
    })
    expect(result.skipped.emptyDays).toEqual([])
    expect(result.skipped.days).toEqual([])
  })

  it('skips nothing for days when the server sent no non_working marker', () => {
    const result = copyWeekRows({ rows: weekOf(), projects: PROJECTS, targetMonday: TARGET })
    expect(result.rows).toHaveLength(5)
    expect(result.skipped.days).toEqual([])
  })

  it('ignores a row dated outside the copied week and leaves a project-less row to the page', () => {
    const rows = [
      { date: '2026-10-05', project: 'PROJ-1', task: '', hours: 2 },
      { date: '2026-10-12', project: '', task: '', hours: 2 },
    ]
    const result = copy({ rows })
    expect(result.rows).toEqual([{ date: '2026-10-19', project: '', task: '', hours: 2 }])
  })

  it('combines every reason into one line', () => {
    expect(
      copyWeekNote({
        projects: ['Hermes'],
        tasks: ['Design review'],
        days: ['Wed', 'Thu', 'Fri'],
        emptyDays: ['Mon'],
      }),
    ).toBe(
      'Not copied: Hermes and task Design review (no longer open for time); ' +
        'Wed, Thu and Fri (not working days this week). Mon had no hours last week.',
    )
  })
})
