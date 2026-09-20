import { test, expect } from '@playwright/test'

// P7-U9 / P7-R12, P7-R13, P7-R15. Reports render inside the portal now --
// this used to be a launcher that opened Frappe's own report view in a new
// tab (P6-U6). `run_portal_report` and `get_billable_hours` (P7-U8) are the
// server's own gates; this spec drives the in-portal table they render.
//
// "Leave Ledger" is the curated report under test below because it is the
// one whose fixture data is deterministic without touching any backend
// fixture: `setup_playwright_fixtures` always books a submitted 5-day
// Casual Leave allocation for the main fixture employee
// (`ensure_leave_allocation`), which HRMS records as a docstatus-1 Leave
// Ledger Entry spanning this calendar year into the next. A wide from/to
// date window (2000-01-01..2100-01-01) picks that row up regardless of
// which day the suite runs on.
const COLLEAGUE_NAME = 'Manager'
const WIDE_FROM = '2000-01-01'
const WIDE_TO = '2100-01-01'

async function getManagerEmployeeId(page) {
  await page.goto('/helixhr/people')
  await page.getByLabel('Search').fill(COLLEAGUE_NAME)
  await page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
  await expect(page).toHaveURL(/\/helixhr\/people\/[^/]+$/)
  return decodeURIComponent(page.url().split('/people/')[1])
}

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'reports is an HR-only screen (billable hours is separately gated on project scope)')
  })

  test('an HR identity sees the curated list and the new billable-hours entry', async ({ page }) => {
    await page.goto('/helixhr/')
    await expect(page.getByRole('link', { name: 'Reports' })).toBeVisible()

    await page.goto('/helixhr/reports')
    await expect(page.getByRole('heading', { name: 'Reports' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Leave balance' }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'Billable hours' })).toBeVisible()
  })

  test('no payroll report appears in the list', async ({ page }) => {
    await page.goto('/helixhr/reports')
    await expect(page.getByText(/Salary/i)).toHaveCount(0)
    await expect(page.getByText(/Payroll/i)).toHaveCount(0)
  })

  test('running a curated report shows a table inside the portal, right-aligned numeric columns included', async ({
    page,
  }) => {
    await page.goto('/helixhr/reports')
    await page.getByRole('button', { name: 'Leave ledger' }).click()

    // Still on the portal -- no new tab, no Desk navigation.
    await expect(page).toHaveURL('/helixhr/reports')
    await expect(page.getByRole('heading', { name: 'Leave Ledger', exact: true })).toBeVisible()

    await page.getByLabel('From').fill(WIDE_FROM)
    await page.getByLabel('To').fill(WIDE_TO)

    const table = page.locator('table')
    await expect(table).toBeVisible()
    await expect(table.getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    // The allocation was booked for 5 days (ensure_leave_allocation) -- the
    // "Leaves" column is Float, so it renders right-aligned with tabular
    // figures (P7-U9's column-type rule).
    const leavesHeader = table.getByRole('columnheader', { name: 'Leaves' })
    await expect(leavesHeader).toHaveClass(/text-right/)
    const leavesCell = table.getByRole('cell', { name: '5' }).first()
    await expect(leavesCell).toBeVisible()
    await expect(leavesCell).toHaveClass(/tabular/)
    await expect(leavesCell).toHaveClass(/text-right/)

    // No monetary column or value anywhere in the rendered table (R8's
    // posture, checked here as a literal DOM assertion rather than trusted
    // from the backend alone).
    await expect(table.getByText(/\$|currency|amount|rate/i)).toHaveCount(0)
  })

  test('changing the employee filter re-runs the report, and a refusal reads differently from an empty result', async ({
    page,
  }) => {
    const managerEmployeeId = await getManagerEmployeeId(page)

    await page.goto('/helixhr/reports')
    await page.getByRole('button', { name: 'Leave ledger' }).click()
    await page.getByLabel('From').fill(WIDE_FROM)
    await page.getByLabel('To').fill(WIDE_TO)
    await expect(page.locator('table').getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    // A real employee, in scope, but with no Leave Ledger Entry of their
    // own: the filter narrows the same query to nothing, and the empty
    // state names the task rather than looking like an outage or a refusal.
    await page.getByLabel('Employee').fill(managerEmployeeId)
    await expect(page.getByText('No matching rows')).toBeVisible()
    await expect(page.getByText("You don't have access to this")).toHaveCount(0)
    await expect(page.locator('table')).toHaveCount(0)

    // An employee id that does not exist at all: `employee_in_admin_scope`
    // refuses it server-side (P6-R9's rule, applied to `run_portal_report`
    // too) -- a different message from "no matching rows", not an empty
    // table implying no data (P7-U9's own test scenario).
    await page.getByLabel('Employee').fill('NO-SUCH-EMPLOYEE-000')
    await expect(page.getByText("You don't have access to this")).toBeVisible()
    await expect(page.getByText('No matching rows')).toHaveCount(0)
  })

  test('the billable-hours entry runs its own scoped query inside the portal', async ({ page }) => {
    await page.goto('/helixhr/reports')
    await page.getByRole('button', { name: 'Billable hours' }).click()

    await expect(page).toHaveURL('/helixhr/reports')
    await expect(page.getByRole('heading', { name: 'Billable hours', exact: true })).toBeVisible()
    await expect(page.getByLabel('Employee')).toBeVisible()
    await expect(page.getByLabel('Project')).toBeVisible()
    await expect(page.getByLabel('Task')).toBeVisible()
    await expect(page.getByLabel('From')).toBeVisible()
    await expect(page.getByLabel('To')).toBeVisible()

    // Whatever this company's fixture data happens to hold, the region
    // settles into a real answer -- never stuck loading, never the
    // permission refusal (an HR Manager always resolves to a "company"
    // scope, never "none").
    await expect(page.getByText("You don't have access to this")).toHaveCount(0)
    await expect(page.locator('[data-async-state^="report-results:"]')).not.toHaveAttribute(
      'data-async-state',
      'report-results:pending',
      { timeout: 10000 },
    )

    // Never a Frappe Report (KTD3a) -- no Desk export exists for it.
    await expect(page.getByRole('button', { name: 'Open in Frappe' })).toHaveCount(0)
  })

  test('the Desk link is a secondary affordance, present for an HR Manager on a curated report', async ({
    page,
    context,
  }) => {
    await page.goto('/helixhr/reports')
    await page.getByRole('button', { name: 'Leave ledger' }).click()

    const deskLink = page.getByRole('button', { name: 'Open in Frappe' })
    await expect(deskLink).toBeVisible()

    const [popup] = await Promise.all([context.waitForEvent('page'), deskLink.click()])
    await popup.waitForLoadState('domcontentloaded')
    expect(popup.url()).toContain('/desk/query-report/Leave%20Ledger')
    await popup.close()
  })

  test('the report table scrolls inside itself at phone width, not the page', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 700 })
    await page.goto('/helixhr/reports')
    await page.getByRole('button', { name: 'Leave ledger' }).click()
    await page.getByLabel('From').fill(WIDE_FROM)
    await page.getByLabel('To').fill(WIDE_TO)
    await expect(page.locator('table').getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    const pageScrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
    const viewportWidth = await page.evaluate(() => document.documentElement.clientWidth)
    expect(pageScrollWidth).toBeLessThanOrEqual(viewportWidth + 1)

    const scroller = page.locator('table').locator('..')
    const overflows = await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)
    expect(overflows).toBe(true)
  })
})

test('an employee identity has no Reports nav item, and a direct hit names the reason', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Reports' })).toHaveCount(0)

  await page.goto('/helixhr/reports')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByRole('button', { name: /Leave balance/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Billable hours' })).toHaveCount(0)
})

test('an IT Team identity is refused the same way, with no Desk-bound link visible', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the one negative assertion this project exists for')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Reports' })).toHaveCount(0)

  await page.goto('/helixhr/reports')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByRole('button', { name: /Leave balance/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open in Frappe' })).toHaveCount(0)
})

// GAP (flagged, not built here -- out of scope for this unit): there is no
// seeded Playwright identity for the HelixHR Delivery Manager's "assigned"
// project scope (only a Python integration fixture,
// `make_test_delivery_manager_employee` -- see playwright.config.ts's own
// note on projects.spec.ts). That branch would prove the Desk link is
// ABSENT for a Delivery Manager even though they can run the billable-hours
// report; it is covered here only indirectly, by the employee/IT identities
// above having no admin or project scope at all. Wiring a fifth identity
// through auth.setup.ts and playwright.config.ts is a deliberate exclusion,
// the same one U5's agent flagged for projects.spec.ts.
