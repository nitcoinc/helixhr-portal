import { test, expect } from '@playwright/test'

test.describe('employee', () => {
  test('applies for leave and sees the waiting status (R12, R13)', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')

    await page.goto('/helixhr/leave')
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByRole('heading', { name: 'Leave' })).toBeVisible()

    await page.getByRole('button', { name: 'Ask for leave' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    // setup_playwright_fixtures gives the employee a real Casual Leave
    // allocation and approver, so this is a genuine apply, not a fixture
    // that's guaranteed to fail.
    await dialog.getByRole('combobox').click()
    await page.getByRole('option', { name: 'Casual Leave' }).click()
    await dialog.getByRole('button', { name: 'Ask for leave' }).click()

    await expect(dialog).toBeHidden()
    await expect(page.getByText(/Waiting for Manager/)).toBeVisible()
  })
})
