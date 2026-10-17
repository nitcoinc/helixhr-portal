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
      // P3-U1 step 1: the three destinations everybody gets.
      { link: 'Payslips', url: /\/helixhr\/payslips$/, heading: 'Payslips' },
      { link: 'Holidays', url: /\/helixhr\/holidays$/, heading: 'Holidays' },
      { link: 'Documents', url: /\/helixhr\/documents$/, heading: 'Documents' },
      { link: 'Directory', url: /\/helixhr\/directory$/, heading: 'Directory' },
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
    // P3-KTD11: Team follows direct reports, and this employee has none.
    await expect(mainNav(page).getByRole('link', { name: 'Team' })).toHaveCount(0)
  })

  test('sends an employee with no reports from /team to Home, with no 403 (EP-24)', async ({
    page,
  }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    const refused: string[] = []
    page.on('response', (response) => {
      if (response.status() === 403) refused.push(response.url())
    })
    await page.goto('/helixhr/team')
    await expect(page).toHaveURL(/\/helixhr\/?$/)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.waitForLoadState('networkidle')
    expect(refused, refused.join('\n')).toEqual([])
  })

  test('shows Team to a manager with reports and opens it (P3-KTD11)', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'manager-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await mainNav(page).getByRole('link', { name: 'Team' }).click()
    await expect(page).toHaveURL(/\/helixhr\/team$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Team' })).toBeVisible()
    await expect(mainNav(page).getByRole('link', { name: 'Team' })).toHaveAttribute(
      'aria-current',
      'page',
    )
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
    // This forces a desktop viewport to test scroll-restoration at that
    // scale, so it is not exercising anything specific to mobile WebKit's
    // touch emulation (unlike the other mobile-webkit-mandatory specs) --
    // and that emulation has no mouse wheel at all (`page.mouse.wheel`
    // throws "not supported in mobile WebKit"). Confirmed a programmatic
    // `window.scrollBy` is not a substitute: it moves window.scrollY the
    // same as a real wheel does, but whatever actually persists the
    // position for the router's popstate restoration did not pick it up
    // (reproduced on Chromium too -- restoration failed there as well),
    // so this is a missing input, not a missing feature, and is skipped
    // rather than faked.
    test.skip(
      testInfo.project.name === 'employee-mobile-webkit',
      'wheel-scroll restoration has no touch equivalent to test here',
    )
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

// Plan 2026-10-02-001 U15 (R8, R9): the rail is sectioned, admin sections
// collapse, and the More sheet carries the same headings.
test.describe('grouped navigation (U15)', () => {
  const rail = (page) => page.locator('aside nav[aria-label="Main"]')
  const HR_COLLAPSED_KEY = 'helixhr.nav.collapsed:hr-manager-employee@helixhr.test'

  test('an employee sees no HR or Admin section', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await expect(rail(page).getByRole('heading', { name: 'My work' })).toBeVisible()
    await expect(rail(page).getByRole('heading', { name: 'Pay & policies' })).toBeVisible()
    await expect(rail(page).getByRole('button', { name: 'HR' })).toHaveCount(0)
    await expect(rail(page).getByRole('button', { name: 'Admin' })).toHaveCount(0)
  })

  test('HR collapses its section by keyboard, it survives a reload, and its route opens it', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'hr-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    // KTD13: the remembered state is per user.
    await page.evaluate((key) => localStorage.setItem(key, '[]'), HR_COLLAPSED_KEY)
    await page.reload()

    const toggle = rail(page).getByRole('button', { name: 'HR' })
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(rail(page).getByRole('link', { name: 'People' })).toBeVisible()

    await toggle.focus()
    await page.keyboard.press('Enter')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(rail(page).getByRole('link', { name: 'People' })).toHaveCount(0)

    await page.reload()
    await expect(rail(page).getByRole('button', { name: 'HR' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )

    // Landing on a route inside a collapsed section opens it for that view
    // only; the remembered state is untouched.
    await page.goto('/helixhr/people')
    await expect(rail(page).getByRole('button', { name: 'HR' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    await expect(rail(page).getByRole('link', { name: 'People' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(await page.evaluate((key) => localStorage.getItem(key), HR_COLLAPSED_KEY)).toBe('["hr"]')
    await page.evaluate((key) => localStorage.removeItem(key), HR_COLLAPSED_KEY)
  })

  test('the rail fits an HR user at 800px without scrolling', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'hr-only scenario')
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/helixhr')
    await page.evaluate((key) => localStorage.removeItem(key), HR_COLLAPSED_KEY)
    await page.reload()
    // Plan 2026-10-17-001 U4 flips this (R8, HR-06): on a first visit HR's own
    // work group opens, directly under the pinned items; Admin and Pay &
    // policies start collapsed, and every heading still shows.
    await expect(rail(page).getByRole('button', { name: 'HR' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    await expect(rail(page).getByRole('link', { name: 'People' })).toBeVisible()
    await expect(rail(page).getByRole('button', { name: 'Admin' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    await expect(rail(page).getByRole('button', { name: 'Pay & policies' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    // HR-07: one People entry, no second Directory lookup.
    await expect(rail(page).getByRole('link', { name: 'Directory' })).toHaveCount(0)
    // The row budget in AppShell.vue: nothing scrolls at 800px.
    const overflow = await rail(page).evaluate((node) => node.scrollHeight - node.clientHeight)
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('the More sheet shows the same headings and the tab bar is unchanged', async ({
    page,
  }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/helixhr')
    const tabs = page.locator('div > nav[aria-label="Main"]').getByRole('link')
    await expect(tabs).toHaveText(['Home', 'Leave', 'Timesheet', 'Requests'])

    await page.getByRole('button', { name: 'More' }).click()
    const dialog = page.getByRole('dialog')
    for (const heading of ['My work', 'Pay & policies', 'People & team']) {
      await expect(dialog.getByRole('heading', { name: heading })).toBeVisible()
    }
    await expect(dialog.getByRole('button', { name: 'My work' })).toHaveCount(0)
    await expect(dialog.getByRole('link', { name: 'Attendance' })).toBeVisible()
  })
})

// Plan 2026-10-17-001 U4 (R3, R8, R9, R10, KTD7, KTD8, KTD13).
test.describe('the shell (U4)', () => {
  const tabBar = (page) => page.locator('div > nav[aria-label="Main"]')
  const rail = (page) => page.locator('aside nav[aria-label="Main"]')
  const EMPLOYEE_BAR = ['Home', 'Leave', 'Timesheet', 'Requests', 'More']
  const APPROVER_BAR = ['Home', 'Approvals', 'Timesheet', 'Requests', 'More']
  // KTD7's table, per Playwright identity.
  const EXPECTED_TABS = {
    employee: EMPLOYEE_BAR,
    'employee-mobile-webkit': EMPLOYEE_BAR,
    manager: APPROVER_BAR,
    hr: APPROVER_BAR,
    'hr-user': EMPLOYEE_BAR,
    it: EMPLOYEE_BAR,
    'portal-admin': EMPLOYEE_BAR,
  }

  async function expectTabs(page, names: string[]) {
    const tabs = tabBar(page).locator('a, button')
    await expect(tabs).toHaveCount(names.length)
    for (const [index, name] of names.entries()) {
      // Accessible names: a count badge is aria-hidden and never part of one.
      await expect(tabs.nth(index)).toHaveAccessibleName(name)
    }
  }

  test("each identity's phone tab bar matches KTD7", async ({ page }, testInfo) => {
    const expected = EXPECTED_TABS[testInfo.project.name]
    test.skip(!expected, 'no tab-bar row for this project')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/helixhr')
    await expectTabs(page, expected)

    if (expected === APPROVER_BAR) {
      // Leave gave up its slot and leads "My work" in More.
      await tabBar(page).getByRole('button', { name: 'More' }).click()
      const work = page.getByRole('dialog').getByRole('region', { name: 'My work' })
      await expect(work.getByRole('link').first()).toHaveAccessibleName('Leave')
    }
    if (testInfo.project.name === 'hr-user') {
      // Oversight decides nothing, so nothing is counted anywhere.
      await expect(page.getByTestId('tab-badge-more')).toHaveCount(0)
      await expect(page.getByTestId('tab-badge-approvals')).toHaveCount(0)
    }
  })

  test.describe('desk-only HR', () => {
    test.use({ storageState: { cookies: [], origins: [] } })

    test('keeps Home and More, and no Ask HR', async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== 'employee', 'run once')
      const api = await request.newContext({
        baseURL: process.env.BASE_URL || 'http://localhost:8000',
        extraHTTPHeaders: { Host: process.env.SITE_HOST || 'test_site' },
      })
      const login = await api.post('/api/method/login', {
        form: {
          usr: 'hr-manager@helixhr.test',
          pwd: process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!',
        },
      })
      expect(login.ok()).toBeTruthy()
      const state = await api.storageState()
      await api.dispose()
      await page.context().addCookies(state.cookies)

      await page.setViewportSize({ width: 390, height: 844 })
      await page.goto('/helixhr')
      await expect(page.locator('[data-portal-state="desk-only"]')).toBeVisible()
      await expectTabs(page, ['Home', 'More'])
      await tabBar(page).getByRole('button', { name: 'More' }).click()
      await expect(page.getByRole('dialog').getByRole('link', { name: 'Ask HR' })).toHaveCount(0)
    })
  })

  test('a click navigation moves focus to main and names the page', async ({ page }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenario')
    test.skip(testInfo.project.name === 'employee-mobile-webkit', 'the desktop rail is the surface here')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
    await expect(page).toHaveTitle('Home · HelixHR')
    // Not on the first load: nothing was left behind to move from.
    expect(await page.evaluate(() => document.activeElement?.id)).not.toBe('main')

    await rail(page).getByRole('link', { name: 'Leave' }).click()
    await expect(page).toHaveURL(/\/helixhr\/leave$/)
    await expect(page).toHaveTitle('Leave · HelixHR')
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('main')
  })

  test('the first Tab reaches the skip link, and it lands in main', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'Chromium keyboard scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: 'Skip to main content' })
    await expect(skip).toBeFocused()
    await expect(skip).toBeVisible()
    // The coarse-pointer sweep skips it while it is visually hidden, so its
    // focused size is the target that has to meet the 44px floor.
    expect((await skip.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    await page.keyboard.press('Enter')
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('main')
  })

  test('Ask HR opens the New request form from the rail', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'run once')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await page.locator('aside').getByRole('link', { name: 'Ask HR' }).click()
    await expect(page).toHaveURL(/\/helixhr\/requests\?category=Other$/)
    await expect(page.getByRole('dialog', { name: 'New request' })).toBeVisible()
  })

  test("a handler's Requests entry opens the Team inbox (AI-02)", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'it', 'routed-worker scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await rail(page).getByRole('link', { name: 'Requests' }).click()
    await expect(page).toHaveURL(/\/helixhr\/requests\?tab=work$/)
  })

  test('a remembered collapse survives a reload for an Admin-first rail', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'portal-admin', 'portal-admin scenario')
    const key = 'helixhr.nav.collapsed:portal-admin@helixhr.test'
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr')
    await page.evaluate((k) => localStorage.removeItem(k), key)
    await page.reload()
    // AI-15: Admin is this role's whole job, so it starts open; its header
    // carries no item count.
    const admin = rail(page).getByRole('button', { name: 'Admin' })
    await expect(admin).toHaveAttribute('aria-expanded', 'true')
    await admin.click()
    await expect(admin).toHaveAttribute('aria-expanded', 'false')
    await expect(admin).toHaveAccessibleName('Admin')
    await page.reload()
    await expect(rail(page).getByRole('button', { name: 'Admin' })).toHaveAttribute('aria-expanded', 'false')
    await page.evaluate((k) => localStorage.removeItem(k), key)
  })

  test('seven click navigations cost one bootstrap and no count call (R3, KTD8)', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'manager-only scenario')
    await page.setViewportSize({ width: 1440, height: 900 })
    const calls: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (url.includes('/api/method/')) calls.push(decodeURIComponent(url.split('/api/method/')[1].split('?')[0]))
    })
    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
    for (const link of ['Leave', 'Timesheet', 'Requests', 'Attendance', 'Documents', 'Notifications', 'Team']) {
      await rail(page).getByRole('link', { name: link, exact: false }).first().click()
      await expect(rail(page).getByRole('link', { name: link, exact: false }).first()).toHaveAttribute(
        'aria-current',
        'page',
      )
    }
    expect(calls.filter((c) => c === 'helixhr.api.get_portal_bootstrap')).toHaveLength(1)
    expect(calls.filter((c) => c === 'helixhr.api.get_my_approvals')).toHaveLength(0)
  })
})

// KTD8's lifecycle in the browser: the Approvals page sets the badge to its
// own total, and a decision takes one off at once -- before the page's own
// reload answers, and without asking anyone else for a count.
test.describe('the Approvals badge (U4, KTD8)', () => {
  const SITE_HOST = process.env.SITE_HOST || 'test_site'
  const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
  const EMPLOYEE = 'employee@helixhr.test'
  const PROJECT_NAME = 'Navigation Spec Project'
  // Clear of every week approvals.spec.ts (5, 9, 10, 11) and
  // timesheet-entry.spec.ts (10, 11) work in: one Timesheet per week.
  const WEEKS_BACK = 14

  async function admin(baseURL: string) {
    const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
    await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
    return api
  }

  async function value(api, doctype: string, filters: object, fieldname: string) {
    const response = await api.get(
      `/api/method/frappe.client.get_value?doctype=${encodeURIComponent(doctype)}&filters=` +
        encodeURIComponent(JSON.stringify(filters)) +
        `&fieldname=${fieldname}`,
    )
    return (await response.json())?.message?.[fieldname]
  }

  async function clearWeek(api, employee: string, monday: string) {
    const sunday = new Date(`${monday}T00:00:00Z`)
    sunday.setUTCDate(sunday.getUTCDate() + 6)
    const existing = await api.get(
      '/api/method/frappe.client.get_list?doctype=Timesheet&filters=' +
        encodeURIComponent(
          JSON.stringify({ employee, start_date: ['between', [monday, sunday.toISOString().slice(0, 10)]] }),
        ) +
        '&fields=' +
        encodeURIComponent(JSON.stringify(['name', 'docstatus'])) +
        '&limit_page_length=0',
    )
    for (const row of (await existing.json())?.message || []) {
      if (row.docstatus === 1) {
        await api.post('/api/method/frappe.client.cancel', { form: { doctype: 'Timesheet', name: row.name } })
      }
      await api.post('/api/method/frappe.client.delete', { form: { doctype: 'Timesheet', name: row.name } })
    }
  }

  /** One pending week for the manager's report, through the portal's own
   * submit, so the Pending-Approval share is the real one. */
  async function seedPendingWeek(baseURL: string) {
    const now = new Date()
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
    day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7) - WEEKS_BACK * 7)
    const monday = day.toISOString().slice(0, 10)

    const api = await admin(baseURL)
    const employee = await value(api, 'Employee', { user_id: EMPLOYEE }, 'name')
    const company = await value(api, 'Employee', { user_id: EMPLOYEE }, 'company')
    let project = await value(api, 'Project', { project_name: PROJECT_NAME }, 'name')
    if (!project) {
      const created = await api.post('/api/method/frappe.client.insert', {
        data: { doc: JSON.stringify({ doctype: 'Project', project_name: PROJECT_NAME, status: 'Open', company }) },
      })
      project = (await created.json()).message.name
    }
    if (!(await value(api, 'User Permission', { user: EMPLOYEE, allow: 'Project', for_value: project }, 'name'))) {
      await api.post('/api/method/frappe.client.insert', {
        data: {
          doc: JSON.stringify({ doctype: 'User Permission', user: EMPLOYEE, allow: 'Project', for_value: project }),
        },
      })
    }
    await clearWeek(api, employee, monday)
    await api.dispose()

    const employeeApi = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
    await employeeApi.post('/api/method/login', { form: { usr: EMPLOYEE, pwd: PASSWORD } })
    const submitted = await employeeApi.post('/api/method/helixhr.api.submit_my_week', {
      form: { week_start: monday, rows: JSON.stringify([{ date: monday, project, task: '', hours: 8, note: '' }]) },
    })
    expect(submitted.ok(), await submitted.text()).toBeTruthy()
    await employeeApi.dispose()

    const check = await admin(baseURL)
    const name = await value(check, 'Timesheet', { employee, start_date: monday }, 'name')
    await check.dispose()
    return { name, employee, monday }
  }

  test('Approvals sets the badge to its total, and a decision takes one off with no extra request', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'manager-only scenario')
    test.setTimeout(90000)
    const baseURL = process.env.BASE_URL || 'http://localhost:8080'
    const { name, employee, monday } = await seedPendingWeek(baseURL)
    const badge = page.locator('aside').getByTestId('nav-badge-approvals')

    try {
      await page.setViewportSize({ width: 1440, height: 900 })
      const loaded = page.waitForResponse((r) => r.url().includes('helixhr.api.get_my_approvals'))
      await page.goto('/helixhr/approvals?view=timesheets')
      const first = (await (await loaded).json()).message
      expect(first.total).toBeGreaterThan(0)
      await expect(badge).toHaveText(`${first.total}${first.total_is_capped ? '+' : ''}`)

      await page.getByTestId('approvals-queue').locator(`[data-approval-name="${name}"]`).click()
      const panel = page.getByTestId('approval-detail')
      await expect(panel.getByRole('button', { name: /^Approve/ })).toBeVisible({ timeout: 10000 })

      // Hold the page's own reload, so what the badge shows is the local
      // decrement and nothing else.
      let release = () => {}
      const held = new Promise<void>((resolve) => (release = resolve))
      await page.route('**/api/method/helixhr.api.get_my_approvals*', async (route) => {
        await held
        await route.continue()
      })
      const after: string[] = []
      page.on('request', (req) => {
        const url = req.url()
        if (url.includes('/api/method/')) after.push(decodeURIComponent(url.split('/api/method/')[1].split('?')[0]))
      })

      await panel.getByRole('button', { name: /^Approve/ }).click()
      const expected = first.total - 1
      if (expected > 0) await expect(badge).toHaveText(`${expected}${first.total_is_capped ? '+' : ''}`)
      else await expect(badge).toHaveCount(0)
      expect(after.filter((c) => c !== 'helixhr.api.act_on_approval' && c !== 'helixhr.api.get_my_approvals')).toEqual(
        [],
      )
      release()
      // Let the held reload finish before the route goes, or Playwright
      // reports the handler's continue() as already handled.
      await page.unrouteAll({ behavior: 'wait' })
    } finally {
      const api = await admin(baseURL)
      await clearWeek(api, employee, monday)
      await api.dispose()
    }
  })
})
