import { test, expect, request, type APIRequestContext } from '@playwright/test'

// Plan 2026-10-15-001 U6: the People & access section in portal Settings.
// Fixtures come from `helixhr.tests.utils.ensure_company_access_fixtures`
// (part of `setup_playwright_fixtures`): the Portal Admin's home is
// `_Test Company` and a Company User Permission reaches `_Test Company B`;
// the target has no grant at setup; the foreign employee works for
// `_Test Company B`.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const TARGET = 'grant-e2e-target@helixhr.test'
const FOREIGN = 'grant-e2e-foreign@helixhr.test'
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

test.describe('Portal Admin manages people and access', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'portal-admin', 'People & access is a Portal Admin section')
  })

  test.beforeAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name === 'portal-admin') await clearTargetGrant(baseURL)
  })

  test.afterAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name === 'portal-admin') await clearTargetGrant(baseURL)
  })

  test('applies Leadership over both companies and the summary updates', async ({ page }) => {
    await page.goto('/helixhr/settings/people-access')
    await expect(page.getByTestId('settings-tab-people-access')).toHaveAttribute('aria-current', 'page')
    await page.getByTestId('people-access-search').fill('grant-e2e-target')
    await page.getByTestId(`people-access-row-${TARGET}`).click()

    const panel = page.getByTestId('people-access-panel')
    await panel.getByRole('button', { name: 'Leadership (All data grant)' }).click()
    await expect(panel.getByRole('button', { name: 'Leadership (All data grant)' })).toHaveAttribute('aria-pressed', 'true')
    await expect(panel.getByRole('checkbox', { name: SECOND_COMPANY, exact: true })).toBeChecked()
    await page.getByTestId('people-access-save').click()

    await expect(panel.getByRole('status')).toContainText('Saved')
    await expect(page.getByTestId('people-access-summary')).toContainText(SECOND_COMPANY)
  })

  test('HR roles are locked with the reason shown, and no HR profiles are offered', async ({ page }) => {
    await page.goto('/helixhr/settings/people-access')
    await page.getByTestId('people-access-search').fill('grant-e2e-target')
    await page.getByTestId(`people-access-row-${TARGET}`).click()

    const panel = page.getByTestId('people-access-panel')
    const hrManager = panel.getByRole('checkbox', { name: 'HR Manager' })
    await expect(hrManager).toHaveAttribute('aria-disabled', 'true')
    await expect(page.getByTestId('hr-roles-locked')).toBeVisible()
    await expect(panel.getByRole('button', { name: 'HR Manager', exact: true })).toHaveCount(0)
    await expect(panel.getByRole('button', { name: 'HR User', exact: true })).toHaveCount(0)
  })

  test('an employee of another company has roles locked and the grant editable', async ({ page }) => {
    await page.goto('/helixhr/settings/people-access')
    await page.getByTestId('people-access-search').fill('grant-e2e-foreign')
    await page.getByTestId(`people-access-row-${FOREIGN}`).click()

    const panel = page.getByTestId('people-access-panel')
    await expect(panel.getByRole('checkbox', { name: 'IT Team' })).toHaveAttribute('aria-disabled', 'true')
    await expect(page.getByTestId('roles-locked')).toContainText('Home company only')
    await expect(panel.getByRole('radio', { name: /All data/ })).toBeEnabled()
  })
})

test('an HR Manager without Portal Admin has no People & access tab', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the refused hat is HR')

  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
  await expect(page.getByTestId('settings-tab-people-access')).toHaveCount(0)
})
