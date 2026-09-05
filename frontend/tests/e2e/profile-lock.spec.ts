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

    // Mobile is editable. P2-U3 replaced the seven per-field Save buttons
    // with one bar for the whole form: it appears only once something has
    // actually changed, and says how much.
    // A fresh value every run: the bar only appears once something has
    // actually *changed*, so re-filling the number a previous run left behind
    // would correctly produce no bar at all.
    const mobile = `+1-555-${String(Date.now() % 10000).padStart(4, '0')}`
    const mobileRow = page.getByTestId('profile-editable-cell_number')
    await mobileRow.getByLabel('Mobile').fill(mobile)

    const saveBar = page.getByTestId('profile-save-bar')
    await expect(saveBar).toBeVisible()
    await expect(saveBar.getByText('1 unsaved change')).toBeVisible()
    await saveBar.getByRole('button', { name: 'Save' }).click()
    // `exact` matters here: Playwright's default text match is a
    // case-insensitive substring, and "1 unsaved change" contains "saved",
    // so a loose matcher passes before the save has even been sent.
    await expect(saveBar.getByText('Saved', { exact: true })).toBeVisible()

    await page.reload()
    await expect(page.getByTestId('profile-editable-cell_number').getByLabel('Mobile')).toHaveValue(
      mobile,
    )
    // And with nothing changed the bar is gone, so a page you came to read
    // carries no dead control.
    await expect(page.getByTestId('profile-save-bar')).toHaveCount(0)
  })

  test('Ask HR link opens a pre-filled request route', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')

    await page.goto('/helixhr/profile')
    const departmentRow = page.getByTestId('profile-readonly-department')
    await departmentRow.getByRole('link', { name: 'Ask HR' }).click()

    await expect(page).toHaveURL(/\/requests\?.*category=HR\+Letter/)
  })
})
