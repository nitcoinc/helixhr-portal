import { test, expect } from '@playwright/test'

// Plan 2026-09-29-001 U5. Everything HR holds, by tab, with a one-click
// correction -- asserted against the seeded record's actual values, not
// labels (docs/runbook.md: specs that only checked a label passed while the
// feature was broken).
test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
  })

  test('the nav reaches Profile and Personal shows the recorded date of birth', async ({ page }) => {
    await page.goto('/helixhr')
    // Reached by clicking, not by URL: the route has to exist in the shell.
    const nav = page.getByRole('navigation', { name: 'Main' }).first()
    await nav.getByRole('link', { name: 'Profile' }).click()
    await expect(page).toHaveURL(/\/helixhr\/profile$/)

    // make_test_user seeds 1990-01-01.
    const dob = page.getByTestId('profile-field-date_of_birth')
    await expect(dob).toContainText('1990')
    await expect(page.getByRole('heading', { name: 'Personal' })).toBeVisible()
  })

  test('Bank & IDs survives a refresh and shows the account masked', async ({ page }) => {
    await page.goto('/helixhr/profile/bank')
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Bank & IDs' })).toBeVisible()
    const account = page.getByTestId('profile-field-bank_ac_no')
    await expect(account).toContainText('••••5678')
    await expect(page.locator('body')).not.toContainText('004512345678')
  })

  test('a correction on date of birth files a pre-filled Profile correction request', async ({
    page,
  }) => {
    await page.goto('/helixhr/profile')
    await page
      .getByTestId('profile-field-date_of_birth')
      .getByRole('button', { name: /Request a correction/ })
      .click()

    const dialog = page.getByRole('dialog')
    await expect(dialog.getByLabel('Subject')).toHaveValue('Correct my date of birth')
    await expect(dialog.getByRole('button', { name: /Profile correction/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await dialog.getByRole('button', { name: 'Send to HR' }).click()
    await expect(dialog.getByText('HR has your request')).toBeVisible()

    await dialog.getByRole('link', { name: 'View your request' }).click()
    await expect(page).toHaveURL(/\/helixhr\/requests\/.+/)
    await expect(page.getByText('Correct my date of birth').first()).toBeVisible()
  })

  test('a plain employee sees no Open in Desk', async ({ page }) => {
    await page.goto('/helixhr/profile')
    await expect(page.getByTestId('profile-identity')).toBeVisible()
    await expect(page.getByTestId('profile-desk-link')).toHaveCount(0)
  })

  test('every tab is reachable at 360px and nothing scrolls sideways', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 })
    for (const tab of ['personal', 'job', 'contact', 'history', 'bank']) {
      await page.goto(tab === 'personal' ? '/helixhr/profile' : `/helixhr/profile/${tab}`)
      await expect(page.getByTestId(`profile-tab-${tab}`)).toHaveAttribute('aria-current', 'page')
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, tab).toBeLessThanOrEqual(0)
    }
  })

  test('an unknown tab is Not found, not a blank page', async ({ page }) => {
    await page.goto('/helixhr/profile/salary')
    await expect(page.locator('[data-portal-state="not-found"]')).toBeVisible()
  })
})

test.describe('hr', () => {
  test('HR sees Open in Desk pointing at their Employee form', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'hr-only scenario')
    await page.goto('/helixhr/profile')
    await expect(page.getByTestId('profile-desk-link')).toHaveAttribute('href', /\/employee\//)
  })
})
