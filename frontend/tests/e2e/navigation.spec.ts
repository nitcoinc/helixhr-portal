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
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
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
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
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
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
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

// P2-U2. What the router now owes: one identity lookup per hard load,
// addressable exact routes, and a session failure that is told apart from
// an ordinary permission failure.

test.describe('route changes reuse the bootstrap (P2-R20, P2-R21)', () => {
  test('seven navigations repeat no employee or capability lookup', async ({ page }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })

    const calls: string[] = []
    page.on('request', (request) => {
      const url = request.url()
      if (url.includes('/api/method/')) {
        calls.push(decodeURIComponent(url.split('/api/method/')[1].split('?')[0]))
      }
    })

    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
    const afterBoot = calls.length
    expect(calls.filter((c) => c === 'helixhr.api.get_portal_bootstrap')).toHaveLength(1)

    for (const link of [
      'Leave',
      'Timesheet',
      'Requests',
      'Attendance',
      'Documents',
      'Notifications',
      'Profile',
    ]) {
      await mainNav(page).getByRole('link', { name: link, exact: false }).click()
      await expect(mainNav(page).getByRole('link', { name: link, exact: false })).toHaveAttribute(
        'aria-current',
        'page',
      )
    }

    const duringRoutes = calls.slice(afterBoot)
    // The whole point of the bootstrap: seven route changes cost zero
    // identity or capability round trips from the router and the shell.
    // Before this unit the guard re-ran hrms.api.get_current_employee_info
    // on every single one of them.
    expect(duringRoutes.filter((c) => c === 'helixhr.api.get_portal_bootstrap')).toHaveLength(0)
    expect(duringRoutes.filter((c) => c === 'frappe.client.get_count')).toHaveLength(0)
  })

  test('no route repeats the identity lookup (P2-R21)', async ({ page }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })

    const calls: string[] = []
    page.on('request', (request) => {
      const url = request.url()
      if (url.includes('/api/method/')) {
        calls.push(decodeURIComponent(url.split('/api/method/')[1].split('?')[0]))
      }
    })

    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
    const afterBoot = calls.length

    // P2-U3 removed the last five page-local
    // `hrms.api.get_current_employee_info` resources -- Leave, Approvals,
    // Profile, Documents and TimesheetHistory each created one, left over
    // from before P2-U2's bootstrap. Every route below now reads identity
    // from `lib/session.js`, so the whole nav can be walked without a single
    // repeat lookup, which is the second half of P2-R21.
    for (const link of [
      'Attendance',
      'Notifications',
      'Requests',
      'Timesheet',
      'Leave',
      'Documents',
      'Profile',
    ]) {
      await mainNav(page).getByRole('link', { name: link, exact: false }).click()
      await expect(mainNav(page).getByRole('link', { name: link, exact: false })).toHaveAttribute(
        'aria-current',
        'page',
      )
    }

    expect(calls.slice(afterBoot).filter((c) => c.includes('get_current_employee'))).toHaveLength(0)
  })
})

test.describe('exact routes are addressable (P2-R12)', () => {
  test('a week route survives a refresh', async ({ page }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    // The week is addressed by its Monday, so the URL means the same thing
    // tomorrow as it does today -- that is what an offset ("last week")
    // could never do.
    await page.goto('/helixhr/timesheet/2026-09-07')
    await expect(page.getByRole('heading', { level: 1, name: 'Timesheet' })).toBeVisible()
    await page.reload()
    await expect(page).toHaveURL(/\/helixhr\/timesheet\/2026-09-07$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Timesheet' })).toBeVisible()
    // ...and a malformed week is a missing page, not an arbitrary one.
    await page.goto('/helixhr/timesheet/last-week')
    await expect(page.getByRole('heading', { name: 'That page does not exist' })).toBeVisible()
  })

  test('Back returns to the previous list, at where it was left', async ({ page }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    await page.setViewportSize({ width: 1440, height: 700 })
    await page.goto('/helixhr/notifications')
    await expect(page.getByRole('heading', { level: 1, name: 'Notifications' })).toBeVisible()

    await page.mouse.wheel(0, 400)
    const leftAt = await page.evaluate(() => window.scrollY)

    await mainNav(page).getByRole('link', { name: 'Profile' }).click()
    await expect(page).toHaveURL(/\/helixhr\/profile$/)

    await page.goBack()
    await expect(page).toHaveURL(/\/helixhr\/notifications$/)
    if (leftAt > 0) {
      // scrollBehavior returns the saved position on a popstate. Only
      // asserted when the list was actually long enough to scroll, so this
      // does not become a test of the fixture data's length.
      await expect
        .poll(() => page.evaluate(() => window.scrollY))
        .toBeGreaterThan(leftAt - 50)
    }
  })
})

test.describe('a dead session and a refused action are different things', () => {
  // Owns the session it destroys, like the sign-out test above.
  test.use({ storageState: { cookies: [], origins: [] } })

  async function signIn(page, usr: string) {
    const api = await request.newContext({
      baseURL: process.env.BASE_URL || 'http://localhost:8000',
      extraHTTPHeaders: { Host: process.env.SITE_HOST || 'test_site' },
    })
    const login = await api.post('/api/method/login', {
      form: { usr, pwd: process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!' },
    })
    expect(login.ok()).toBeTruthy()
    const state = await api.storageState()
    await api.dispose()
    await page.context().addCookies(state.cookies)
  }

  test('an expired session redirects to login, carrying the page', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'run once')
    await signIn(page, 'employee@helixhr.test')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()

    // The session dies while the tab stays open. The bootstrap is already
    // cached, so it is the *domain* call on the next page that finds out.
    // Through the page, so it carries the CSRF token the API requires.
    const loggedOut = await page.evaluate(async () => {
      const response = await fetch('/api/method/logout', {
        method: 'POST',
        headers: { 'X-Frappe-CSRF-Token': window.csrf_token },
      })
      return response.status
    })
    expect(loggedOut, 'the session has to actually be gone').toBe(200)

    await mainNav(page).getByRole('link', { name: 'Documents' }).click()
    await page.waitForURL(/\/login/)
    expect(decodeURIComponent(page.url())).toContain('/helixhr/documents')
  })

  test('an ordinary permission failure stays an in-app error', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'run once')
    await signIn(page, 'employee@helixhr.test')
    await page.setViewportSize({ width: 1440, height: 900 })

    // A logged-in user who is refused one action gets a 403 PermissionError
    // too -- without the "Login to access" phrase. Redirecting that to the
    // login page would throw away a live session and tell the user the
    // wrong thing entirely (see the note in lib/api.js).
    await page.route('**/api/method/helixhr.api.get_my_documents*', (route) =>
      route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({
          exc_type: 'PermissionError',
          _server_messages: JSON.stringify([
            JSON.stringify({ message: 'Not permitted to read Document Link' }),
          ]),
        }),
      }),
    )

    await page.goto('/helixhr/documents')
    await expect(page.getByRole('heading', { level: 1, name: 'Documents' })).toBeVisible()
    await expect(page).not.toHaveURL(/\/login/)
  })
})
