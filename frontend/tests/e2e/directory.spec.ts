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

  // P3-U9 regression, and desktop-only on purpose: the coarse-pointer sweep
  // in visual-foundation.spec.ts runs at 390px, where the card *is* a real
  // control and the card body is not rendered at all, so neither of these
  // two defects is reachable from there.
  //
  // The two people are stubbed rather than seeded because the assertion
  // needs a colleague whose manager is *also* on the page -- that is what
  // makes "Reports to" a control instead of a name -- and which of a site's
  // employees report to each other is not this spec's to arrange.
  test('a card is not a control at desktop widths, and Reports to is a real target', async ({
    page,
  }) => {
    await page.route('**/api/method/helixhr.api.get_directory*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          message: {
            people: [
              {
                name: 'HR-EMP-A',
                employee_name: 'Ada Reports',
                initials: 'AR',
                designation: 'Engineer',
                department: 'Engineering',
                manager: 'HR-EMP-B',
                manager_name: 'Bo Manages',
                email: 'ada@helixhr.test',
              },
              {
                name: 'HR-EMP-B',
                employee_name: 'Bo Manages',
                initials: 'BM',
                designation: 'Lead',
                department: 'Engineering',
                manager: null,
                manager_name: null,
                email: 'bo@helixhr.test',
              },
            ],
            total: 2,
            departments: [{ name: 'Engineering', count: 2 }],
          },
        }),
      }),
    )

    await page.goto('/helixhr/directory')
    await expect(page.getByRole('heading', { name: 'Directory' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    const cards = page.locator('li.surface-card')
    await expect(cards.first()).toBeVisible()
    // Tapping a card at this width does nothing -- `openPerson` returns
    // early -- so it must not be a tab stop. Fifty cards would otherwise be
    // fifty inert stops between the search box and the next real control.
    expect(await cards.locator('> button').count()).toBe(0)

    // The manager link inside the card body is a control, so the 44px floor
    // applies to it: it used to be a roughly 20px inline <button>, which the
    // `display: inline` exemption in visual-foundation.spec.ts does not
    // cover.
    const managerLink = page.getByRole('button', { name: 'Bo Manages' })
    await expect(managerLink).toBeVisible()
    const box = await managerLink.boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(44)
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
    // Exact, on the name itself. `hasText` is a case-insensitive *substring*
    // match over the whole row, and the fixture site has other people whose
    // names contain "manager" (the HR-queue identity is
    // `hr-manager-employee`, which also sorts first) -- so a loose filter
    // plus `.first()` opened somebody else's sheet and then failed on the
    // work email they do not publish.
    await page
      .getByRole('listitem')
      .filter({ has: page.getByText(MANAGER_NAME, { exact: true }) })
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
