import { test, expect } from '@playwright/test'

// Plan 2026-10-15-002 U6 (R6, R7, R9-R11), as the HR User identity
// (`hr-user@helixhr.test`, the `hr-user` project): All pending timesheets,
// Send reminder, and the read-only detail. The rows come from whatever the
// site has pending in the HR User's company, so each check holds on a fresh
// site and on a long-lived one.

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
  const button = page.getByTestId('send-reminder').and(page.locator('[aria-disabled="false"]')).first()
  test.skip((await button.count()) === 0, 'no remindable approver group on this site right now')
  const approver = await page.getByTestId('oversight-group').filter({ has: button }).first().getAttribute('data-approver')
  const group = page.locator(`[data-testid="oversight-group"][data-approver="${approver}"]`)
  await button.click()
  await page.getByTestId('reminder-confirm').click()
  await expect(group.getByTestId('reminder-status')).toContainText('Next reminder allowed')
})

test('opening a timesheet is view only', async ({ page }) => {
  const row = page.getByTestId('oversight-row').first()
  test.skip((await row.count()) === 0, 'no pending timesheet in this company right now')
  await row.click()
  await expect(page.getByTestId('view-only-note').first()).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(0)
})
