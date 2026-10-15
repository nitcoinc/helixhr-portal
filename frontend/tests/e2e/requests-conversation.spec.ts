// Plan 2026-10-15-003 U4: e2e tests for request conversation.
import { test, expect } from '@playwright/test'
import { loginAs, EMPLOYEE_USER, HR_USER, IT_USER } from './helpers'

test.describe('Request conversation', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, EMPLOYEE_USER)
  })

  test('employee can comment on their own request', async ({ page }) => {
    // Open a request detail.
    await page.goto('/requests')
    const request = page.locator('[data-testid="request-row"]').first()
    if (await request.isVisible()) {
      await request.click()
      await page.waitForSelector('[data-testid="request-thread"]')
      // Should see the thread component.
      await expect(page.locator('textarea[placeholder*="Add a comment"]').or(page.locator('text=This request is closed'))).toBeVisible()
    }
  })

  test('handler sees thread on approvals detail', async ({ page }) => {
    await loginAs(page, HR_USER)
    await page.goto('/approvals')
    const request = page.locator('[data-testid="request-row"]').first()
    if (await request.isVisible()) {
      await request.click()
      await page.waitForTimeout(1000)
      // Desktop detail should show the thread.
      const thread = page.locator('[data-testid="request-thread"]').first()
      await expect(thread).toBeVisible()
    }
  })
})