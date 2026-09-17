import { test, expect } from '@playwright/test'

// P6-U6 / P6-R9, P6-R10, P6-R11, P6-R12. A curated launcher into Frappe's
// own reports. `get_report_link` is the server's own gate -- the same
// posture organisation.spec.ts and people.spec.ts already take.
const COLLEAGUE_NAME = 'Manager'

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'reports is an HR-only screen')
  })

  test('an HR identity sees the curated list and every entry opens Frappe in a new tab', async ({
    page,
    context,
  }) => {
    await page.goto('/helixhr/')
    await expect(page.getByRole('link', { name: 'Reports' })).toBeVisible()

    await page.goto('/helixhr/reports')
    await expect(page.getByRole('heading', { name: 'Reports' })).toBeVisible()
    const entry = page.getByRole('button', { name: 'Leave balance' }).first()
    await expect(entry).toBeVisible()

    const [popup] = await Promise.all([context.waitForEvent('page'), entry.click()])
    await popup.waitForLoadState('domcontentloaded')
    expect(popup.url()).toContain('/desk/query-report/Employee%20Leave%20Balance')
    await popup.close()
  })

  test('opening a report from a person carries that person as a filter', async ({ page, context }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill(COLLEAGUE_NAME)
    await page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
    await expect(page).toHaveURL(/\/helixhr\/people\/[^/]+$/)

    await page.getByRole('link', { name: 'Leave reports' }).click()
    await expect(page).toHaveURL(/\/helixhr\/reports\?employee=/)
    await expect(page.getByText('Filtered to one person.')).toBeVisible()

    const entry = page.getByRole('button', { name: 'Leave balance' }).first()
    const [popup] = await Promise.all([context.waitForEvent('page'), entry.click()])
    await popup.waitForLoadState('domcontentloaded')
    expect(popup.url()).toContain('employee=')
    await popup.close()
  })

  test('no payroll report appears in the list', async ({ page }) => {
    await page.goto('/helixhr/reports')
    await expect(page.getByText(/Salary/i)).toHaveCount(0)
    await expect(page.getByText(/Payroll/i)).toHaveCount(0)
  })
})

test('an employee identity has no Reports nav item, and a direct hit names the reason', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Reports' })).toHaveCount(0)

  await page.goto('/helixhr/reports')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByRole('button', { name: /Leave balance/ })).toHaveCount(0)
})

test('an IT Team identity is refused the same way, with no Desk-bound link visible', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the one negative assertion this project exists for')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Reports' })).toHaveCount(0)

  await page.goto('/helixhr/reports')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByRole('button', { name: /Leave balance/ })).toHaveCount(0)
})
