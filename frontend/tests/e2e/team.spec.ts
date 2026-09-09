import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// P3-U7 scenario 5 / P3-R20, P3-R21. The team week in a real browser: the
// seeded report's leave as a bar on the grid, the out-today block naming
// them, and the payload behind it carrying no leave reason.
//
// Chromium `manager` project only: the page exists for a manager with direct
// reports, and the employee identity is refused by the server (which is what
// the Python suite asserts).

const SITE_HOST = process.env.SITE_HOST || 'test_site'

/** The dedicated report and its approved leave across this week, seeded by
 * `helixhr.tests.utils.ensure_team_week_fixtures`. Called here rather than
 * from `setup_playwright_fixtures` so the row is re-dated on every run --
 * a week-shaped fixture goes stale in seven days. */
async function seedTeamWeek(baseURL: string) {
  const api: APIRequestContext = await request.newContext({
    baseURL,
    extraHTTPHeaders: { Host: SITE_HOST },
  })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  const response = await api.post('/api/method/helixhr.tests.utils.ensure_team_week_fixtures')
  expect(response.ok(), await response.text()).toBeTruthy()
  const fixture = (await response.json()).message
  await api.dispose()
  return fixture as { report_name: string; leave_type: string; leave: string }
}

/** The week label the page draws, so a navigation assertion is about what
 * the manager reads rather than about a URL. */
function weekLabel(page: Page) {
  return page.locator('h2.type-section').first()
}

test.describe('manager', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'the team week is a manager surface')
  })

  test('the seeded report shows as a bar, and is named in out today', async ({ page, baseURL }) => {
    const fixture = await seedTeamWeek(baseURL!)

    await page.goto('/helixhr/team')
    await expect(page.getByRole('heading', { name: 'Team' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    // The field block: who is out today, by name.
    const outToday = page.locator('section[aria-label="Out today"]')
    await expect(outToday).toBeVisible()
    await expect(outToday).toContainText(fixture.report_name)
    await expect(outToday).toContainText(fixture.leave_type)

    // The grid: that person's row, with a bar carrying the leave type and
    // no reason anywhere on it.
    const row = page.locator(
      `[data-testid="team-row"][data-employee="${fixture.report_name}"]`,
    )
    await expect(row).toBeVisible()
    await expect(row.getByText(fixture.leave_type, { exact: false })).toBeVisible()
    await expect(row).not.toContainText('Seeded by')

    // The legend, so the two tints are readable as words too.
    await expect(page.getByText('Waiting for a decision')).toBeVisible()
    // The footnote states the scope and that reasons are hidden.
    await expect(page.getByText(/Only the people who report to you/)).toBeVisible()
    await expect(page.getByText(/Reasons for leave aren't shown here/)).toBeVisible()
  })

  // P3-U9 regression. `empty` used to be "this week has no leave in it",
  // which collapsed the whole region on a quiet week -- taking the anchored
  // block with it. Neither half of that block is week-scoped: "out today" is
  // about today, and the waiting count is this manager's whole queue.
  test('a week with no leave still shows out today and the waiting queue', async ({
    page,
    baseURL,
  }) => {
    await seedTeamWeek(baseURL!)

    // The real payload with its leave taken out, so only the quiet week is
    // the test's invention and every other shape stays the server's.
    const response = await page.request.get('/api/method/helixhr.api.get_my_team_week')
    const real = (await response.json()).message
    await page.route('**/api/method/helixhr.api.get_my_team_week*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: {
            ...real,
            reports: real.reports.map((row: object) => ({ ...row, leaves: [] })),
            out_today: [],
            waiting_count: 2,
          },
        }),
      }),
    )

    await page.goto('/helixhr/team')
    await page.waitForLoadState('networkidle')

    // The block, its today-shaped sentence, and the decision link -- none of
    // which the week decides.
    const outToday = page.locator('section[aria-label="Out today"]')
    await expect(outToday).toBeVisible()
    await expect(outToday).toContainText('Everyone on your team is in today.')
    await expect(outToday.getByRole('link', { name: /2 requests still waiting/ })).toBeVisible()
    // And the quiet week says so rather than rendering a grid with no
    // explanation.
    await expect(page.getByText('Nobody on your team is booked off this week')).toBeVisible()
    await expect(page.locator('[data-testid="team-row"]').first()).toBeVisible()
  })

  test('the payload carries no leave reason', async ({ page, baseURL }) => {
    await seedTeamWeek(baseURL!)

    // P3-R21 and P3-AE11, asserted against the wire and not the screen: the
    // key does not exist, because the field is never selected.
    const response = await page.request.get('/api/method/helixhr.api.get_my_team_week')
    expect(response.ok(), await response.text()).toBeTruthy()
    const payload = (await response.json()).message

    const leaves = payload.reports.flatMap((row: { leaves: object[] }) => row.leaves)
    expect(leaves.length).toBeGreaterThan(0)
    for (const leave of leaves) {
      expect(leave).not.toHaveProperty('description')
      expect(leave).not.toHaveProperty('status')
    }
    for (const row of payload.out_today) {
      expect(row).not.toHaveProperty('description')
    }
  })

  test('the arrows move the week and This week comes back', async ({ page, baseURL }) => {
    await seedTeamWeek(baseURL!)

    await page.goto('/helixhr/team')
    await page.waitForLoadState('networkidle')

    const thisWeek = await weekLabel(page).textContent()
    expect(thisWeek?.trim()).toBeTruthy()
    // On this week there is nothing to go back to, so the link is absent.
    await expect(page.getByRole('button', { name: 'This week', exact: true })).toHaveCount(0)

    await page.getByRole('button', { name: 'Next week' }).click()
    await expect(weekLabel(page)).not.toHaveText(thisWeek!)
    await page.waitForLoadState('networkidle')
    // Another week cannot answer "who is out today", and says so instead of
    // claiming a full office. The field block is there whether or not that
    // week has any leave in it (P3-U9): the region is empty only when nobody
    // reports to this manager.
    await expect(page.locator('section[aria-label="Out today"]')).toContainText(
      'looking at another week',
    )

    await page.getByRole('button', { name: 'This week', exact: true }).click()
    await expect(weekLabel(page)).toHaveText(thisWeek!)
  })
})
