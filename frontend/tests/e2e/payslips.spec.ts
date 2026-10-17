import { test, expect } from '@playwright/test'

// P3-U2 scenario 6 / P3-R1, P3-R2. The seeded payslip, in a real browser:
// the list, the detail behind its own URL, and the PDF as a real GET that
// answers with a PDF attachment rather than a page.
//
// The fixture (`helixhr.tests.utils.setup_playwright_fixtures`) seeds one
// submitted slip for last month, so nothing here assumes a period or an
// amount -- it reads the row the page drew.

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('the seeded payslip is listed, opens on its own URL, and its PDF downloads', async ({
    page,
  }) => {
    await page.goto('/helixhr/payslips')
    await expect(page.getByRole('heading', { name: 'Payslips' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    // The anchored field block: the latest slip's net pay, with a currency
    // symbol on it (P3-R1 -- never a bare number).
    const field = page.locator('section[aria-label="Latest payslip"]')
    await expect(field).toBeVisible()
    await expect(field).toContainText(/[$₹€£]|[A-Z]{3}/)

    // One row per slip, each linking to its own record.
    const rowLink = page.locator('a[href*="/helixhr/payslips/"]').first()
    await expect(rowLink).toBeVisible()

    // The detail is a route, so it survives a refresh (P2-KTD5).
    await rowLink.click()
    await expect(page).toHaveURL(/\/helixhr\/payslips\/.+/)
    const detailUrl = page.url()
    await expect(page.getByText('Net pay')).toBeVisible()
    await expect(page.getByRole('heading', { level: 3, name: 'Earnings' })).toBeVisible()

    await page.reload()
    await page.waitForLoadState('networkidle')
    expect(page.url()).toBe(detailUrl)
    await expect(page.getByText('Net pay')).toBeVisible()

    // The PDF is a plain link to the GET endpoint, and the endpoint answers
    // with a saved file: `application/pdf`, an attachment disposition and no
    // caching (P3-KTD2). Fetched through the page's own context, so it
    // carries the session the browser is signed in with.
    const pdfLink = page.getByRole('link', { name: 'Download PDF' }).first()
    const href = await pdfLink.getAttribute('href')
    expect(href).toContain('helixhr.api.download_my_payslip')

    const response = await page.request.get(href!)
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toContain('application/pdf')
    expect(response.headers()['content-disposition']).toContain('attachment')
    expect(response.headers()['cache-control']).toContain('no-store')
    expect((await response.body()).subarray(0, 4).toString()).toBe('%PDF')

    // Nobody else's slip, through the same endpoint (P3-R3). The name embeds
    // an employee id, so this is a slip that either does not exist or is not
    // theirs -- one answer for both.
    const foreign = await page.request.get(
      '/api/method/helixhr.api.download_my_payslip?name=' +
        encodeURIComponent('Sal Slip/HR-EMP-99999/00001'),
    )
    expect(foreign.status()).toBe(404)
  })
})

test.describe('employee on a phone', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('the same breakdown arrives as a bottom sheet', async ({ browser }) => {
    // The phone shape of one state, not a second screen: the sheet renders
    // the same `PayslipBreakdown` the desktop panel does (P3-U2 step 4).
    const context = await browser.newContext({
      storageState: 'tests/.auth/employee.json',
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    })
    const page = await context.newPage()
    await page.goto('/helixhr/payslips')
    await page.waitForLoadState('networkidle')

    await page.locator('a[href*="/helixhr/payslips/"]').first().click()
    const sheet = page.getByRole('dialog')
    await expect(sheet).toBeVisible()
    await expect(sheet.getByText('Net pay')).toBeVisible()
    await expect(sheet.getByRole('link', { name: 'Download PDF' })).toBeVisible()

    // Escape closes it and the URL goes back to the list, so Back is not a
    // dead end.
    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden()
    await expect(page).toHaveURL(/\/helixhr\/payslips$/)
    await context.close()
  })
})

// Plan 2026-10-17-001 U10 (ED-06). An older slip downloads from its own row,
// without opening the sheet, and a withheld slip offers no PDF. The fixture
// seeds one real slip; a withheld slip for the month after it is stubbed in
// front of it, which makes the real one the "older" row and keeps its PDF a
// real download.
test.describe('employee, per-row PDF', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('an older row has a 44px named PDF link that downloads in place; Withheld has none', async ({
    page,
  }) => {
    await page.route('**/api/method/helixhr.api.get_my_payslips*', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      const real = body.message.payslips[0]
      if (real?.end_date) {
        const [y, m] = real.end_date.split('-').map(Number)
        const next = new Date(Date.UTC(y, m, 1)) // first of the month after
        const iso = (d: Date) => d.toISOString().slice(0, 10)
        const end = new Date(Date.UTC(next.getUTCFullYear(), next.getUTCMonth() + 1, 0))
        body.message.payslips = [
          {
            ...real,
            name: 'Sal Slip/U10-WITHHELD/00001',
            start_date: iso(next),
            end_date: iso(end),
            status: 'Withheld',
            withheld: true,
            can_download: false,
            revised: false,
          },
          real,
        ]
        body.message.total = (body.message.total || 1) + 1
      }
      await route.fulfill({ response, body: JSON.stringify(body) })
    })

    await page.goto('/helixhr/payslips')
    await page.waitForLoadState('networkidle')

    const withheld = page.locator('li', { hasText: 'Withheld, ask HR' })
    await expect(withheld).toHaveCount(1)
    await expect(withheld.getByRole('link', { name: /Download .+ payslip/ })).toHaveCount(0)
    // The field block's PDF follows the latest slip, which is the withheld one.
    await expect(page.getByRole('link', { name: 'Download PDF' })).toHaveCount(0)

    const pdf = page.getByRole('link', { name: /Download .+ payslip/ })
    await expect(pdf).toHaveCount(1)
    const box = (await pdf.boundingBox())!
    expect(box.height, `the PDF link is ${box.height}px tall`).toBeGreaterThanOrEqual(44)
    expect(box.width, `the PDF link is ${box.width}px wide`).toBeGreaterThanOrEqual(44)

    const download = page.waitForEvent('download')
    await pdf.click()
    expect((await download).suggestedFilename()).toMatch(/\.pdf$/i)
    // Downloaded where it stood: no sheet, no detail route.
    await expect(page).toHaveURL(/\/helixhr\/payslips$/)
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })
})
