// Plan 2026-10-17-001 U9 / KTD9. "Copy last week" already shipped; this is
// the part that decides *what* it copies, kept pure so a unit test can hold
// every case without a browser or a bench.
//
// A copy used to re-date every row of last week by seven days, so it put
// hours on this week's public holiday, on approved leave, on days before the
// employee joined, and on a project or task that had since closed -- hours
// the manager's queue then flagged, or the server refused on save.

import { addCalendarDays, weekDates } from '@/lib/dates'
import { WEEKDAYS } from '@/lib/week'

/**
 * Last week's rows, re-dated into the target week, minus what can no longer
 * be booked there.
 *
 * @param {object} args
 * @param {Array<{date: string, project: string, task?: string, hours: number,
 *   note?: string, project_name?: string, task_subject?: string}>} args.rows
 *   the previous week's `get_my_week().timesheet.rows`.
 * @param {Array<{name: string, tasks?: Array<{name: string}>}>} args.projects
 *   `get_my_projects()`: open projects with their open tasks.
 * @param {string} args.targetMonday the Monday of the week being filled.
 * @param {Object<string, boolean>} [args.targetNonWorking] the target week's
 *   `get_my_week().non_working`; a missing map (an older server) skips nothing.
 * @param {Object<string, boolean>} [args.sourceNonWorking] the same, for the
 *   week being copied.
 * @returns {{rows: Array, skipped: {projects: string[], tasks: string[],
 *   days: string[], emptyDays: string[]}}} `rows` in the server's row shape;
 *   `skipped` by display name (projects, tasks) and weekday (days).
 *   `emptyDays` are target working days nothing landed on because the same
 *   day last week was not a working day.
 */
export function copyWeekRows({
  rows,
  projects,
  targetMonday,
  targetNonWorking = {},
  sourceNonWorking = {},
}) {
  const open = new Map((projects || []).map((p) => [p.name, new Set((p.tasks || []).map((t) => t.name))]))
  const dates = weekDates(targetMonday)
  const weekdayOf = (iso) => WEEKDAYS[dates.indexOf(iso)]
  const skippedProjects = new Set()
  const skippedTasks = new Set()
  const skippedDays = new Set()
  const kept = []

  for (const row of rows || []) {
    // Re-dated by seven calendar days, not by an offset in hours: a week
    // that crosses a daylight-saving change is still seven days long.
    const date = addCalendarDays(row.date, 7)
    if (!dates.includes(date)) continue
    // A row with no project at all is left for the page's own "Every row
    // needs a project." -- there is nothing to name it by here.
    if (row.project && !open.has(row.project)) {
      skippedProjects.add(row.project_name || row.project)
    } else if (row.project && row.task && !open.get(row.project).has(row.task)) {
      skippedTasks.add(row.task_subject || row.task)
    } else if (targetNonWorking[date]) {
      skippedDays.add(date)
    } else {
      kept.push({ ...row, date })
    }
  }

  const filled = new Set(kept.map((row) => row.date))
  const emptyDays = dates.filter(
    (date) =>
      !targetNonWorking[date] && sourceNonWorking[addCalendarDays(date, -7)] && !filled.has(date),
  )

  return {
    rows: kept,
    skipped: {
      projects: [...skippedProjects],
      tasks: [...skippedTasks],
      days: dates.filter((date) => skippedDays.has(date)).map(weekdayOf),
      emptyDays: emptyDays.map(weekdayOf),
    },
  }
}

/** "a", "a and b", "a, b and c". */
function listOf(items) {
  if (items.length < 2) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** The one-line note under the copy, or `''` when nothing was skipped. */
export function copyWeekNote({ projects = [], tasks = [], days = [], emptyDays = [] } = {}) {
  const notCopied = []
  const closed = [...projects, ...tasks.map((task) => `task ${task}`)]
  if (closed.length) notCopied.push(`${listOf(closed)} (no longer open for time)`)
  if (days.length) {
    notCopied.push(`${listOf(days)} (${days.length === 1 ? 'not a working day' : 'not working days'} this week)`)
  }
  const sentences = []
  if (notCopied.length) sentences.push(`Not copied: ${notCopied.join('; ')}.`)
  if (emptyDays.length) sentences.push(`${listOf(emptyDays)} had no hours last week.`)
  return sentences.join(' ')
}
