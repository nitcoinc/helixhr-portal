import { test, expect, Page } from '@playwright/test'

// P3-U3 scenario 5 / P3-R10. The Holidays page in a real browser: the seeded
// holiday on its date tile, and a countdown that agrees with the *site's*
// today rather than the runner's clock (the site runs in Asia/Kolkata and CI
// in UTC).
//
// Chromium `employee` project only: it is one employee's own holiday list,
// and the mobile WebKit project deliberately runs read-shaped critical flows
// only.

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

/** The server's own today, not the host clock. */
async function siteToday(page: Page): Promise<string> {
  const response = await page.request.get('/api/method/helixhr.api.get_portal_bootstrap')
  return (await response.json()).message.today as string
}

/** The date `helixhr.tests.utils.playwright_holiday_date` seeds: twelve days
 * ahead of the site's today, clamped inside the calendar year. String date
 * math, so no host timezone gets a vote. */
function seededHoliday(today: string): string {
  const [year, month, day] = today.split('-').map(Number)
  const ahead = new Date(Date.UTC(year, month - 1, day + 12))
  const clamped = new Date(Math.min(ahead.getTime(), Date.UTC(year, 11, 31)))
  return clamped.toISOString().slice(0, 10)
}

/** Whole days between two calendar dates, both date-only strings. */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split('-').map(Number)
  const [ty, tm, td] = to.split('-').map(Number)
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000)
}

/** The page's own countdown wording, restated here so the assertion is
 * about what the employee reads. */
function countdown(days: number): string {
  if (days <= 0) return 'Today'
  if (days === 1) return 'Tomorrow'
  return `in ${days} days`
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'Chromium employee-only scenarios')
  })

  test('the seeded holiday is listed with its tile', async ({ page }) => {
    const today = await siteToday(page)
    const holiday = seededHoliday(today)
    const [, month, day] = holiday.split('-')

    await page.goto('/helixhr/holidays')
    await expect(page.getByRole('heading', { name: 'Holidays' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    // The fixture's own description, so this is that holiday and not
    // whichever one the site happens to carry.
    const row = page.getByRole('listitem').filter({ hasText: '_Test Holiday' }).first()
    await expect(row).toBeVisible()
    await expect(row.locator('.date-tile-month')).toHaveText(MONTHS[Number(month) - 1])
    await expect(row.locator('.date-tile-day')).toHaveText(String(Number(day)))

    // It is ahead of today, so it belongs to Coming up.
    await expect(page.getByRole('heading', { name: 'Coming up' })).toBeVisible()
  })

  test("the countdown agrees with the site's today", async ({ page }) => {
    const today = await siteToday(page)

    // Which holiday is next is a property of the site's data (a long-lived
    // site may carry a nearer one than the fixture's), so the date comes
    // from the server and the *wording* is computed here from the site's
    // today. That is the thing under test: the strip must not count from
    // the browser's clock.
    const response = await page.request.get('/api/method/helixhr.api.get_my_holidays')
    const payload = (await response.json()).message
    expect(payload.known).toBeTruthy()
    expect(payload.next).not.toBeNull()

    await page.goto('/helixhr/holidays')
    await page.waitForLoadState('networkidle')

    const strip = page.locator('section[aria-label="Next holiday"]')
    await expect(strip).toBeVisible()
    await expect(strip).toContainText(countdown(daysBetween(today, payload.next.date)))
  })

  test('the footnote names the list and says weekly offs are not in it', async ({ page }) => {
    await page.goto('/helixhr/holidays')
    await page.waitForLoadState('networkidle')

    await expect(page.getByText('_Test Holiday List')).toBeVisible()
    await expect(page.getByText(/weekly days off aren't listed/)).toBeVisible()
  })
})
