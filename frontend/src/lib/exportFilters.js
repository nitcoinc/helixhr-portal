// Plan 2026-10-17-001 U11 (AI-10). The export log's Filters column, in plain
// words. `get_export_log` returns the run's cleaned filters as the server
// stored them -- raw keys, ISO dates, the 2000..2100 window a "run it over
// everything" link carries -- so this turns that into a sentence a Portal
// Admin can read. Pure, so vitest holds it.
import { formatDate, formatDateRange } from '@/lib/dates'
import { formatMonth, isMonth } from '@/lib/month'
import { ALL_COMPANIES } from '@/lib/reportQuery'

// The catalog's own filter labels (`helixhr/reports.py`, `_f(...)`). A key
// missing here still reads as words: underscores become spaces.
const LABELS = {
  company: 'Company',
  employee: 'Employee',
  department: 'Department',
  branch: 'Branch',
  designation: 'Designation',
  employment_type: 'Employment type',
  project: 'Project',
  task: 'Task',
  month: 'Month',
  basis: 'Hours basis',
  project_members_only: 'Project members only',
}
// Shown in place of the key it names, never on its own.
const COMPANIONS = { project_name: 'project' }
// At or past both of these, a date range is "everything", not data.
const OPEN_FROM = '2000-01-01'
const OPEN_TO = '2100-01-01'

function labelOf(key) {
  const label = LABELS[key] || key.replace(/_/g, ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

function valueOf(key, value, filters) {
  if (key === 'project' && filters.project_name) return String(filters.project_name)
  if (key === 'company' && value === ALL_COMPANIES) return 'All my companies'
  if (key === 'month' && isMonth(value)) return formatMonth(value)
  if (typeof value === 'boolean' || (key === 'project_members_only' && (value === 0 || value === 1))) {
    return value ? 'Yes' : 'No'
  }
  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

function period(from, to) {
  if (!from && !to) return ''
  const openFrom = !from || from <= OPEN_FROM
  const openTo = !to || to >= OPEN_TO
  if (openFrom && openTo) return 'All dates'
  if (openFrom) return `Until ${formatDate(to)}`
  if (openTo) return `From ${formatDate(from)}`
  return formatDateRange(from, to)
}

/** "1 Oct – 31 Oct 2026, Company: Acme, Project: Website relaunch; grouped
 * by date" from a stored filter dict and `group_by` ("date, employee"). */
export function summarizeExportFilters(filters, groupBy = '') {
  const values = filters && typeof filters === 'object' ? filters : {}
  const parts = []
  const range = period(values.from_date, values.to_date)
  if (range) parts.push(range)
  for (const [key, value] of Object.entries(values)) {
    if (key === 'from_date' || key === 'to_date' || COMPANIONS[key]) continue
    if (value === '' || value === null || value === undefined) continue
    parts.push(`${labelOf(key)}: ${valueOf(key, value, values)}`)
  }
  const groups = String(groupBy || '')
    .split(',')
    .map((field) => field.trim().replace(/_/g, ' '))
    .filter(Boolean)
  const text = parts.join(', ')
  if (!groups.length) return text
  const grouped = `grouped by ${groups.join(', ')}`
  return text ? `${text}; ${grouped}` : grouped.charAt(0).toUpperCase() + grouped.slice(1)
}
