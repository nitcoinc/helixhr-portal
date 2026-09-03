import { test, expect } from '@playwright/test'

test.describe('employee', () => {
  test('locked field is read-only with an Ask HR link, editable field can be saved (R8, R9, R11)', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')

    await page.goto('/helixhr/profile')
    await expect(page).not.toHaveURL(/\/login/)

    await expect(page.getByText('Your information')).toBeVisible()
    await expect(page.getByText('You can update')).toBeVisible()

    // Department is locked -- shown as plain text next to an "Ask HR"
    // link, not as an editable field.
    const departmentRow = page.getByTestId('profile-readonly-department')
    await expect(departmentRow.getByRole('link', { name: 'Ask HR' })).toBeVisible()

    // Mobile number is editable: type a value and save it.
    const mobileRow = page.getByTestId('profile-editable-cell_number')
    await mobileRow.getByLabel('Mobile number').fill('+1-555-0123')
    await mobileRow.getByRole('button', { name: 'Save' }).click()
    await expect(mobileRow.getByText('Saved')).toBeVisible()

    await page.reload()
    await expect(page.getByTestId('profile-editable-cell_number').getByLabel('Mobile number')).toHaveValue(
      '+1-555-0123',
    )
  })

  test('Ask HR link opens a pre-filled request route', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')

    await page.goto('/helixhr/profile')
    const departmentRow = page.getByTestId('profile-readonly-department')
    await departmentRow.getByRole('link', { name: 'Ask HR' }).click()

    await expect(page).toHaveURL(/\/requests\?.*category=HR\+Letter/)
  })
})
