import { test, expect, Page } from '@playwright/test'

// P3-U4 scenario 8 / P3-R5, P3-R6. The punch, in a real browser with a real
// geolocation permission.
//
// Chromium only, and the `employee` project only: `context.grantPermissions`
// and `setGeolocation` are CDP features, so the mobile WebKit project has no
// way to answer the permission prompt and would hang on the locating state
// rather than fail usefully.
//
// The suite runs against a long-lived site whose fixture employee may
// already have punched today, so nothing here asserts an absolute punch
// count or a fixed button label: the spec reads which punch is next, does
// it, and asserts the flip and the one new row.

const OFFICE = { latitude: 12.9716, longitude: 77.5946 }

/** The server's own today, not the runner's host clock (the site runs in
 * Asia/Kolkata and CI in UTC). */
async function siteToday(page: Page): Promise<string> {
  const response = await page.request.get('/api/method/helixhr.api.get_portal_bootstrap')
  return (await response.json()).message.today as string
}

async function punchCount(page: Page): Promise<number> {
  const today = await siteToday(page)
  const response = await page.request.get(
    `/api/method/helixhr.api.get_my_checkins?date=${today}`,
  )
  const body = await response.json()
  return (body.message || []).length
}

/** The strip's button, whichever punch is next. */
function stripButton(page: Page) {
  return page
    .locator('section[aria-label="This month"]')
    .getByRole('button', { name: /Check (in|out)/ })
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'Chromium employee-only scenarios')
  })

  test('a punch with location creates one row and flips the strip', async ({ page, baseURL }) => {
    const context = page.context()
    await context.grantPermissions(['geolocation'], { origin: baseURL! })
    await context.setGeolocation(OFFICE)

    await page.goto('/helixhr/attendance')
    await page.waitForLoadState('networkidle')

    const button = stripButton(page)
    await expect(button).toBeVisible()
    const before = (await button.textContent())!.trim()
    const expected = before === 'Check in' ? 'Check out' : 'Check in'
    const rowsBefore = await punchCount(page)

    await button.click()

    // The sheet asks for the location on open -- never on page load -- and
    // says so once it has one.
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('Location captured')).toBeVisible()
    // P3-KTD4's disclosure, in the sheet where the tap happens.
    await expect(dialog.getByText(/does not follow you between punches/)).toBeVisible()

    await dialog.getByRole('button', { name: before }).click()
    await expect(dialog).toBeHidden()

    // One new row, and the strip now offers the other punch.
    await expect(stripButton(page)).toHaveText(expected)
    expect(await punchCount(page)).toBe(rowsBefore + 1)
    await expect(
      page.locator('section[aria-label="This month"]').getByText(/location captured/),
    ).toBeVisible()
  })

  test('a denied permission records nothing and explains the browser setting', async ({
    browser,
  }) => {
    // A context that never grants geolocation: Playwright answers the
    // prompt with a denial, which is the same code path as a user who has
    // blocked the site (P3-R6).
    const context = await browser.newContext({ storageState: 'tests/.auth/employee.json' })
    const page = await context.newPage()
    await page.goto('/helixhr/attendance')
    await page.waitForLoadState('networkidle')

    const rowsBefore = await punchCount(page)
    await stripButton(page).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('Your browser is blocking location for this site')).toBeVisible()
    await expect(dialog.getByText('Nothing was recorded.')).toBeVisible()
    // The fallback, never a punch.
    await expect(dialog.getByRole('link', { name: 'Fix a day' })).toBeVisible()
    expect(await punchCount(page)).toBe(rowsBefore)

    // And no way to punch from this state.
    await expect(dialog.getByRole('button', { name: /^Check (in|out)$/ })).toHaveCount(0)

    await context.close()
  })
})
