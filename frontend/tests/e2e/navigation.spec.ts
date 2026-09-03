import { test, expect, request } from '@playwright/test'

// Every other spec in this suite reaches its page with page.goto('/helixhr/<x>').
// That is why a portal whose App.vue was nothing but <router-view /> -- no side
// nav, no tab bar, eleven pages reachable only by typing the URL -- passed a
// fully green e2e run. These tests only ever click, so the shell has to exist.
//
// Both navs carry aria-label="Main" but only one is ever displayed (the other
// is `display:none` via lg:hidden / hidden lg:flex, so it is out of the
// accessibility tree and getByRole cannot match it). That keeps a single
// locator working at both widths.
const mainNav = (page) => page.getByRole('navigation', { name: 'Main' })

test.describe('desktop side nav', () => {
  test('reaches every employee page by clicking, never by URL', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')

    const pages = [
      { link: 'Leave', url: /\/helixhr\/leave$/, heading: 'Leave' },
      { link: 'Timesheet', url: /\/helixhr\/timesheet$/, heading: 'Timesheet' },
      { link: 'Requests', url: /\/helixhr\/requests$/, heading: 'Requests' },
      { link: 'Attendance', url: /\/helixhr\/attendance$/, heading: 'Attendance' },
      { link: 'Documents', url: /\/helixhr\/documents$/, heading: 'Documents' },
      { link: 'Notifications', url: /\/helixhr\/notifications$/, heading: 'Notifications' },
      { link: 'Profile', url: /\/helixhr\/profile$/, heading: 'Your profile' },
    ]

    for (const target of pages) {
      await mainNav(page).getByRole('link', { name: target.link, exact: false }).click()
      await expect(page).toHaveURL(target.url)
      await expect(page.getByRole('heading', { level: 1, name: target.heading })).toBeVisible()
      // The item the user just landed on is the one marked current (R7).
      await expect(
        mainNav(page).getByRole('link', { name: target.link, exact: false }),
      ).toHaveAttribute('aria-current', 'page')
    }

    await mainNav(page).getByRole('link', { name: 'Home' }).click()
    await expect(page).toHaveURL(/\/helixhr\/?$/)
  })

  test('hides Approvals from an employee with no reports (U12)', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await expect(mainNav(page).getByRole('link', { name: 'Leave' })).toBeVisible()
    await expect(mainNav(page).getByRole('link', { name: 'Approvals' })).toHaveCount(0)
  })

  test('shows Approvals to a manager and opens it (U12)', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'manager-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await mainNav(page).getByRole('link', { name: 'Approvals' }).click()
    await expect(page).toHaveURL(/\/helixhr\/approvals$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible()
  })
})

test.describe('phone tab bar', () => {
  test('navigates from the tab bar and from More', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.setViewportSize({ width: 360, height: 780 })
    await page.goto('/helixhr')

    await mainNav(page).getByRole('link', { name: 'Timesheet' }).click()
    await expect(page).toHaveURL(/\/helixhr\/timesheet$/)

    // Anything past the four tab slots lives behind More (design system:
    // max five items in the bar).
    await expect(mainNav(page).getByRole('link', { name: 'Documents' })).toHaveCount(0)
    await mainNav(page).getByRole('button', { name: 'More' }).click()
    await page.getByRole('link', { name: 'Documents' }).click()
    await expect(page).toHaveURL(/\/helixhr\/documents$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Documents' })).toBeVisible()
  })
})

test.describe('not-linked page', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('renders without nav chrome', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'run once')
    // Reuses the no-employee identity login-dashboard.spec.ts already covers;
    // here the point is only that this route is the one page with no nav.
    const api = await request.newContext({
      baseURL: process.env.BASE_URL || 'http://localhost:8000',
      extraHTTPHeaders: { Host: process.env.SITE_HOST || 'test_site' },
    })
    const login = await api.post('/api/method/login', {
      form: {
        usr: 'no-employee@helixhr.test',
        pwd: process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!',
      },
    })
    expect(login.ok()).toBeTruthy()
    const state = await api.storageState()
    await api.dispose()

    await page.context().addCookies(state.cookies)
    await page.goto('/helixhr')
    await expect(page).toHaveURL(/\/not-linked/)
    await expect(mainNav(page)).toHaveCount(0)
  })
})

test.describe('signing out', () => {
  // A fresh session on purpose. Frappe's logout deletes the server-side
  // session for that sid, and the two projects share one storageState -- so
  // signing out of the shared session would break every spec that runs after
  // this one. This test owns the session it destroys.
  test.use({ storageState: { cookies: [], origins: [] } })

  test('ends the session and lands on the login page, not a Desk error', async ({ page }) => {
    const api = await request.newContext({
      baseURL: process.env.BASE_URL || 'http://localhost:8000',
      extraHTTPHeaders: { Host: process.env.SITE_HOST || 'test_site' },
    })
    const login = await api.post('/api/method/login', {
      form: {
        usr: 'employee@helixhr.test',
        pwd: process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!',
      },
    })
    expect(login.ok()).toBeTruthy()
    const state = await api.storageState()
    await api.dispose()
    await page.context().addCookies(state.cookies)

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await page.getByRole('button', { name: 'Sign out' }).click()

    // The bug this guards: `logout` is POST-only, a GET is refused with
    // PermissionError, and swallowing that left the session alive -- /login
    // then bounced the user to the Desk, which an employee cannot open, so
    // signing out ended on a Frappe "Not permitted" page.
    await page.waitForURL(/\/login/)
    await expect(page).not.toHaveURL(/\/(app|desk)/)

    const after = await page.request.get('/api/method/frappe.auth.get_logged_user')
    expect(after.status(), 'the server session must actually be gone').toBe(403)
  })
})
