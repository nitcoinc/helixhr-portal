import { test, expect, request, type APIRequestContext } from '@playwright/test'

// Plan 2026-10-14-001 U3. The Company access section in portal Settings.
// Fixtures come from `helixhr.tests.utils.ensure_company_access_fixtures`
// (part of `setup_playwright_fixtures`): the target has no grant at setup,
// and the read-only holder's grant names a company the Portal Admin cannot
// reach.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const TARGET = 'grant-e2e-target@helixhr.test'
const READONLY = 'grant-e2e-readonly@helixhr.test'
const SECOND_COMPANY = '_Test Company B'

async function adminApi(baseURL: string | undefined): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  const login = await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  expect(login.ok(), 'login as Administrator').toBeTruthy()
  return api
}

// Re-runnable: drop any grant an earlier run left on the target. A 404 (no
// grant) is the normal case and is ignored.
async function clearTargetGrant(baseURL: string | undefined) {
  const api = await adminApi(baseURL)
  await api.post('/api/method/frappe.client.delete', {
    data: { doctype: 'HelixHR Company Access', name: TARGET },
  })
  await api.dispose()
}

test.describe('Portal Admin manages company access', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'portal-admin', 'Company access is a Portal Admin section')
  })

  test.beforeAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name === 'portal-admin') await clearTargetGrant(baseURL)
  })

  test.afterAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name === 'portal-admin') await clearTargetGrant(baseURL)
  })

  test('adds an All data grant on the second company', async ({ page }) => {
    await page.goto('/helixhr/settings/company-access')
    await expect(page.getByTestId('settings-tab-company-access')).toHaveAttribute('aria-current', 'page')

    await page.getByTestId('company-access-add').click()
    const form = page.getByTestId('company-access-form')
    await form.getByLabel('Find an employee').fill('Grant-e2e-target')
    await form.getByRole('button', { name: /Grant-e2e-target/ }).click()
    await form.getByRole('radio', { name: /All data/ }).check()
    await form.getByRole('checkbox', { name: SECOND_COMPANY, exact: true }).check()
    await page.getByTestId('company-access-save').click()

    await expect(page.getByRole('status').filter({ hasText: 'needs to reload the portal' })).toBeVisible()
    const row = page.getByTestId(`company-access-row-${TARGET}`)
    await expect(row).toContainText('All data')
    await expect(row).toContainText(SECOND_COMPANY)
    await expect(row.getByTestId('company-access-status')).toHaveText(/^Enabled/)
  })

  test('disables it after a confirm step', async ({ page }) => {
    await page.goto('/helixhr/settings/company-access')
    const row = page.getByTestId(`company-access-row-${TARGET}`)
    await row.getByRole('button', { name: 'Disable' }).click()
    await row.getByTestId('company-access-confirm').click()

    await expect(row.getByTestId('company-access-status')).toContainText('Disabled')
    await expect(row.getByRole('button', { name: 'Re-enable' })).toBeVisible()
  })

  test('a grant outside the admin\'s companies is read-only', async ({ page }) => {
    await page.goto('/helixhr/settings/company-access')
    const row = page.getByTestId(`company-access-row-${READONLY}`)
    await expect(row).toContainText('Reports only')
    await expect(row).toContainText('Outside your companies')
    await expect(row.getByRole('button')).toHaveCount(0)
  })
})

test('an HR Manager does not see the Company access tab', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the refused hat is HR')

  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
  await expect(page.getByTestId('settings-tab-company-access')).toHaveCount(0)
})
