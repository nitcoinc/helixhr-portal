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

test.describe('hr', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeEach(async ({ baseURL }, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'HR edits the roster')
    await seedRoster(baseURL!)
  })

  test.afterAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name === 'hr') await seedRoster(baseURL!)
  })

  async function colleagueNextWeek(page: Page) {
    await openRoster(page)
    await page.getByRole('button', { name: 'Everyone' }).click()
    await page.getByLabel('Search').fill(COLLEAGUE)
    await page.waitForLoadState('networkidle')
    await expect(row(page, COLLEAGUE)).toBeVisible()
    await page.getByRole('button', { name: 'Next week' }).click()
    await page.waitForLoadState('networkidle')
    await expect(row(page, COLLEAGUE).getByText(SHIFT)).toHaveCount(0)
    return row(page, COLLEAGUE)
  }

  function cellButton(target: ReturnType<typeof row>, index: number) {
    return target.locator('[data-testid="roster-cell"]').nth(index).getByRole('button')
  }

  test('assigns next week from the keyboard, then ends it mid-week', async ({ page }) => {
    const colleague = await colleagueNextWeek(page)

    // Keyboard: focus Monday's cell, open with Enter, set the end date,
    // submit with Enter from the form.
    const monday = cellButton(colleague, 0)
    await monday.focus()
    await page.keyboard.press('Enter')
    const sheet = page.getByTestId('roster-sheet')
    await expect(sheet).toBeVisible()
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    const sunday = await colleague.locator('[data-testid="roster-cell"]').nth(6).getAttribute('data-date')
    await sheet.getByLabel('To (optional)').fill(sunday!)
    await sheet.getByLabel('To (optional)').press('Enter')
    await expect(sheet).toBeHidden()
    await expect(colleague.getByText(SHIFT)).toHaveCount(7)
    // Focus comes back to the cell it was opened from.
    await expect(cellButton(colleague, 0)).toBeFocused()

    // End it on Wednesday: Thursday to Sunday clear.
    await cellButton(colleague, 2).click()
    await expect(sheet).toBeVisible()
    await sheet.getByRole('button', { name: 'End it' }).click()
    await sheet.getByRole('button', { name: 'End shift' }).click()
    await expect(sheet).toBeHidden()
    await expect(colleague.getByText(SHIFT)).toHaveCount(3)
    for (const index of [3, 4, 5, 6]) {
      await expect(colleague.locator('[data-testid="roster-cell"]').nth(index)).not.toContainText(SHIFT)
    }
  })

  test('an overlapping assign shows the plain overlap sentence', async ({ page }) => {
    const colleague = await colleagueNextWeek(page)
    // Monday to Wednesday next week...
    await cellButton(colleague, 0).click()
    const sheet = page.getByTestId('roster-sheet')
    const wednesday = await colleague.locator('[data-testid="roster-cell"]').nth(2).getAttribute('data-date')
    await sheet.getByLabel('To (optional)').fill(wednesday!)
    await sheet.getByRole('button', { name: 'Assign shift' }).click()
    await expect(sheet).toBeHidden()
    await expect(colleague.getByText(SHIFT)).toHaveCount(3)

    // ...then Thursday's cell, moved back to Tuesday: overlaps.
    await cellButton(colleague, 3).click()
    await expect(sheet).toBeVisible()
    const tuesday = await colleague.locator('[data-testid="roster-cell"]').nth(1).getAttribute('data-date')
    await sheet.getByLabel('From').fill(tuesday!)
    await sheet.getByRole('button', { name: 'Assign shift' }).click()
    await expect(sheet.getByRole('alert')).toHaveText(
      'This person already has a shift on some of those dates. End or change that one first.',
    )
  })
})
