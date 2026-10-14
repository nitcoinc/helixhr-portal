import { test, expect, request, type APIRequestContext, type Page } from '@playwright/test'

// Plan 2026-10-14-001 U10: the Reports page offers a Company select only to
// a caller the catalog lists more than one company for, runs one company at
// a time, and carries the pick in the URL.
//
// Fixtures: `helixhr.tests.utils.ensure_company_access_fixtures` (seeded by
// `setup_playwright_fixtures`). The grant holder's home is `_Test Company`
// and an All-data grant covers `_Test Company` and `_Test Company B`; the
// foreign employee is Active in `_Test Company B` as `GRANT-E2E-B1`.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const GRANT_HOLDER = 'grant-holder@helixhr.test'
const HR_USER = 'hr-user@helixhr.test'
const HOME_COMPANY = '_Test Company'
const OTHER_COMPANY = '_Test Company B'
const FOREIGN_NUMBER = 'GRANT-E2E-B1'
// `make_test_user` numbers each fixture employee after its email's local part.
const HOLDER_NUMBER = 'grant-holder'
const DIRECTORY_URL = '/helixhr/reports/employee_directory'

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

test.describe('report company picker', () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'runs once, in the hr project')
  })

  test('a grant holder runs the directory for the granted company', async ({ page, baseURL }) => {
    await signInAs(page, baseURL, GRANT_HOLDER)
    await page.goto(DIRECTORY_URL)
    await expect(page.getByRole('heading', { name: 'Employee directory', exact: true })).toBeVisible()

    const company = page.getByRole('combobox', { name: 'Company', exact: true })
    await expect(company).toHaveValue(HOME_COMPANY)
    await company.selectOption(OTHER_COMPANY)
    await page.getByRole('button', { name: 'Run report' }).click()
    await expect(page).toHaveURL(/[?&]company=_Test(%20|\+)Company(%20|\+)B(&|$)/)

    const table = page.locator('table')
    await expect(table.getByRole('cell', { name: FOREIGN_NUMBER, exact: true })).toBeVisible()
    await expect(table.getByRole('cell', { name: HOLDER_NUMBER, exact: true })).toHaveCount(0)

    // The link reopens the same company.
    await page.reload()
    await expect(company).toHaveValue(OTHER_COMPANY)
    await expect(table.getByRole('cell', { name: FOREIGN_NUMBER, exact: true })).toBeVisible()
  })

  test('a single-company HR User sees no Company control', async ({ page, baseURL }) => {
    await signInAs(page, baseURL, HR_USER)
    await page.goto(DIRECTORY_URL)
    await expect(page.getByRole('heading', { name: 'Employee directory', exact: true })).toBeVisible()
    await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toBeVisible()
    await expect(page.getByRole('combobox', { name: 'Company', exact: true })).toHaveCount(0)
  })
})
