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

  // Plan 2026-10-17-001 U10 (ED-18, EP-17): one correction entry per section,
  // not one per row. Plan 2026-11-03-001 R18: it opens the Requests form with
  // the correction drafted, not a dialog of its own.
  test('a read-only section offers one correction entry that drafts a request', async ({ page }) => {
    await page.goto('/helixhr/profile')
    const personal = page.getByRole('region', { name: 'Personal' })
    // Counted once the section has its data, not on the loading skeleton.
    await expect(personal.getByTestId('profile-field-date_of_birth')).toBeVisible()
    expect(await personal.getByTestId(/^profile-field-/).count()).toBeGreaterThan(1)
    await expect(personal.getByRole('button', { name: /Request a correction/ })).toHaveCount(1)
    await expect(page.getByTestId('profile-field-date_of_birth').getByRole('button')).toHaveCount(0)

    await personal.getByRole('button', { name: /Request a correction/ }).click()
    await expect(page).toHaveURL(/\/helixhr\/requests/)
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByLabel('Subject')).toHaveValue('Correct my personal details')
    await expect(dialog.getByRole('button', { name: /Profile correction/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await dialog.getByRole('button', { name: 'Send to HR' }).click()
    await expect(page.getByText('Correct my personal details').first()).toBeVisible()

    // The Contact tab keeps its inline edit, and Personal says so.
    await page.goto('/helixhr/profile')
    await page.getByRole('link', { name: 'Contact & emergency' }).last().click()
    await expect(page).toHaveURL(/\/helixhr\/profile\/contact$/)
    await expect(page.getByTestId('profile-editable-cell_number').getByLabel('Mobile')).toBeEditable()
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

  test('a retired category and a failed section each say so, and Retry refetches', async ({
    page,
  }) => {
    // The real response, bent into both fallback states, so the page's own
    // rendering of them is what is asserted.
    let calls = 0
    await page.route('**/api/method/helixhr.api.get_my_profile*', async (route) => {
      calls += 1
      const response = await route.fetch()
      const body = await response.json()
      body.message.correction_category = null
      if (calls === 1) {
        body.message.failed_sections = ['bank']
        body.message.sections.bank = null
      }
      await route.fulfill({ response, json: body })
    })

    await page.goto('/helixhr/profile/bank')
    await expect(page.getByTestId('profile-contact-hr')).toBeVisible()
    await expect(page.getByText('Couldn’t load this section.')).toBeVisible()
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByTestId('profile-field-bank_ac_no')).toContainText('••••5678')
    // Retired category: the correction entries are gone, the banner speaks instead.
    await expect(page.getByRole('button', { name: /Request a correction/ })).toHaveCount(0)
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
