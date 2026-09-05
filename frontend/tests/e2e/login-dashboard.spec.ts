import { test, expect, request } from '@playwright/test'

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

test.describe('guest', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('is redirected to /login', async ({ page }) => {
    await page.goto('/helixhr')
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('employee', () => {
  // Every describe block in this file runs under both the 'employee' and
  // 'manager' projects by default (Playwright doesn't scope a spec file to
  // one project just because a describe shares its name). Before U4 that
  // was harmless -- both projects saw the same placeholder text -- but a
  // real, person-specific name now makes it a real bug: this test must
  // only run logged in as the employee.
  test('sees real dashboard data: name, manager, leave card (R6)', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.goto('/helixhr')
    await expect(page).not.toHaveURL(/\/login/)

    // Designation/department aren't set on the fixture employee (see
    // helixhr/tests/utils.py's make_test_employee_and_manager docstring
    // for why) -- name and manager are enough to prove real API data
    // reaches the screen; the Python test covers the field-by-field
    // shape of get_dashboard directly.
    await expect(page.getByRole('heading', { name: 'Employee' })).toBeVisible()
    await expect(page.getByText(/Reports to Manager/)).toBeVisible()

    // U6's setup_playwright_fixtures gives the employee a real 5-day
    // Casual Leave allocation, so the card shows a real number now
    // instead of the pre-U6 empty state -- asserted as "some number",
    // not literally 5: leave.spec.ts's own "apply for leave" run against
    // this same fixture legitimately spends a day of that same balance,
    // and Playwright doesn't guarantee spec run order.
    //
    // The card is "Leave left" since the week-spine redesign moved the
    // balances into a reference rail; it was "Leave balance" when the
    // dashboard was a grid of equal-weight stat cards.
    const leaveCard = page.getByRole('link', { name: /Leave left/ })
    await expect(leaveCard).toBeVisible()
    await expect(leaveCard.getByText(/^[0-9](\.5)?$/)).toBeVisible()
  })
})

test.describe('manager', () => {
  test('sees the dashboard after login, not the login page', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'manager-only scenario')
    await page.goto('/helixhr')
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByRole('heading', { name: 'Manager' })).toBeVisible()
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

test.describe('dashboard week spine (redesign)', () => {
  test('shows the Monday..Sunday spine and the action queue', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.goto('/helixhr')

    // Seven day cells, always, and the week is graspable without scrolling
    // sideways -- that is the whole argument of this layout, so it is worth
    // a test rather than an eyeball.
    const spine = page.getByRole('region', { name: 'This week' })
    await expect(spine).toBeVisible()
    for (const day of ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']) {
      await expect(spine.getByText(day, { exact: true })).toBeVisible()
    }
    await expect(spine.getByText(/hours logged this week/)).toBeVisible()

    // P2-U4 / P2-R12: the spine links at *this* week by its Monday, not at
    // "/timesheet", which resolves to whichever week is current when the
    // link is followed. Same identity the server uses.
    await expect(spine.getByRole('link', { name: 'Timesheet' })).toHaveAttribute(
      'href',
      /\/helixhr\/timesheet\/\d{4}-\d{2}-\d{2}$/,
    )

    // The queue either lists things to act on, each carrying its own verb,
    // or says so plainly. Both are correct; a blank region is not.
    const queue = page.getByRole('region', { name: 'Needs you' })
    await expect(queue).toBeVisible()
    const rows = queue.getByRole('listitem')
    if (await rows.count()) {
      // Every row goes to a *record*, never to a list page: P2-U4 gave each
      // item an exact destination, and action-queue-notifications.spec.ts
      // travels the two that matter.
      await expect(rows.first().getByRole('link')).toHaveAttribute(
        'href',
        /\/helixhr\/(leave|requests|timesheet|approvals)\/.+/,
      )
    } else {
      await expect(queue.getByText('Nothing needs you.')).toBeVisible()
    }
  })
})

// P2-U2. Boot, and the four failure identities that used to look alike.

/** Every whitelisted method the page called, in order. */
function methodCalls(page) {
  const calls: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    if (!url.includes('/api/method/')) return
    calls.push(decodeURIComponent(url.split('/api/method/')[1].split('?')[0]))
  })
  return calls
}

test.describe('portal bootstrap (P2-R20, P2-R21)', () => {
  test('a hard load asks who you are exactly once', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    const calls = methodCalls(page)

    await page.goto('/helixhr')
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()

    // One bootstrap...
    expect(calls.filter((c) => c === 'helixhr.api.get_portal_bootstrap')).toHaveLength(1)
    // ...and none of the two calls it replaced. The router guard used to
    // run get_current_employee_info on every navigation and the shell
    // counted direct reports separately.
    expect(calls.filter((c) => c.includes('get_current_employee_info'))).toHaveLength(0)
    expect(calls.filter((c) => c === 'frappe.client.get_count')).toHaveLength(0)
  })
})

test.describe('a Guest keeps the page they asked for', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('carries the destination into the login redirect (P2-R12)', async ({ page }) => {
    await page.goto('/helixhr/leave')
    await expect(page).toHaveURL(/\/login/)
    // Not just "/login": the exact route has to survive signing in, or a
    // notification link is a link to the home page.
    expect(decodeURIComponent(page.url())).toContain('/helixhr/leave')
  })
})

test.describe('a failed portal service is not a broken account (P2-AE8)', () => {
  test('offers Retry, never "not set up", and resumes the requested page', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    const failing = '**/api/method/helixhr.api.get_portal_bootstrap*'
    await page.route(failing, (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          exc_type: 'InternalServerError',
          _server_messages: JSON.stringify([JSON.stringify({ message: 'Bootstrap exploded' })]),
        }),
      }),
    )

    await page.goto('/helixhr/requests')
    await expect(page).toHaveURL(/\/helixhr\/unavailable/)
    await expect(page.getByRole('heading', { name: 'We could not load your portal' })).toBeVisible()
    // The bug this guards: a 500 rendered as "your account is not set up",
    // which is both wrong and unactionable.
    await expect(page.getByText('Your account is not set up')).toHaveCount(0)

    await page.unroute(failing)
    await page.getByRole('button', { name: 'Retry' }).click()
    // Retry resumes the destination, it does not dump the user on Home.
    await expect(page).toHaveURL(/\/helixhr\/requests$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Requests' })).toBeVisible()
  })
})

test.describe('an unknown portal route', () => {
  test('says so and offers a way home', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
    await page.goto('/helixhr/leave/nope/not-a-route')
    await expect(page.getByRole('heading', { name: 'That page does not exist' })).toBeVisible()
    await page.getByRole('link', { name: 'Go to Home' }).click()
    await expect(page).toHaveURL(/\/helixhr\/?$/)
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
  })
})
