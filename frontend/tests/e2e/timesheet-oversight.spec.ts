import { test, expect } from '@playwright/test'

// Plan 2026-10-15-002 U6 (R6, R7, R9-R11), as the HR User identity
// (`hr-user@helixhr.test`, the `hr-user` project): All pending timesheets,
// Send reminder, and the read-only detail. `setup_playwright_fixtures`
// seeds one pending week for `oversight-manager@helixhr.test` in the HR
// User's company and frees that approver's reminder slot, so every check
// runs against a known group.

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr-user', 'this is the HR User capability shape')
  await page.goto('/helixhr/approvals')
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()
})

test('HR User lands on the Timesheets oversight list with no tab bar', async ({ page }) => {
  // One visible tab hides the bar (R14).
  await expect(page.getByRole('tablist', { name: 'Approvals views' })).toHaveCount(0)
  const oversight = page.getByTestId('timesheet-oversight')
  await expect(oversight.getByRole('heading', { name: /All pending timesheets/ })).toBeVisible()
  const toggle = page.getByTestId('oversight-overdue-toggle')
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
})

test('Send reminder confirms, then shows the next allowed time', async ({ page }) => {
  const group = page.locator('[data-testid="oversight-group"][data-approver="oversight-manager@helixhr.test"]')
  await expect(group).toBeVisible({ timeout: 15000 })
  await expect(group).toContainText('Oversight-manager')
  const button = group.getByTestId('send-reminder')
  await expect(button).toHaveAttribute('aria-disabled', 'false')
  await button.click()
  await page.getByTestId('reminder-confirm').click()
  await expect(group.getByTestId('reminder-status')).toContainText('Next reminder allowed')
})

test('opening a timesheet is view only', async ({ page }) => {
  const group = page.locator('[data-testid="oversight-group"][data-approver="oversight-manager@helixhr.test"]')
  const row = group.getByTestId('oversight-row').first()
  await expect(row).toBeVisible({ timeout: 15000 })
  await row.click()
  await expect(page.getByTestId('view-only-note').first()).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(0)
})
