import { test, expect } from '@playwright/test'

// P6-U5 / P6-R1, P6-R2, P6-R13. Find a person, then read them -- HR-only,
// the server's own gate (`resolve_admin_scope`), the same posture
// organisation.spec.ts and settings.spec.ts already take.
//
// `setup_playwright_fixtures` seeds a colleague and a manager in the HR
// fixture's own company (`make_test_employee_and_manager`, reused by
// `ensure_directory_fixtures`) -- "Manager" is the name this suite searches
// for, the same fixture directory.spec.ts already opens.
const COLLEAGUE_NAME = 'Manager'

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'people is an HR-only screen')
  })

  test('an HR identity searches, opens a colleague, and the URL survives a reload', async ({ page }) => {
    await page.goto('/helixhr/')
    await expect(page.getByRole('link', { name: 'People' })).toBeVisible()

    await page.goto('/helixhr/people')
    await expect(page.getByRole('heading', { name: 'People' })).toBeVisible()

    const search = page.getByLabel('Search')
    await search.fill(COLLEAGUE_NAME)
    const row = page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first()
    await expect(row).toBeVisible()
    await row.click()

    await expect(page).toHaveURL(/\/helixhr\/people\/[^/]+$/)
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()
    await expect(page.getByText('Leave balance')).toBeVisible()
    await expect(page.getByText('Attendance this month')).toBeVisible()
    await expect(page.getByText('Open and recent requests')).toBeVisible()

    // A reload lands on the same person (P2-R12) -- not a client-side-only
    // route, and not thrown back to the search.
    await page.reload()
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()
  })

  test('P8-U8: editing overview persists and reflects without a full reload', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill(COLLEAGUE_NAME)
    await page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()

    const email = `manager-e2e-${Date.now()}@helixhr.test`
    await page.getByTestId('person-edit-overview').click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('heading', { name: 'Edit overview' })).toBeVisible()
    const original = await page.getByLabel('Work email').inputValue()
    await page.getByLabel('Work email').fill(email)
    await page.getByRole('button', { name: 'Save' }).click()

    await expect(dialog).toBeHidden()
    await expect(page.getByText(email)).toBeVisible()

    // Read back from the server, not just the in-memory response.
    await page.reload()
    await expect(page.getByText(email)).toBeVisible()

    // Put the seeded address back: directory.spec.ts asserts the manager's
    // published work email, and fails whenever this spec runs before it.
    await page.getByTestId('person-edit-overview').click()
    await page.getByLabel('Work email').fill(original)
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toBeHidden()
  })

  test('P8-U9: setting an approver persists and shows the resolved name', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill(COLLEAGUE_NAME)
    await page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()

    await page.getByTestId('person-edit-approvers').click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('heading', { name: 'Edit approvers and shift' })).toBeVisible()

    // The manager picker offers themself among the options (any active
    // employee in their own company, per get_person_form_options) --
    // picking the second option (index 1) avoids "None" at index 0
    // without depending on a specific fixture name. get_person_form_options
    // loads lazily on first open, so wait for it past the "None"-only state
    // before reading the list.
    const managerSelect = page.getByLabel('Manager')
    await expect(async () => {
      expect(await managerSelect.locator('option').count()).toBeGreaterThan(1)
    }).toPass({ timeout: 10000 })
    const options = await managerSelect.locator('option').allTextContents()
    await managerSelect.selectOption({ index: 1 })
    const chosenName = options[1]

    await page.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByText(chosenName)).toBeVisible()
  })

  test('a search nobody matches names it plainly', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill('zzz-nobody-by-that-name')
    await expect(page.getByText('Nobody matches that search')).toBeVisible()
  })
})

test('an employee identity has no People nav item, and a direct hit is refused server-side', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'People' })).toHaveCount(0)

  await page.goto('/helixhr/people')
  await expect(page.locator('[data-async-state="people:forbidden"]')).toBeVisible()

  await page.goto('/helixhr/people/HR-EMP-00001')
  await expect(page.locator('[data-async-state="person:forbidden"]')).toBeVisible()
})

test('an IT Team identity -- a routed worker, not HR -- is refused the same way', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the one negative assertion this project exists for')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'People' })).toHaveCount(0)

  await page.goto('/helixhr/people')
  await expect(page.locator('[data-async-state="people:forbidden"]')).toBeVisible()
})
