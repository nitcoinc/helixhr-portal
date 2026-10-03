import { test, expect, request, type APIRequestContext, type Page } from '@playwright/test'

// Plan 2026-10-04-001 U3/U4: the catalog-driven Reports page.
//   /helixhr/reports          catalog from `get_report_catalog`
//   /helixhr/reports/<key>    filter bar, explicit Run, shaped table
//
// "Leave ledger" is the report under test because its fixture data is
// deterministic: `setup_playwright_fixtures` books a submitted 5-day Casual
// Leave allocation for the main fixture employee (`ensure_leave_allocation`),
// which HRMS records as a Leave Ledger Entry spanning this calendar year into
// the next. A wide from/to window picks it up on any day the suite runs.
const COLLEAGUE_NAME = 'Manager'
const WIDE_FROM = '2000-01-01'
const WIDE_TO = '2100-01-01'
const LEDGER_URL = `/helixhr/reports/leave_ledger?from_date=${WIDE_FROM}&to_date=${WIDE_TO}`

async function openHrNav(page) {
  await page.goto('/helixhr/')
  // U15: the HR section starts collapsed on a first visit.
  const hrSection = page.getByRole('button', { name: 'HR', exact: true })
  if ((await hrSection.getAttribute('aria-expanded')) === 'false') await hrSection.click()
}

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'the HR Manager branch; other tiers are U6')
  })

  test('the catalog lists four families and search narrows it', async ({ page }) => {
    await openHrNav(page)
    await expect(page.getByRole('link', { name: 'Reports' })).toBeVisible()

    await page.goto('/helixhr/reports')
    await expect(page.getByRole('heading', { name: 'Reports' })).toBeVisible()
    for (const family of ['Time', 'Attendance', 'Leave', 'People']) {
      await expect(page.getByRole('heading', { name: family, exact: true })).toBeVisible()
    }
    await expect(page.getByText(/Salary|Payroll/i)).toHaveCount(0)

    await page.getByLabel('Find a report').fill('late')
    await expect(page.getByRole('link', { name: /Shift attendance/ })).toBeVisible()
    await expect(page.getByRole('link', { name: /Leave ledger/ })).toHaveCount(0)

    await page.getByLabel('Find a report').fill('zzzz-nothing')
    await expect(page.getByText('No report matches')).toBeVisible()
  })

  test('opening a report waits for Run, then renders a real table', async ({ page }) => {
    await page.goto('/helixhr/reports')
    await page.getByRole('link', { name: /Leave ledger/ }).click()
    await expect(page).toHaveURL(/\/helixhr\/reports\/leave_ledger/)
    await expect(page.getByRole('heading', { name: 'Leave ledger', exact: true })).toBeVisible()
    await expect(page.getByText('Choose filters, then press Run report.')).toBeVisible()
    await expect(page.locator('table')).toHaveCount(0)

    await page.getByLabel('From').fill(WIDE_FROM)
    await page.getByLabel('To').fill(WIDE_TO)
    await page.getByRole('button', { name: 'Run report' }).click()
    await expect(page).toHaveURL(/from_date=2000-01-01/)

    const table = page.locator('table')
    await expect(table.getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()
    await expect(table.locator('caption')).toHaveText('Leave ledger')

    const leavesHeader = table.getByRole('columnheader', { name: 'Leaves' })
    await expect(leavesHeader).toHaveClass(/text-right/)
    const leavesCell = table.locator('tbody').getByRole('cell', { name: '5', exact: true }).first()
    await expect(leavesCell).toHaveClass(/tabular/)
    await expect(leavesCell).toHaveClass(/text-right/)
    await expect(table.locator('tfoot tr[data-kind="total"]')).toBeVisible()

    await expect(table.getByText(/\$|currency|amount|rate/i)).toHaveCount(0)
  })

  test('a link with filters runs on open; later edits mark results stale', async ({ page }) => {
    await page.goto(LEDGER_URL)
    await expect(page.locator('table').getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    await page.getByLabel('From', { exact: true }).fill('2001-01-01')
    await expect(page.getByText(/results are out of date/)).toBeVisible()
    await page.getByRole('button', { name: 'Run report' }).click()
    await expect(page.getByText(/results are out of date/)).toHaveCount(0)
  })

  test('sorting, grouping and hidden columns ride in the URL', async ({ page }) => {
    await page.goto(LEDGER_URL)
    const table = page.locator('table')
    await expect(table.getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    await table.getByRole('button', { name: /Leaves/ }).click()
    await expect(table.getByRole('columnheader', { name: /Leaves/ })).toHaveAttribute('aria-sort', 'descending')
    await expect(page).toHaveURL(/sort=-leaves/)

    await page.getByLabel('Group by').selectOption('leave_type')
    await expect(page.getByLabel('Then by')).toBeEnabled()
    await page.getByRole('button', { name: 'Run report' }).click()
    await expect(page).toHaveURL(/group=leave_type/)
    await expect(table.locator('tbody tr[data-kind="subtotal"]').first()).toBeVisible()

    await page.getByRole('button', { name: /^Columns/ }).click()
    await page.getByRole('checkbox', { name: 'Leaves' }).uncheck()
    await expect(table.getByRole('columnheader', { name: /Leaves/ })).toHaveCount(0)
    await expect(page).toHaveURL(/hide=leaves/)

    // Reloading the URL restores all three.
    await page.reload()
    await expect(table.locator('tbody tr[data-kind="subtotal"]').first()).toBeVisible()
    await expect(table.getByRole('columnheader', { name: /Leaves/ })).toHaveCount(0)
  })

  test('the employee picker is a scoped combobox; empty and narrowed read differently', async ({ page }) => {
    await page.goto(LEDGER_URL)
    await expect(page.locator('table')).toBeVisible()

    const picker = page.getByRole('combobox', { name: 'Employee' })
    await picker.fill('M')
    await expect(page.getByRole('listbox', { name: 'Employee' }).getByText('Type at least 2 letters.')).toBeVisible()
    await picker.fill(COLLEAGUE_NAME)
    await page.getByRole('option', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
    await page.getByRole('button', { name: 'Run report' }).click()
    await expect(page).toHaveURL(/employee=/)
    // The manager has no Leave Ledger Entry of their own.
    await expect(page.getByText('Nothing matched these filters')).toBeVisible()

    await page.getByRole('button', { name: 'Clear Employee' }).click()
    await expect(picker).toHaveValue('')
  })

  test('an out-of-scope employee in a link narrows to nothing and says so', async ({ page }) => {
    await page.goto(`${LEDGER_URL}&employee=NO-SUCH-EMPLOYEE-000`)
    await expect(page.getByText(/Employee: outside what you can report on/)).toBeVisible()
    await expect(page.getByText("You don't have access to this")).toHaveCount(0)
  })

  test('a required filter disables Run with a reason', async ({ page }) => {
    await page.goto('/helixhr/reports/monthly_attendance')
    await page.getByLabel('Month').fill('')
    await expect(page.getByRole('button', { name: 'Run report' })).toBeDisabled()
    await expect(page.getByText('Choose Month to run this report.')).toBeVisible()
  })

  test('a report key that is not in the catalog gets the refusal', async ({ page }) => {
    await page.goto('/helixhr/reports/not_a_report?from_date=2026-01-01')
    await expect(page.getByText("You don't have access to this")).toBeVisible()
    await expect(page.locator('table')).toHaveCount(0)
  })

  test('hours by project runs its own scoped query, with pickers and no Desk link', async ({ page }) => {
    await page.goto('/helixhr/reports/hours_by_project')
    await expect(page.getByRole('heading', { name: 'Hours by project', exact: true })).toBeVisible()
    for (const name of ['Employee', 'Project', 'Task']) {
      await expect(page.getByRole('combobox', { name })).toBeVisible()
    }
    await expect(page.getByLabel('Period')).toHaveValue('last_month')
    await page.getByRole('button', { name: 'Run report' }).click()
    await expect(page.locator('[data-async-state^="report-results:"]')).not.toHaveAttribute(
      'data-async-state',
      'report-results:pending',
      { timeout: 10000 },
    )
    await expect(page.getByText("You don't have access to this")).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Open in Frappe' })).toHaveCount(0)
  })

  test('the Desk link stays a secondary affordance on a wrapped report', async ({ page, context }) => {
    await page.goto('/helixhr/reports/leave_ledger')
    const deskLink = page.getByRole('button', { name: 'Open in Frappe' })
    await expect(deskLink).toBeVisible()

    const [popup] = await Promise.all([context.waitForEvent('page'), deskLink.click()])
    await popup.waitForLoadState('domcontentloaded')
    expect(popup.url()).toContain('/desk/query-report/Leave%20Ledger')
    await popup.close()
  })

  test('the table scrolls inside itself at phone width; filters fold away', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 700 })
    await page.goto(LEDGER_URL)
    await expect(page.locator('table').getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    const toggle = page.getByRole('button', { name: /^Filters/ })
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('button', { name: 'Run report' })).toBeHidden()
    await toggle.click()
    await expect(page.getByRole('button', { name: 'Run report' })).toBeVisible()

    const pageScrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
    const viewportWidth = await page.evaluate(() => document.documentElement.clientWidth)
    expect(pageScrollWidth).toBeLessThanOrEqual(viewportWidth + 1)

    const scroller = page.locator('table').locator('..')
    expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
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
  await expect(page.getByRole('link', { name: /Leave balance/ })).toHaveCount(0)

  await page.goto('/helixhr/reports/leave_ledger?from_date=2026-01-01')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
})

test('an IT Team identity is refused the same way, with no Desk-bound link visible', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the one negative assertion this project exists for')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Reports' })).toHaveCount(0)

  await page.goto('/helixhr/reports')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open in Frappe' })).toHaveCount(0)
})

// Plan 2026-10-04-001 U5/U6: export, the export log, the access matrix, and
// the report tiers. HR User, Delivery Manager and Report Manager are seeded by
// `setup_playwright_fixtures` and signed in here on a clean context, the same
// way email-templates.spec.ts signs in its Notification Manager.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const HR_USER = 'hr-user@helixhr.test'
const DELIVERY_MANAGER = 'delivery-manager@helixhr.test'
const REPORT_MANAGER = 'report-manager@helixhr.test'
const HR_MANAGER = 'hr-manager-employee@helixhr.test'

async function apiAs(baseURL: string | undefined, user: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  const login = await api.post('/api/method/login', { form: { usr: user, pwd: PASSWORD } })
  expect(login.ok(), `login as ${user}`).toBeTruthy()
  return api
}

async function signInAs(page: Page, baseURL: string | undefined, user: string) {
  const api = await apiAs(baseURL, user)
  const state = await api.storageState()
  await api.dispose()
  await page.context().clearCookies()
  await page.context().addCookies(state.cookies)
}

test.describe('export', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'HR Manager exports; tiers are below')
  })

  test('CSV export downloads a named file and lands in the export log', async ({ page }) => {
    await page.goto(LEDGER_URL)
    await expect(page.locator('table').getByRole('cell', { name: 'Casual Leave' }).first()).toBeVisible()

    await page.getByRole('button', { name: 'Export', exact: true }).click()
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('menuitem', { name: /CSV/ }).click(),
    ])
    expect(download.suggestedFilename()).toMatch(/^helixhr_leave_ledger_2000-01-01_2100-01-01_\d{8}T\d{4}\.csv$/)

    await page.goto('/helixhr/reports')
    await page.getByRole('button', { name: 'Export log' }).click()
    await expect(page.getByTestId('export-log-row').first()).toContainText('Leave ledger')
    await expect(page.getByTestId('export-log-row').first()).toContainText('CSV')
  })
})

test.describe('report tiers', () => {
  test.describe.configure({ mode: 'serial' })
  test.use({ storageState: { cookies: [], origins: [] } })
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'runs once, in the hr project')
  })

  test('a Delivery Manager sees only project-scoped reports', async ({ page, baseURL }) => {
    await signInAs(page, baseURL, DELIVERY_MANAGER)
    await page.goto('/helixhr/reports')
    await expect(page.getByRole('link', { name: /Hours by project/ })).toBeVisible()
    await expect(page.getByRole('link', { name: /Leave ledger/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Export log' })).toHaveCount(0)
  })

  test('a Report Manager sees every family and may export', async ({ page, baseURL }) => {
    await signInAs(page, baseURL, REPORT_MANAGER)
    await page.goto('/helixhr/reports')
    for (const family of ['Time', 'Attendance', 'Leave', 'People']) {
      await expect(page.getByRole('heading', { name: family, exact: true })).toBeVisible()
    }
    await page.goto('/helixhr/reports/leave_ledger')
    await expect(page.getByRole('button', { name: 'Export', exact: true })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Open in Frappe' })).toHaveCount(0)
  })

  test('HR Manager grants HR User export in Settings; HR User sees it after reload', async ({
    page,
    baseURL,
  }) => {
    const admin = await apiAs(baseURL, HR_MANAGER)
    const restore = async (flags: object) => {
      const response = await admin.post('/api/method/helixhr.api.save_report_access', {
        data: { rows: [{ key: 'leave_ledger', ...flags }] },
      })
      expect(response.ok()).toBeTruthy()
    }
    await restore({ hr_user_run: 1, hr_user_export: 0 })
    try {
      await signInAs(page, baseURL, HR_USER)
      await page.goto('/helixhr/reports/leave_ledger')
      const exportButton = page.getByRole('button', { name: 'Export', exact: true })
      await expect(exportButton).toBeDisabled()
      await expect(exportButton).toHaveAccessibleDescription(/not export it/)

      await signInAs(page, baseURL, HR_MANAGER)
      await page.goto('/helixhr/settings/report-access')
      const cell = page.getByRole('checkbox', { name: 'Leave ledger: HR User export' })
      await cell.check()
      // Delivery Manager cells on a company-only report are disabled, with the reason.
      await expect(page.getByRole('checkbox', { name: 'Leave ledger: Delivery Manager run' })).toBeDisabled()
      await expect(page.getByTestId('access-save-bar')).toContainText('1 unsaved change')
      await page.getByRole('button', { name: 'Save', exact: true }).click()
      await expect(page.getByText('Saved 1 report.')).toBeVisible()

      await signInAs(page, baseURL, HR_USER)
      await page.goto('/helixhr/reports/leave_ledger')
      await expect(page.getByRole('button', { name: 'Export', exact: true })).toBeEnabled()
    } finally {
      await restore({ hr_user_run: 1, hr_user_export: 0 })
      await admin.dispose()
    }
  })
})
