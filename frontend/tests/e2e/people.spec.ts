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
