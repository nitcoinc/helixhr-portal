import { test, expect } from '@playwright/test'

// Plan 2026-10-17-001 U1 (R22). Every identity opens every route its own
// bootstrap lets it reach, and no route may log a console error or throw.
// `tests/audit-shots.mjs` (U18) logs the same thing for human review; this is
// the asserting half.
//
// Read-only and per-identity: each project signs in as its own hat and visits
// only what the rail would offer that hat, so it is safe in every project's
// `testMatch` without running somebody else's flow.

type Bootstrap = Record<string, unknown>

// [path, gate(bootstrap)]. The gates mirror the rail's filter in
// AppShell.vue's `navItems` and the list in tests/audit-shots.mjs: a route
// the rail would not offer this identity is not visited.
const linked = (b: Bootstrap) => Boolean(b.employee)
const ROUTES: [string, (b: Bootstrap) => boolean][] = [
  ['/', () => true],
  ['/notifications', linked],
  ['/leave', linked],
  ['/timesheet', linked],
  ['/timesheet/history', linked],
  ['/my-projects', linked],
  ['/attendance', linked],
  ['/requests', linked],
  ['/requests?tab=work', (b) => Boolean(b.can_handle_requests || b.can_work_requests)],
  ['/payslips', linked],
  ['/holidays', linked],
  ['/documents', linked],
  ['/directory', linked],
  ['/profile', linked],
  ['/team', (b) => Boolean(b.has_reports)],
  ['/roster', (b) => linked(b) || Boolean(b.can_roster_everyone)],
  ['/approvals', (b) => Boolean(b.can_approve || b.can_oversee_timesheets)],
  ['/people', (b) => Boolean(b.can_see_people)],
  ['/reports', (b) => Boolean(b.can_run_reports || b.can_admin_portal)],
  ['/organisation', (b) => Boolean(b.can_see_organisation)],
  ['/projects', (b) => Boolean(b.can_see_projects)],
  ['/settings', (b) => Boolean(b.can_configure || b.can_admin_portal)],
  ['/email-templates', (b) => Boolean(b.can_edit_email_templates)],
]

test('every route this identity can reach loads without a console error', async ({ page }) => {
  // One browser pass per route across the whole rail.
  test.setTimeout(180_000)

  const response = await page.request.get('/api/method/helixhr.api.get_portal_bootstrap')
  expect(response.ok(), `bootstrap: ${response.status()}`).toBe(true)
  const bootstrap: Bootstrap = (await response.json()).message || {}
  const paths = ROUTES.filter(([, gate]) => gate(bootstrap)).map(([path]) => path)
  expect(paths.length).toBeGreaterThan(0)

  let current = ''
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`${current}: ${message.text().split('\n')[0]}`)
  })
  page.on('pageerror', (error) => errors.push(`${current}: ${error.message.split('\n')[0]}`))

  for (const path of paths) {
    current = path
    await page.goto(`/helixhr${path}`)
    await page.waitForLoadState('networkidle')
  }

  expect(errors, `visited ${paths.join(', ')}\n${errors.join('\n')}`).toEqual([])
})
