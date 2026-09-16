import { test, expect } from '@playwright/test'

// P5-U15. The organisation view is read-only and HR-only: there is nothing
// here for a Python test to prove that a real browser hitting the route
// directly is what actually demonstrates -- the server's own gate, not a
// client-side redirect, and no nav item for anyone else.

test('an HR identity reaches Organisation and sees the aggregate counts', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'organisation is an HR-only screen')

  await page.goto('/helixhr/organisation')
  await expect(page.getByRole('heading', { name: 'Organisation' })).toBeVisible()
  await expect(page.getByText('Headcount')).toBeVisible()
  await expect(page.getByText('On leave today')).toBeVisible()
  await expect(page.getByTestId('organisation-queue-leave')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Organisation' })).toBeVisible()
})

test('an employee identity has no Organisation nav item, and a direct hit is refused server-side', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Organisation' })).toHaveCount(0)

  // `get_organisation_view` throws PermissionError for a non-HR caller, and
  // AsyncState renders that as its 'forbidden' region -- the same posture
  // /settings already takes (P5-U14).
  await page.goto('/helixhr/organisation')
  await expect(page.locator('[data-async-state="organisation:forbidden"]')).toBeVisible()
  await expect(page.getByText("You don't have access to this")).toBeVisible()
})

test('an IT Team identity -- a routed worker, not HR -- is refused the same way', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the one negative assertion this project exists for')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Organisation' })).toHaveCount(0)

  await page.goto('/helixhr/organisation')
  await expect(page.locator('[data-async-state="organisation:forbidden"]')).toBeVisible()
})
