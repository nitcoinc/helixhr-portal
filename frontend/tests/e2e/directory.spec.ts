import { test, expect } from '@playwright/test'

// P3-U8 scenario 5 / P3-R22. The employee looks their manager up by name,
// opens the person sheet on a phone, and reaches them: the email action is a
// real `mailto:` and nothing on the page is a login identifier.
//
// The fixture (`helixhr.tests.utils.ensure_directory_fixtures`, called by
// `setup_playwright_fixtures`) publishes a work email on the manager record,
// which is the only thing that turns an email address into an action.
const MANAGER_NAME = 'Manager'
const MANAGER_EMAIL = 'manager.work@helixhr.test'

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('the directory finds a colleague by name and offers their work email', async ({ page }) => {
    await page.goto('/helixhr/directory')
    await expect(page.getByRole('heading', { name: 'Directory' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    // The search is a server question, so the row arrives from the API rather
    // than from a filter over the page that is already loaded.
    // frappe-ui's FormControl forwards attributes to the input itself, so the
    // field is addressed by its label the way the other specs do it.
    const search = page.getByLabel('Search')
    await search.fill(MANAGER_NAME)
    await expect(page.getByText(MANAGER_NAME, { exact: true }).first()).toBeVisible()

    // Desktop shape: three-column cards that already carry the work email.
    const mailto = page.locator(`a[href="mailto:${MANAGER_EMAIL}"]`).first()
    await expect(mailto).toBeVisible()

    // A search nobody matches is its own sentence, never the empty state's
    // (P2-R2) and never an error.
    await search.fill('zzz-nobody-by-that-name')
    await expect(page.getByText('Nobody matches that search')).toBeVisible()
  })
})

test.describe('employee on a phone', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('a row opens the person sheet, with a mailto: action', async ({ browser }) => {
    const context = await browser.newContext({
      storageState: 'tests/.auth/employee.json',
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    })
    const page = await context.newPage()
    await page.goto('/helixhr/directory')
    await page.waitForLoadState('networkidle')

    await page.getByLabel('Search').fill(MANAGER_NAME)
    await page
      .getByRole('listitem')
      .filter({ hasText: MANAGER_NAME })
      .getByRole('button')
      .first()
      .click()

    const sheet = page.getByRole('dialog')
    await expect(sheet).toBeVisible()
    await expect(sheet.getByText('Department')).toBeVisible()
    await expect(sheet.getByText('Manager', { exact: true }).first()).toBeVisible()

    // The action, and the only contact route the directory publishes.
    const action = sheet.locator(`a[href="mailto:${MANAGER_EMAIL}"]`)
    await expect(action).toBeVisible()
    expect(await action.getAttribute('href')).toBe(`mailto:${MANAGER_EMAIL}`)

    // The sign-in address is never in the payload, so it can never be on the
    // page (P3-R22).
    await expect(page.locator('body')).not.toContainText('employee@helixhr.test')
    await expect(page.locator('body')).not.toContainText('manager@helixhr.test')

    // Escape closes it: the sheet is a shape of one row, not a screen.
    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden()
    await context.close()
  })
})
