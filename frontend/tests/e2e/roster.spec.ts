import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// Plan 2026-09-30-001 U9 / U10. The roster in a real browser: the employee's
// own row, a manager's reports, week paging, the phone day list, and HR's
// assign / end / overlap flows. Seeded by `ensure_roster_fixtures` (via
// `setup_playwright_fixtures`, and `seed_roster_fixtures` around the HR
// writes so a rerun starts from the same week).

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const SHIFT = '_Test Portal Shift'
const COLLEAGUE = 'Directory Colleague'

async function seedRoster(baseURL: string) {
  const api: APIRequestContext = await request.newContext({
    baseURL,
    extraHTTPHeaders: { Host: SITE_HOST },
  })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  const response = await api.post('/api/method/helixhr.tests.utils.seed_roster_fixtures')
  expect(response.ok(), await response.text()).toBeTruthy()
  await api.dispose()
}

function weekLabel(page: Page) {
  return page.locator('h2.type-section').first()
}

function row(page: Page, name: string) {
  return page.locator(`[data-testid="roster-row"][data-employee="${name}"]`)
}

async function openRoster(page: Page) {
  await page.goto('/helixhr/roster')
  await expect(page.getByRole('heading', { level: 1, name: 'Roster' })).toBeVisible()
  await page.waitForLoadState('networkidle')
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'the desktop employee roster')
  })

  test('shows their own row with the shift name, and nothing to edit', async ({ page }) => {
    const response = await page.request.get('/api/method/helixhr.api.get_roster_week')
    const data = (await response.json()).message
    expect(data.rows).toHaveLength(1)
    expect(data.can_edit).toBe(false)
    const me = data.rows[0].employee_name

    await openRoster(page)
    // One mode, so no switcher.
    await expect(page.getByRole('group', { name: 'Whose shifts' })).toHaveCount(0)
    const mine = row(page, me)
    await expect(mine).toBeVisible()
    await expect(mine.getByText(SHIFT).first()).toBeVisible()
    // No edit affordances anywhere on the grid.
    await expect(page.locator('[data-testid="roster-cell"] button')).toHaveCount(0)
    await expect(page.locator('[data-testid="roster-row"]')).toHaveCount(1)
  })

  test('an empty week says so in a sentence, and no rows is an empty state', async ({ page }) => {
    const response = await page.request.get('/api/method/helixhr.api.get_roster_week')
    const real = (await response.json()).message
    const quiet = {
      ...real,
      rows: real.rows.map((r: { cells: object[] }) => ({
        ...r,
        default_shift: null,
        cells: r.cells.map((c: object) => ({ ...c, shift_type: null, start_time: null, end_time: null })),
      })),
    }
    await page.route('**/api/method/helixhr.api.get_roster_week*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: quiet }) }),
    )
    await openRoster(page)
    await expect(page.getByTestId('roster-quiet')).toContainText('No shifts are assigned this week')
    await expect(page.locator('[data-testid="roster-row"]')).toHaveCount(1)

    await page.unroute('**/api/method/helixhr.api.get_roster_week*')
    await page.route('**/api/method/helixhr.api.get_roster_week*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: { ...real, rows: [], total: 0 } }),
      }),
    )
    await page.getByRole('button', { name: 'Next week' }).click()
    await expect(page.getByText('Nobody to show on this roster')).toBeVisible()
  })
})

test.describe('manager', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'a manager with reports')
  })

  test('sees their reports, and the arrows change the cells', async ({ page }) => {
    await openRoster(page)
    await page.getByRole('button', { name: 'My team' }).click()
    await page.waitForLoadState('networkidle')

    const colleague = row(page, COLLEAGUE)
    await expect(colleague).toBeVisible()
    // Seeded for this week only.
    await expect(colleague.getByText(SHIFT)).toHaveCount(7)

    const label = await weekLabel(page).textContent()
    await page.getByRole('button', { name: 'Next week' }).click()
    await expect(weekLabel(page)).not.toHaveText(label!)
    await page.waitForLoadState('networkidle')
    await expect(colleague.getByText(SHIFT)).toHaveCount(0)

    await page.getByRole('button', { name: 'This week' }).click()
    await expect(weekLabel(page)).toHaveText(label!)
    await expect(colleague.getByText(SHIFT)).toHaveCount(7)
  })
})

test.describe('phone', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee-mobile-webkit', 'the 375px day list')
  })

  test('reads as a day list with no page-level horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await openRoster(page)
    await expect(page.getByTestId('roster-days')).toBeVisible()
    await expect(page.getByTestId('roster-day')).toHaveCount(7)
    await expect(page.getByTestId('roster-days').getByText(SHIFT).first()).toBeVisible()
    await expect(page.locator('[data-testid="roster-row"]').first()).toBeHidden()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
