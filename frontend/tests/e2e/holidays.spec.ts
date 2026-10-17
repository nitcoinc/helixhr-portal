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

/** A date-only string `days` away, by UTC string math. */
function shiftDays(date: string, days: number): string {
  const [year, month, day] = date.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10)
}

/** The weekday name of a date-only string, as the server spells it. */
function weekdayOf(date: string): string {
  const [year, month, day] = date.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString('en-US', {
    weekday: 'long',
    timeZone: 'UTC',
  })
}

/** The Coming up / Earlier row for one holiday, found by its name *and* its
 * date tile, so two holidays sharing a fixture name stay apart. */
function holidayRow(page: Page, description: string, date: string) {
  const [, month, day] = date.split('-')
  return page
    .getByRole('listitem')
    .filter({ hasText: description })
    .filter({ has: page.locator('.date-tile-month', { hasText: MONTHS[Number(month) - 1] }) })
    .filter({ has: page.locator('.date-tile-day', { hasText: new RegExp(`^${Number(day)}$`) }) })
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

    // U16 (EP-22): the field block names the next holiday and Coming up
    // starts from the one after it. On a fresh site the seeded holiday *is*
    // the next one, so it would only be in the field block -- the real
    // payload is re-served with one nearer holiday ahead of it, which keeps
    // the seeded row a real row and makes the no-duplicate rule testable on
    // every site. Today itself is free: if a real holiday were on it, the
    // seeded one would not be next.
    const response = await page.request.get('/api/method/helixhr.api.get_my_holidays')
    const real = (await response.json()).message
    const seededIsNext = real.next?.date === holiday
    const nearer = {
      date: today,
      description: '_Spec Nearer Holiday',
      weekday: weekdayOf(today),
      is_half_day: 0,
    }
    const next = seededIsNext
      ? { date: today, description: nearer.description, days_until: 0 }
      : real.next
    await page.route('**/api/method/helixhr.api.get_my_holidays*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: {
            ...real,
            holidays: seededIsNext ? [nearer, ...real.holidays] : real.holidays,
            next,
          },
        }),
      }),
    )

    await page.goto('/helixhr/holidays')
    await expect(page.getByRole('heading', { name: 'Holidays' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    // The fixture's own description and its own day, so this is that
    // holiday and not whichever one the site happens to carry.
    const row = holidayRow(page, '_Test Holiday', holiday)
    await expect(row).toHaveCount(1)
    await expect(row).toBeVisible()
    await expect(row.locator('.date-tile-month')).toHaveText(MONTHS[Number(month) - 1])
    await expect(row.locator('.date-tile-day')).toHaveText(String(Number(day)))

    // It is ahead of today, so it belongs to Coming up.
    await expect(page.getByRole('heading', { name: 'Coming up' })).toBeVisible()

    // The next holiday is said once: in the field block, not again as the
    // first Coming up row.
    const strip = page.locator('section[aria-label="Next holiday"]')
    await expect(strip).toContainText(next.description || 'Holiday')
    await expect(holidayRow(page, next.description || 'Holiday', next.date)).toHaveCount(0)
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

  // P3-U9 regression. The year chips used to live inside the async region,
  // so choosing a year with no holidays in it rendered the empty state --
  // which removed the only control that could get back out of that year.
  test('a year with no holidays keeps its year chips reachable', async ({ page }) => {
    const today = await siteToday(page)
    const current = Number(today.slice(0, 4))
    const quiet = current - 1

    // The real payload, re-served: the shape stays the server's and only
    // which year is empty is the test's.
    const response = await page.request.get('/api/method/helixhr.api.get_my_holidays')
    const real = (await response.json()).message
    await page.route('**/api/method/helixhr.api.get_my_holidays*', (route) => {
      // frappe-ui posts a resource's params as a JSON body, so the year is
      // in `postData` and not in the query string.
      const asked = Number(route.request().postDataJSON()?.year ?? current)
      const empty = asked === quiet
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: {
            ...real,
            known: true,
            year: asked,
            years: [current, quiet],
            holidays: empty ? [] : real.holidays,
            next: empty ? null : real.next,
          },
        }),
      })
    })

    await page.goto('/helixhr/holidays')
    await page.waitForLoadState('networkidle')

    const chips = page.getByRole('group', { name: 'Year' })
    await expect(chips.getByRole('button', { name: String(quiet) })).toBeVisible()

    await chips.getByRole('button', { name: String(quiet) }).click()
    await expect(page.getByText(`No holidays listed for ${quiet}`)).toBeVisible()
    // The way out is still on the page.
    await expect(chips.getByRole('button', { name: String(current) })).toBeVisible()

    await chips.getByRole('button', { name: String(current) }).click()
    await expect(page.getByText(`No holidays listed for ${quiet}`)).toHaveCount(0)
  })

  // P3-R11. No holiday list resolves -> the cannot-tell state, in the same
  // words the attendance-request preview uses (@/lib/holidays), never an
  // empty list. Stubbed rather than un-assigned: the company's assignment is
  // shared with every other suite on this site.
  test('no holiday list at all shows the cannot-tell state', async ({ page }) => {
    const year = Number((await siteToday(page)).slice(0, 4))
    await page.route('**/api/method/helixhr.api.get_my_holidays*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: {
            known: false,
            holiday_list: null,
            year,
            years: [year],
            holidays: [],
            next: null,
          },
        }),
      }),
    )

    await page.goto('/helixhr/holidays')
    await page.waitForLoadState('networkidle')

    await expect(page.getByText("We can't tell your holidays yet")).toBeVisible()
    await expect(
      page.getByText(
        "No holiday list is assigned to you or your company yet, so we can't tell which days are working days. Ask HR about your holiday list.",
      ),
    ).toBeVisible()
  })

  // U16 (EP-22). The days already gone fold behind "Show past", a real
  // disclosure -- and with none of them there is no toggle at all.
  test('past holidays fold behind Show past, which is absent when there are none', async ({
    page,
  }) => {
    const today = await siteToday(page)
    const year = Number(today.slice(0, 4))
    const holiday = (date: string, description: string) => ({
      date,
      description,
      weekday: weekdayOf(date),
      is_half_day: 0,
    })
    const ahead = [
      holiday(shiftDays(today, 5), '_Spec Soon'),
      holiday(shiftDays(today, 9), '_Spec Later'),
    ]
    const gone = [holiday(shiftDays(today, -20), '_Spec Gone')]
    let withPast = false
    await page.route('**/api/method/helixhr.api.get_my_holidays*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: {
            known: true,
            holiday_list: '_Test Holiday List',
            year,
            years: [year],
            holidays: withPast ? [...gone, ...ahead] : ahead,
            next: { date: ahead[0].date, description: ahead[0].description, days_until: 5 },
          },
        }),
      }),
    )

    // No past holidays: no toggle, no Earlier group.
    await page.goto('/helixhr/holidays')
    await page.waitForLoadState('networkidle')
    await expect(page.getByText('_Spec Later')).toBeVisible()
    await expect(page.getByRole('button', { name: /Show past/ })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: /^Earlier/ })).toHaveCount(0)

    // With one: folded, announced as collapsed, and it opens in place.
    withPast = true
    await page.reload()
    await page.waitForLoadState('networkidle')
    const toggle = page.getByRole('button', { name: 'Show past (1)' })
    await expect(toggle).toBeVisible()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    const panel = page.locator(`#${await toggle.getAttribute('aria-controls')}`)
    await expect(panel).toBeHidden()
    await expect(page.getByText('_Spec Gone')).toBeHidden()

    await toggle.click()
    const open = page.getByRole('button', { name: 'Hide past' })
    await expect(open).toHaveAttribute('aria-expanded', 'true')
    await expect(panel).toBeVisible()
    await expect(panel.getByText('_Spec Gone')).toBeVisible()
  })

  test('the footnote names the list and says weekly offs are not in it', async ({ page }) => {
    await page.goto('/helixhr/holidays')
    await page.waitForLoadState('networkidle')

    await expect(page.getByText('_Test Holiday List')).toBeVisible()
    await expect(page.getByText(/weekly days off aren't listed/)).toBeVisible()
  })
})
