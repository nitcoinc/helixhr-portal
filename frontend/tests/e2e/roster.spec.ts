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

    // A proposed day opens on its change, with the whole line's Edit and
    // Remove (plan 2026-11-06-002 U5).
    await cell(colleague, 1).getByRole('button').click()
    await expect(sheet).toContainText(proposed)
    await expect(sheet.getByRole('button', { name: 'Remove whole line' })).toBeVisible()
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
    // R10: the change count and the shifts it writes (the End creates none).
    await expect(panel.getByTestId('roster-review-count')).toHaveText('2 changes → 1 shift')
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

  // Plan 2026-11-03-001 U4. The colleague's holiday list has Saturday and
  // Sunday as weekly offs (`ensure_roster_fixtures`).
  function addDays(iso: string, days: number) {
    const date = new Date(`${iso}T00:00:00Z`)
    date.setUTCDate(date.getUTCDate() + days)
    return date.toISOString().slice(0, 10)
  }

  test('a two-week assign skips the weekend, edits from the bar keeping the skip, and clears', async ({
    page,
  }) => {
    await teamNextWeek(page)
    const colleague = row(page, COLLEAGUE)

    await cell(colleague, 0).getByRole('button').click()
    const sheet = page.getByTestId('roster-sheet')
    // The sheet's own Monday: the grid's dates may still be last week's.
    const monday = await sheet.getByLabel('From').inputValue()
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    await sheet.getByLabel('To (optional)').fill(addDays(monday, 13))
    // Plan 2026-11-06-002 U4: two skips, both on for a new ranged Assign.
    const weekends = sheet.getByTestId('roster-skip-weekends')
    const holidays = sheet.getByTestId('roster-skip-holidays')
    await expect(weekends).toBeChecked()
    await expect(holidays).toBeChecked()
    await expect(sheet.getByTestId('roster-skip-summary')).toContainText('run of working days')
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()

    const bar = page.getByTestId('roster-draft-bar')
    await expect(page.getByTestId('roster-draft-summary')).toHaveText('1 change for 1 person')
    await bar.getByRole('button', { name: 'Show changes' }).click()
    await expect(bar.getByTestId('roster-draft-blocks')).toContainText('2 shifts')
    for (const index of [0, 1, 2, 3, 4]) await expect(cell(colleague, index)).toContainText(`Proposed: ${SHIFT}`)
    for (const index of [5, 6]) {
      await expect(cell(colleague, index).getByTestId('roster-off')).toHaveText('Off')
      await expect(cell(colleague, index)).not.toContainText('Proposed')
    }

    // Edit from the bar: no "Apply to", the skip is kept, focus comes back.
    const edit = bar.getByRole('button', { name: new RegExp(`^Edit change 1: ${COLLEAGUE}`) })
    await edit.click()
    await expect(sheet).toBeVisible()
    await expect(sheet.getByRole('group', { name: 'Apply to' })).toHaveCount(0)
    await expect(weekends).toBeChecked()
    await expect(holidays).toBeChecked()
    await expect(sheet.getByTestId('roster-skip-summary')).toContainText('Saved as 2 shifts')
    await sheet.getByLabel('To (optional)').fill(addDays(monday, 8))
    await sheet.getByRole('button', { name: 'Save change' }).click()
    await expect(sheet).toBeHidden()
    await expect(edit).toBeFocused()
    await expect(page.getByTestId('roster-draft-item')).toHaveCount(1)
    await expect(bar.getByTestId('roster-draft-blocks')).toContainText('2 shifts')
    await expect(page.getByTestId('roster-draft-skip')).toHaveText('skips weekends and holidays')

    // Clear all asks once, then empties the draft.
    await page.getByTestId('roster-discard').click()
    await expect(bar).toContainText("Clear all 1 change for 1 person? This can't be undone.")
    await page.getByTestId('roster-discard').click()
    await expect(bar).toHaveCount(0)
  })

  test('applies one change to picked people and names who was skipped', async ({ page, baseURL }) => {
    const reportName = await employeeName(baseURL!)
    await teamNextWeek(page)
    const colleague = row(page, COLLEAGUE)

    await cell(colleague, 0).getByRole('button').click()
    const sheet = page.getByTestId('roster-sheet')
    const wednesday = addDays(await sheet.getByLabel('From').inputValue(), 2)
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    await sheet.getByLabel('To (optional)').fill(wednesday)
    await sheet.getByTestId('roster-apply-pick').click()
    const picker = sheet.getByTestId('roster-people-picker')
    await picker.getByLabel(COLLEAGUE, { exact: true }).check()
    await picker.getByLabel(reportName, { exact: true }).check()
    await expect(picker.getByTestId('roster-pick-count')).toHaveText('2 selected')

    // A12: a team bigger than the loaded page points to "Whole team" -- not
    // for this small team, and not while searching. Stubbed: seeding 51
    // reports is heavy.
    const hint = picker.getByTestId('roster-pick-whole-team-hint')
    await expect(hint).toHaveCount(0)
    await page.route('**/api/method/helixhr.api.get_roster_week*', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      await route.fulfill({ response, json: { ...body, message: { ...body.message, total: 120 } } })
    })
    const search = picker.getByLabel('Find a report')
    await search.fill('zz')
    await expect(picker.getByText('Nobody matches that search.')).toBeVisible()
    await expect(hint).toHaveCount(0)
    await search.fill('')
    await expect(hint).toContainText('of 120. To change everyone, choose Whole team above.')
    // Typing a query hides the shown hint; clearing it brings it back.
    await search.fill(COLLEAGUE.slice(0, 3))
    await expect(hint).toHaveCount(0)
    await search.fill('')
    await expect(hint).toContainText('of 120. To change everyone, choose Whole team above.')
    await page.unroute('**/api/method/helixhr.api.get_roster_week*')
    await search.fill('zz')
    await expect(hint).toHaveCount(0)
    await search.fill('')
    await expect(hint).toHaveCount(0)
    await sheet.getByRole('button', { name: 'Add for 2 people' }).click()
    await expect(sheet).toBeHidden()

    // The report already has an open-ended shift, so the assign overlaps.
    const notice = page.getByTestId('roster-bulk-notice')
    await expect(notice).toHaveAttribute('role', 'status')
    await expect(notice).toContainText('1 change added; 1 person skipped')
    await expect(notice).toContainText(reportName)
    await expect(page.getByTestId('roster-draft-summary')).toHaveText('1 change for 1 person')
    await notice.getByRole('button', { name: 'Dismiss' }).click()
    await expect(notice).toHaveCount(0)
  })

  // Plan 2026-11-05-001 U5. One-day changes on the report's open-ended
  // shift, and a one-day shift for the colleague, who has none next week.
  // Swap Day needs a second shift; its name sorts after SHIFT so every
  // sheet still opens on SHIFT.
  const SWING = '_Test Portal Swing Shift'
  async function ensureSwingShift(baseURL: string) {
    const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
    await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
    const found = await api.get(
      '/api/method/frappe.client.get_count?doctype=Shift%20Type&filters=' +
        encodeURIComponent(JSON.stringify({ name: SWING })),
    )
    if (!(await found.json()).message) {
      const made = await api.post('/api/method/frappe.client.insert', {
        data: {
          doc: JSON.stringify({ doctype: 'Shift Type', __newname: SWING, start_time: '14:00:00', end_time: '22:00:00' }),
        },
      })
      expect(made.ok(), await made.text()).toBeTruthy()
    }
    await api.dispose()
  }

  async function draftRows(page: Page) {
    const response = await page.request.get('/api/method/helixhr.api.get_roster_draft')
    return ((await response.json()).message.draft?.rows || []) as {
      action: string
      shift_type: string | null
      from_date: string | null
      to_date: string | null
      skip_off_days: number
      skip_weekends: number
      skip_holidays: number
    }[]
  }

  test('one-day changes: no shift one day, a different shift another, and the rest untouched', async ({
    page,
    baseURL,
  }) => {
    await ensureSwingShift(baseURL!)
    const reportName = await employeeName(baseURL!)
    await teamNextWeek(page)
    const report = row(page, reportName)
    await expect(report.getByText(SHIFT)).toHaveCount(7)
    const sheet = page.getByTestId('roster-sheet')

    // Wednesday: no shift that day, from the keyboard.
    await cell(report, 2).getByRole('button').click()
    await expect(sheet).toBeVisible()
    const chip = sheet.getByRole('button', { name: 'No shift that day' })
    await chip.focus()
    await page.keyboard.press('Enter')
    await expect(chip).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('#roster-shift-type')).toHaveCount(0)
    const wednesday = await sheet.getByLabel(/^Day/).inputValue()
    await expect(sheet).toContainText('The usual shift continues the next day.')
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()
    await expect(page.getByTestId('roster-draft-summary')).toHaveText('1 change for 1 person')
    await expect(cell(report, 2)).toContainText('Proposed: no shift')
    for (const index of [0, 1, 3, 4, 5, 6]) await expect(cell(report, index)).not.toContainText('Proposed')

    // Friday: a second one-day change on the same row, a different shift.
    await cell(report, 4).getByRole('button').click()
    await expect(sheet).toBeVisible()
    await sheet.getByRole('button', { name: 'Different shift for one day' }).click()
    await page.locator('#roster-shift-type').selectOption(SWING)
    const friday = await sheet.getByLabel(/^Day/).inputValue()
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()
    await expect(page.getByTestId('roster-draft-summary')).toHaveText('2 changes for 1 person')
    await expect(cell(report, 4)).toContainText(`Proposed: ${SWING}`)
    await expect(cell(report, 2)).toContainText('Proposed: no shift')
    for (const index of [0, 1, 3, 5, 6]) await expect(cell(report, index)).not.toContainText('Proposed')

    const rows = await draftRows(page)
    expect(rows.map((r) => [r.action, r.shift_type || null, r.from_date])).toEqual([
      ['Clear Day', null, wednesday],
      ['Swap Day', SWING, friday],
    ])
    const bar = page.getByTestId('roster-draft-bar')
    await bar.getByRole('button', { name: 'Show changes' }).click()
    const items = page.getByTestId('roster-draft-item')
    await expect(items.nth(0)).toContainText(/No shift on \S/)
    await expect(items.nth(1)).toContainText(new RegExp(`${SWING} on .+ only`))

    await page.getByTestId('roster-submit').click()
    await expect(page.getByTestId('toast').getByText('Sent to HR for approval')).toBeVisible()
    expect((await latestRequest(page)).status).toBe('Pending')
  })

  test('editing a saved no-shift day keeps it a no-shift day', async ({ page, baseURL }) => {
    const reportName = await employeeName(baseURL!)
    await teamNextWeek(page)
    const report = row(page, reportName)
    const sheet = page.getByTestId('roster-sheet')

    await cell(report, 2).getByRole('button').click()
    await sheet.getByRole('button', { name: 'No shift that day' }).click()
    const wednesday = await sheet.getByLabel(/^Day/).inputValue()
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()

    const bar = page.getByTestId('roster-draft-bar')
    await bar.getByRole('button', { name: 'Show changes' }).click()
    await bar.getByRole('button', { name: new RegExp(`^Edit change 1: ${reportName}, No shift on`) }).click()
    await expect(sheet).toBeVisible()
    await expect(sheet.getByRole('button', { name: 'No shift that day' })).toHaveAttribute('aria-pressed', 'true')
    await expect(sheet.getByLabel(/^Day/)).toHaveValue(wednesday)
    const thursday = addDays(wednesday, 1)
    await sheet.getByLabel(/^Day/).fill(thursday)
    await sheet.getByRole('button', { name: 'Save change' }).click()
    await expect(sheet).toBeHidden()

    const rows = await draftRows(page)
    expect(rows.map((r) => [r.action, r.from_date])).toEqual([['Clear Day', thursday]])
    await expect(page.getByTestId('roster-draft-item')).toHaveCount(1)
    await expect(page.getByTestId('roster-draft-item')).toContainText('No shift on')
    await expect(cell(report, 3)).toContainText('Proposed: no shift')
    await expect(cell(report, 2)).not.toContainText('Proposed')
  })

  test('an empty day takes a one-day shift with no skip', async ({ page }) => {
    await teamNextWeek(page)
    const colleague = row(page, COLLEAGUE)
    const sheet = page.getByTestId('roster-sheet')

    await cell(colleague, 1).getByRole('button').click()
    await expect(sheet).toBeVisible()
    const tuesday = await sheet.getByLabel('From').inputValue()
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    await sheet.getByLabel('To (optional)').fill(addDays(tuesday, 3))
    await expect(sheet.getByTestId('roster-skip-weekends')).toBeVisible()

    const oneDay = sheet.getByTestId('roster-one-day')
    await oneDay.check()
    await expect(oneDay).toHaveAttribute('aria-checked', 'true')
    await expect(sheet.getByLabel('To (optional)')).toHaveCount(0)
    await expect(sheet.getByTestId('roster-skip-weekends')).toHaveCount(0)
    await expect(sheet.getByTestId('roster-skip-holidays')).toHaveCount(0)
    await expect(sheet).toContainText('One day only. The shift ends the same day.')
    // Off again: To comes back empty.
    await oneDay.uncheck()
    await expect(sheet.getByLabel('To (optional)')).toHaveValue('')
    await oneDay.check()
    await expect(sheet.getByLabel(/^Day/)).toHaveValue(tuesday)
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()

    const rows = await draftRows(page)
    expect(rows.map((r) => [r.action, r.from_date, r.to_date, r.skip_weekends, r.skip_holidays])).toEqual([
      ['Assign', tuesday, tuesday, 0, 0],
    ])
    await expect(cell(colleague, 1)).toContainText(`Proposed: ${SHIFT}`)
    for (const index of [0, 2, 3]) await expect(cell(colleague, index)).not.toContainText('Proposed')
  })

  // Plan 2026-11-06-002 U4: each skip on its own, each with its own help.
  test('a ranged assign shows two skips; unticking weekends saves holidays only', async ({ page }) => {
    await teamNextWeek(page)
    const colleague = row(page, COLLEAGUE)
    await cell(colleague, 0).getByRole('button').click()
    const sheet = page.getByTestId('roster-sheet')
    const monday = await sheet.getByLabel('From').inputValue()
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    await sheet.getByLabel('To (optional)').fill(addDays(monday, 13))
    const weekends = sheet.getByRole('switch', { name: 'Skip weekends' })
    const holidays = sheet.getByRole('switch', { name: 'Skip holidays' })
    await expect(weekends).toBeChecked()
    await expect(holidays).toBeChecked()
    await expect(weekends).toHaveAccessibleDescription('Saturday and Sunday, unless their holiday list sets weekly offs')
    await expect(holidays).toHaveAccessibleDescription('Public holidays on their holiday list')
    await weekends.uncheck()
    await expect(holidays).toBeChecked()
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()

    const rows = await draftRows(page)
    expect(rows.map((r) => [r.action, r.skip_weekends, r.skip_holidays, r.skip_off_days])).toEqual([
      ['Assign', 0, 1, 0],
    ])
    await page.getByTestId('roster-draft-bar').getByRole('button', { name: 'Show changes' }).click()
    await expect(page.getByTestId('roster-draft-skip')).toHaveText('skips holidays')
    // The weekend is worked: Saturday is proposed too.
    await expect(cell(colleague, 5)).toContainText(`Proposed: ${SHIFT}`)
    // An edit keeps the row's own ticks, and a re-save keeps weekends-only
    // rows from collapsing (a note save re-sends every row).
    await page.getByLabel('Note to HR (optional)').fill('Cover')
    await page.getByLabel('Note to HR (optional)').blur()
    await expect.poll(async () => (await draftRows(page)).map((r) => [r.skip_weekends, r.skip_holidays])).toEqual([[0, 1]])
    await page.getByRole('button', { name: new RegExp(`^Edit change 1: ${COLLEAGUE}`) }).click()
    await expect(weekends).not.toBeChecked()
    await expect(holidays).toBeChecked()
  })

  // Plan 2026-11-06-002 U5: the user's case -- a drafted fortnight, then one
  // day changed on its own before sending to HR.
  test('a day of a drafted line takes its own change; whole-line edit and remove carry it', async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(90000)
    await ensureSwingShift(baseURL!)
    await teamNextWeek(page)
    const colleague = row(page, COLLEAGUE)
    const sheet = page.getByTestId('roster-sheet')

    await cell(colleague, 0).getByRole('button').click()
    const monday = await sheet.getByLabel('From').inputValue()
    await page.locator('#roster-shift-type').selectOption(SHIFT)
    await sheet.getByLabel('To (optional)').fill(addDays(monday, 13))
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()
    for (const index of [0, 1, 2, 3, 4]) await expect(cell(colleague, index)).toContainText(`Proposed: ${SHIFT}`)
    const wednesday = addDays(monday, 2)
    const thursday = addDays(monday, 3)

    // Wednesday: the day's own actions next to the whole line's.
    const wed = cell(colleague, 2).getByRole('button')
    await wed.click()
    await expect(sheet).toBeVisible()
    for (const name of ['Different shift for one day', 'No shift that day', 'Edit whole line', 'Remove whole line']) {
      await expect(sheet.getByRole('button', { name, exact: true })).toBeVisible()
    }
    // One press, and only that day changes.
    await sheet.getByRole('button', { name: 'No shift that day', exact: true }).click()
    await expect(sheet).toBeHidden()
    await expect(wed).toBeFocused()
    await expect(cell(colleague, 2)).toContainText('Proposed: no shift')
    for (const index of [0, 1, 3, 4]) await expect(cell(colleague, index)).toContainText(`Proposed: ${SHIFT}`)
    let rows = await draftRows(page)
    expect(rows.map((r) => [r.action, r.from_date, r.to_date])).toEqual([
      ['Assign', monday, addDays(monday, 13)],
      ['Clear Day', wednesday, null],
    ])

    // The changed day opens on its own change; Remove puts the line's day back.
    await wed.click()
    await expect(sheet.getByRole('button', { name: 'Edit', exact: true })).toBeVisible()
    await expect(sheet.getByRole('button', { name: 'Edit whole line' })).toBeVisible()
    await expect(sheet.getByTestId('roster-also-removes')).toContainText('no shift on')
    await sheet.getByRole('button', { name: 'Remove', exact: true }).click()
    await expect(sheet).toBeHidden()
    await expect(cell(colleague, 2)).toContainText(`Proposed: ${SHIFT}`)
    expect((await draftRows(page)).map((r) => r.action)).toEqual(['Assign'])

    // Thursday: a different shift for that day. Back returns to the summary;
    // the picker never starts on the line's own shift.
    const thu = cell(colleague, 3).getByRole('button')
    await thu.click()
    await sheet.getByRole('button', { name: 'Different shift for one day', exact: true }).click()
    const picker = page.locator('#roster-shift-type')
    await expect(picker).toBeFocused()
    await expect(picker).not.toHaveValue(SHIFT)
    await expect(sheet.getByTestId('roster-fixed-day')).toBeVisible()
    await expect(sheet.getByRole('group', { name: 'Apply to' })).toHaveCount(0)
    await sheet.getByRole('button', { name: 'Back' }).click()
    await expect(sheet.getByRole('button', { name: 'Remove whole line' })).toBeVisible()
    await sheet.getByRole('button', { name: 'Different shift for one day', exact: true }).click()
    await picker.selectOption(SWING)
    await sheet.getByRole('button', { name: 'Add to request' }).click()
    await expect(sheet).toBeHidden()
    await expect(cell(colleague, 3)).toContainText(`Proposed: ${SWING}`)
    await wed.click()
    await sheet.getByRole('button', { name: 'No shift that day', exact: true }).click()
    await expect(sheet).toBeHidden()
    rows = await draftRows(page)
    expect(rows.map((r) => [r.action, r.shift_type || null, r.from_date])).toEqual([
      ['Assign', SHIFT, monday],
      ['Swap Day', SWING, thursday],
      ['Clear Day', null, wednesday],
    ])

    // Edit whole line to end Tuesday: refused here, naming a day, nothing saved.
    await cell(colleague, 1).getByRole('button').click()
    await sheet.getByRole('button', { name: 'Edit whole line' }).click()
    await sheet.getByLabel('To (optional)').fill(addDays(monday, 1))
    await sheet.getByRole('button', { name: 'Save change' }).click()
    await expect(sheet.getByRole('alert')).toHaveText(/^Remove the one-day change on .+ first\.$/)
    await expect(sheet.getByRole('button', { name: 'Save change' })).toBeDisabled()
    expect((await draftRows(page)).map((r) => r.to_date)).toEqual([addDays(monday, 13), null, null])
    await sheet.getByRole('button', { name: 'Close' }).click()
    await expect(sheet).toBeHidden()

    // Remove whole line: the line and both of its day changes, in one save.
    await cell(colleague, 1).getByRole('button').click()
    const also = sheet.getByTestId('roster-also-removes')
    await expect(also).toContainText('no shift on')
    await expect(also).toContainText(`${SWING} on`)
    await sheet.getByRole('button', { name: 'Remove whole line' }).click()
    await expect(sheet).toBeHidden()
    await expect(page.getByTestId('roster-draft-bar')).toHaveCount(0)
    expect(await draftRows(page)).toEqual([])
    await expect(page.locator('[data-proposed]')).toHaveCount(0)
  })

  test('at phone width the four actions on a shift all show', async ({ page, baseURL }) => {
    const reportName = await employeeName(baseURL!)
    await page.setViewportSize({ width: 375, height: 812 })
    await teamNextWeek(page)
    await page.getByRole('button', { name: `Propose a change to ${reportName}'s shift on Monday` }).click()
    const sheet = page.getByTestId('roster-sheet')
    await expect(sheet).toBeVisible()
    const group = sheet.getByRole('group', { name: new RegExp(`^What to do with ${reportName}'s shift on `) })
    for (const name of ['Change shift', 'Different shift for one day', 'No shift that day', 'End it']) {
      await expect(group.getByRole('button', { name })).toBeInViewport({ ratio: 1 })
    }
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
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

    // Clear all asks once, then empties the draft.
    await page.getByTestId('roster-discard').click()
    await expect(bar).toContainText("This can't be undone")
    await page.getByTestId('roster-discard').click()
    await expect(bar).toHaveCount(0)
  })
})
