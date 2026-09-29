import { test, expect } from '@playwright/test'

test.describe('employee', () => {
  test('a locked field offers a correction, an editable field can be saved (R8, R9, R11)', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')

    await page.goto('/helixhr/profile/job')
    await expect(page).not.toHaveURL(/\/login/)

    // Department is locked -- shown as plain text with a way to ask HR to
    // fix it, not as an editable field.
    const departmentRow = page.getByTestId('profile-field-department')
    await expect(departmentRow.getByRole('button', { name: /Request a correction/ })).toBeVisible()
    await expect(departmentRow.getByRole('textbox')).toHaveCount(0)

    // Mobile is editable, on the Contact tab. One bar for the whole form: it
    // appears only once something has actually changed, and says how much.
    // A fresh value every run: re-filling the number a previous run left
    // behind would correctly produce no bar at all.
    await page.getByTestId('profile-tab-contact').click()
    await expect(page).toHaveURL(/\/helixhr\/profile\/contact$/)
    const mobile = `+1-555-${String(Date.now() % 10000).padStart(4, '0')}`
    const mobileRow = page.getByTestId('profile-editable-cell_number')
    await mobileRow.getByLabel('Mobile').fill(mobile)

    const saveBar = page.getByTestId('profile-save-bar')
    await expect(saveBar).toBeVisible()
    await expect(saveBar.getByText('1 unsaved change')).toBeVisible()
    await saveBar.getByRole('button', { name: 'Save' }).click()
    // `exact` matters: "1 unsaved change" contains "saved", so a loose
    // matcher passes before the save has even been sent.
    await expect(saveBar.getByText('Saved', { exact: true })).toBeVisible()

    await page.reload()
    await expect(page.getByTestId('profile-editable-cell_number').getByLabel('Mobile')).toHaveValue(
      mobile,
    )
    await expect(page.getByTestId('profile-save-bar')).toHaveCount(0)
  })
})
