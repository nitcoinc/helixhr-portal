import { test, expect, request } from '@playwright/test'

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

// Skeleton for U3 (auth/session/shell plumbing only); the real dashboard
// content assertions land in U4 once Dashboard.vue exists. Home.vue is
// still the U2 placeholder page at this point.

test.describe('guest', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('is redirected to /login', async ({ page }) => {
    await page.goto('/helixhr')
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('employee', () => {
  test('sees the portal shell after login, not the login page', async ({ page }) => {
    await page.goto('/helixhr')
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByText('HelixHR Portal')).toBeVisible()
  })
})

test.describe('manager', () => {
  test('sees the portal shell after login, not the login page', async ({ page }) => {
    await page.goto('/helixhr')
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByText('HelixHR Portal')).toBeVisible()
  })
})

test.describe('user with no active Employee', () => {
  // Not one of the two projects' storageState users -- logs in fresh via
  // the API, matching auth.setup.ts (R3, AE5).
  test.use({ storageState: { cookies: [], origins: [] } })

  test('sees the not-linked page, not the portal shell or a raw error', async ({
    page,
    baseURL,
  }) => {
    const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
    const login = await api.post('/api/method/login', {
      form: { usr: 'no-employee@helixhr.test', pwd: PASSWORD },
    })
    expect(login.ok()).toBeTruthy()
    const storageState = await api.storageState()
    await api.dispose()

    await page.context().addCookies(storageState.cookies)
    await page.goto('/helixhr')

    await expect(page).toHaveURL(/\/not-linked/)
    await expect(page.getByText('Your account is not set up')).toBeVisible()
  })
})
