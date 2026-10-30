import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// Plan 2026-09-30-001 U9 / U10. The roster in a real browser: the employee's
// own row, a manager's reports, week paging, the phone day list, and HR's
// assign / end / overlap flows. Seeded by `ensure_roster_fixtures` (via
// `setup_playwright_fixtures`, and `seed_roster_fixtures` around the HR
// writes so a rerun starts from the same week).

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const SHIFT = '_Test Portal Shift'
const COLLEAGUE = 'Directory Colleague'
const EMPLOYEE_USER = 'employee@helixhr.test'

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
    // Plan 2026-10-31-001 U10: no reports, so no team mode and nothing to
    // propose -- and the server refuses the team read outright.
    expect(data.can_request).toBe(false)
    const team = await page.request.get('/api/method/helixhr.api.get_roster_week?mode=team')
    expect(team.ok()).toBe(false)
    const me = data.rows[0].employee_name

    await openRoster(page)
    // One mode, so no switcher.
    await expect(page.getByRole('group', { name: 'Whose shifts' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'My team' })).toHaveCount(0)
    await expect(page.getByTestId('roster-draft-bar')).toHaveCount(0)
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
    // Plan 2026-10-17-001 U17 (MH-17): your own empty week is one card --
    // no grid row and no seven "No shift" day cards -- at both widths.
    const card = page.getByTestId('roster-quiet')
    await expect(card).toHaveCount(1)
    await expect(card).toContainText('No shifts are assigned this week')
    await expect(page.locator('[data-testid="roster-row"]')).toHaveCount(0)
    await expect(page.getByTestId('roster-day')).toHaveCount(0)
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(card).toBeVisible()
    await expect(page.getByTestId('roster-day')).toHaveCount(0)

    await page.unroute('**/api/method/helixhr.api.get_roster_week*')
    await page.route('**/api/method/helixhr.api.get_roster_week*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: { ...real, rows: [], total: 0 } }),
      }),
    )
    // The card's own way out moves the week, the same as the arrow.
    const label = await weekLabel(page).textContent()
    await card.getByRole('button', { name: 'Go to next week' }).click()
    await expect(weekLabel(page)).not.toHaveText(label!)
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

// Plan 2026-10-31-001 U10. A manager drafts shift changes for their reports
// in the team view and sends them to HR; HR decides in Approvals. Every test
// reseeds (`seed_roster_fixtures` also deletes the manager's requests), works
// in next week -- tomorrow or later, as R13 requires -- and reseeds after.
test.describe('manager roster requests', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeEach(async ({ baseURL }, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'a manager proposes for their reports')
    await seedRoster(baseURL!)
  })

  test.afterAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name === 'manager') await seedRoster(baseURL!)
  })

  async function employeeName(baseURL: string) {
    const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
    await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
    const response = await api.get(
      '/api/method/frappe.client.get_value?doctype=Employee&fieldname=employee_name&filters=' +
        encodeURIComponent(JSON.stringify({ user_id: EMPLOYEE_USER })),
    )
    const name = (await response.json()).message.employee_name as string
    await api.dispose()
    return name
  }

  async function teamNextWeek(page: Page) {
    await openRoster(page)
    await page.getByRole('button', { name: 'My team' }).click()
    await page.waitForLoadState('networkidle')
    await page.getByRole('button', { name: 'Next week' }).click()
    await page.waitForLoadState('networkidle')
  }

  function cell(target: ReturnType<typeof row>, index: number) {
    return target.locator('[data-testid="roster-cell"]').nth(index)
  }

  async function latestRequest(page: Page) {
    const response = await page.request.get('/api/method/helixhr.api.get_roster_draft')
    return (await response.json()).message.recent[0] as { name: string; status: string }
  }

  test('proposes two changes, keeps them across a reload, sends them, and HR approves', async ({
    page,
    browser,
    baseURL,
  }) => {
    test.setTimeout(120000)
    const reportName = await employeeName(baseURL!)
    const mine = await page.request.get('/api/method/helixhr.api.get_roster_week')
    const me = (await mine.json()).message.rows[0].employee_name as string
    await teamNextWeek(page)

    const colleague = row(page, COLLEAGUE)
    const report = row(page, reportName)
    await expect(colleague.getByText(SHIFT)).toHaveCount(0)
    await expect(report.getByText(SHIFT)).toHaveCount(7)
    // The manager's own row takes no proposal.
    await expect(row(page, me).locator('[data-testid="roster-cell"] button')).toHaveCount(0)

    // 1: the colleague, Monday to Wednesday.
    await cell(colleague, 0).getByRole('button').click()
    const sheet = page.getByTestId('roster-sheet')
    await expect(sheet).toBeVisible()
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    const wednesday = await cell(colleague, 2).getAttribute('data-date')
    await sheet.getByLabel('To (optional)').fill(wednesday!)
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()
    const summary = page.getByTestId('roster-draft-summary')
    await expect(summary).toHaveText('1 change for 1 person')

    // 2: the report's shift ends on Thursday.
    await cell(report, 3).getByRole('button').click()
    await expect(sheet).toBeVisible()
    await sheet.getByRole('button', { name: 'End it' }).click()
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()
    await expect(summary).toHaveText('2 changes for 2 people')

    // Proposed days say so in words, in the accessible name too.
    const proposed = `Proposed: ${SHIFT}`
    await expect(colleague.getByRole('button', { name: new RegExp(proposed) })).toHaveCount(3)
    await expect(report.getByRole('button', { name: /Proposed: no shift/ })).toHaveCount(3)

    // A proposed day opens on its change, with Edit and Remove.
    await cell(colleague, 1).getByRole('button').click()
    await expect(sheet).toContainText(proposed)
    await expect(sheet.getByRole('button', { name: 'Remove' })).toBeVisible()
    await sheet.getByRole('button', { name: 'Close' }).click()
    await expect(sheet).toBeHidden()

    // The draft is the server's: a reload keeps it.
    await page.reload()
    await teamNextWeek(page)
    await expect(summary).toHaveText('2 changes for 2 people')
    await expect(colleague.getByRole('button', { name: new RegExp(proposed) })).toHaveCount(3)

    await page.getByRole('button', { name: 'Show changes' }).click()
    await expect(page.getByTestId('roster-draft-item')).toHaveCount(2)
    await page.getByLabel('Note to HR (optional)').fill('Stocktake cover')
    await page.getByTestId('roster-submit').click()
    await expect(page.getByTestId('toast').getByText('Sent to HR for approval')).toBeVisible()
    await expect(page.getByTestId('roster-draft-bar')).toHaveCount(0)
    const strip = page.getByTestId('roster-requests')
    await expect(strip.locator('[data-testid="roster-request"][data-status="Pending"]')).toBeVisible()
    await expect(colleague.getByTestId('roster-pending-pill')).toBeVisible()
    await expect(report.getByTestId('roster-pending-pill')).toBeVisible()
    // Waiting for HR: their cells take no new proposal.
    await expect(colleague.locator('[data-testid="roster-cell"] button')).toHaveCount(0)
    const sent = await latestRequest(page)
    expect(sent.status).toBe('Pending')

    // HR decides in Approvals, through the confirm.
    const hrContext = await browser.newContext({ storageState: 'tests/.auth/hr.json' })
    const hr = await hrContext.newPage()
    await hr.goto(`/helixhr/approvals/roster/${sent.name}`)
    const panel = hr.getByTestId('approval-detail')
    await expect(panel.getByTestId('roster-review')).toBeVisible({ timeout: 15000 })
    await expect(panel.getByTestId('roster-review-person')).toHaveCount(2)
    await expect(panel).toContainText('Stocktake cover')
    await panel.getByTestId('approve').click()
    const dialog = hr.getByRole('dialog')
    await expect(dialog).toContainText('Approve 2 changes')
    await dialog.getByTestId('accept-confirm').click()
    await expect.poll(async () => (await latestRequest(page)).status, { timeout: 15000 }).toBe('Approved')
    await hrContext.close()

    // The roster shows the new shifts.
    await page.reload()
    await teamNextWeek(page)
    for (const index of [0, 1, 2]) await expect(cell(colleague, index)).toContainText(SHIFT)
    for (const index of [3, 4, 5, 6]) await expect(cell(colleague, index)).not.toContainText(SHIFT)
    for (const index of [0, 1, 2, 3]) await expect(cell(report, index)).toContainText(SHIFT)
    for (const index of [4, 5, 6]) await expect(cell(report, index)).not.toContainText(SHIFT)
    await expect(page.getByTestId('roster-requests').locator('[data-testid="roster-request"][data-status="Approved"]')).toBeVisible()
    await expect(page.locator('[data-proposed]')).toHaveCount(0)
  })

  test('at phone width the sheet and the draft bar fit with no sideways scroll', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await teamNextWeek(page)
    const overflow = () =>
      page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

    await page.getByRole('button', { name: `Propose a change to ${COLLEAGUE}'s shift on Monday` }).click()
    const sheet = page.getByTestId('roster-sheet')
    await expect(sheet).toBeVisible()
    expect(await overflow()).toBeLessThanOrEqual(0)
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()

    const bar = page.getByTestId('roster-draft-bar')
    await expect(bar).toBeVisible()
    await expect(page.getByTestId('roster-draft-summary')).toHaveText('1 change for 1 person')
    await expect(page.getByRole('button', { name: new RegExp(`Proposed: ${SHIFT}`) }).first()).toBeVisible()
    await bar.getByRole('button', { name: 'Show changes' }).click()
    expect(await overflow()).toBeLessThanOrEqual(0)

    // Discard asks once, then empties the draft.
    await page.getByTestId('roster-discard').click()
    await expect(bar).toContainText("This can't be undone")
    await page.getByTestId('roster-discard').click()
    await expect(bar).toHaveCount(0)
  })
})
