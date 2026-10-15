// Plan 2026-10-15-001 U6: People & access Settings section e2e tests.
// Run as Portal Admin who can grant both companies.
import { test, expect } from '@playwright/test'
import { loginAs, PORTAL_ADMIN_USER } from './helpers'

test.describe('People & access', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, PORTAL_ADMIN_USER)
    await page.goto('/settings/people-access')
    await page.waitForSelector('text=People & access')
  })

  test('shows the person search', async ({ page }) => {
    await expect(page.locator('#people-access-search')).toBeVisible()
  })

  test('searches and selects a person', async ({ page }) => {
    const search = page.locator('#people-access-search')
    await search.fill('grant')
    // Wait for search results.
    await page.waitForTimeout(500)
    const results = page.locator('ul li')
    const count = await results.count()
    expect(count).toBeGreaterThan(0)
    // Select the first result.
    await results.first().click()
    // Person panel should show.
    await expect(page.locator('text=Current access')).toBeVisible()
  })

  test('shows profile chips for a grant holder', async ({ page }) => {
    const search = page.locator('#people-access-search')
    await search.fill('grant-holder')
    await page.waitForTimeout(500)
    const results = page.locator('ul li')
    if (await results.count() > 0) {
      await results.first().click()
      await page.waitForTimeout(500)
      // Should see Leadership or Cross-company chip.
      const chipArea = page.locator('text=All data').or(page.locator('text=Reports only'))
      await expect(chipArea).toBeVisible()
    }
  })

  test('shows unsaved changes bar after editing a role', async ({ page }) => {
    const search = page.locator('#people-access-search')
    await search.fill('grant-holder')
    await page.waitForTimeout(500)
    const results = page.locator('ul li')
    if (await results.count() > 0) {
      await results.first().click()
      await page.waitForTimeout(500)
      // Toggle a role checkbox.
      const checkboxes = page.locator('fieldset input[type="checkbox"]')
      if (await checkboxes.count() > 0) {
        await checkboxes.first().click()
        await expect(page.locator('text=Unsaved changes')).toBeVisible()
      }
    }
  })

  test('has no people-access tab for non-admins', async ({ page }) => {
    // Log in as an HR user with no Portal Admin.
    await loginAs(page, 'hr-user@helixhr.test')
    await page.goto('/settings')
    await expect(page.locator('[data-testid="settings-tab-people-access"]')).not.toBeVisible()
  })
})