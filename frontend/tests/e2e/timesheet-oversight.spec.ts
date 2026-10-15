// Plan 2026-10-15-002 U6: e2e tests for timesheet oversight and reminders.
import { test, expect } from '@playwright/test'
import { loginAs, HR_USER } from './helpers'

test.describe('Timesheet oversight', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, HR_USER)
    await page.goto('/approvals?view=timesheets')
    await page.waitForSelector('text=All pending timesheets')
  })

  test('shows the pending timesheets list', async ({ page }) => {
    await expect(page.locator('text=All pending timesheets')).toBeVisible()
  })

  test('has an Overdue-only toggle', async ({ page }) => {
    const toggle = page.locator('input[type="checkbox"]')
    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(toggle).toBeChecked()
  })

  test('shows refresh button', async ({ page }) => {
    await expect(page.locator('button', { hasText: 'Refresh' })).toBeVisible()
  })

  test('view-only note visible for HR User', async ({ page }) => {
    await expect(page.locator('text=View only')).toBeVisible()
  })
})