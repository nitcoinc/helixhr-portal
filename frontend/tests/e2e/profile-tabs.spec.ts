import { test, expect } from '@playwright/test'

// A real one-page PDF (Frappe parses PDFs on save); same bytes as
// requests-documents.spec.ts.
const SAFE_PDF = Buffer.from(
  'JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNzIgNzIgXQovUGFyZW50IDIgMCBSCj4+CmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA1NCAwMDAwMCBuIAowMDAwMDAwMTEzIDAwMDAwIG4gCjAwMDAwMDAxNjIgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSA1Ci9Sb290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgoyNTQKJSVFT0YK',
  'base64',
)

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

  // Plan 2026-10-02-001 U14. A bank-detail correction carries the value, typed
  // twice, plus proof; date of birth keeps the free-text draft.
  test('bank account asks for the new value twice and shows a mismatch inline', async ({ page }) => {
    await page.goto('/helixhr/profile/bank')
    await page
      .getByTestId('profile-field-bank_ac_no')
      .getByRole('button', { name: /Request a correction/ })
      .click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('New value').fill('111122223333')
    await dialog.getByLabel('Type it again').fill('111122223334')
    await expect(dialog.getByRole('alert')).toHaveText(
      'The two entries don’t match. Type the new value again in both boxes.',
    )
    await expect(dialog.getByRole('button', { name: 'Send to HR' })).toBeDisabled()
    await dialog.getByLabel('Type it again').fill('111122223333')
    await expect(dialog.getByRole('alert')).toHaveCount(0)
    // Proof is required: still disabled without it.
    await expect(dialog.getByRole('button', { name: 'Send to HR' })).toBeDisabled()
    await dialog.getByRole('button', { name: 'Cancel' }).click()

    await page.goto('/helixhr/profile')
    await page
      .getByTestId('profile-field-date_of_birth')
      .getByRole('button', { name: /Request a correction/ })
      .click()
    await expect(page.getByRole('dialog').getByLabel('Subject')).toHaveValue('Correct my date of birth')
    await expect(page.getByRole('dialog').getByLabel('New value')).toHaveCount(0)
  })

  test('an IBAN correction with proof is revealed and applied by HR', async ({ page, browser }) => {
    // Unique per run so a long-lived site never sees "already the value",
    // with a real ISO 13616 check (ERPNext validates IBANs on save).
    const bban = `NWBK601613${String(Date.now()).slice(-8)}`
    const digits = `${bban}GB00`.replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55))
    const check = String(98n - (BigInt(digits) % 97n)).padStart(2, '0')
    const iban = `GB${check}${bban}`
    const tail = iban.slice(-4)
    await page.goto('/helixhr/profile/bank')
    await page
      .getByTestId('profile-field-iban')
      .getByRole('button', { name: /Request a correction/ })
      .click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('New value').fill(iban)
    await dialog.getByLabel('Type it again').fill(iban)
    await dialog.getByLabel('Proof (required)').setInputFiles({
      name: 'bank-letter.pdf',
      mimeType: 'application/pdf',
      buffer: SAFE_PDF,
    })
    await dialog.getByRole('button', { name: 'Send to HR' }).click()
    await expect(dialog.getByText('HR has your request')).toBeVisible()
    await dialog.getByRole('link', { name: 'View your request' }).click()
    await expect(page.getByTestId('request-correction')).toContainText(`••••${tail}`)
    await expect(page.locator('body')).not.toContainText(iban)
    const name = page.url().split('/').pop()

    const hrContext = await browser.newContext({ storageState: 'tests/.auth/hr.json' })
    const hr = await hrContext.newPage()
    await hr.goto(`/helixhr/approvals/request/${name}`)
    const panel = hr.getByTestId('approval-detail')
    const review = panel.getByTestId('correction-review')
    await expect(review.getByTestId('correction-proposed')).toHaveText(`••••${tail}`, { timeout: 10000 })
    await expect(review.getByRole('link', { name: /bank-letter\.pdf/ })).toBeVisible()
    await expect(panel).not.toContainText(iban)
    await review.getByRole('button', { name: 'Reveal' }).click()
    await review.getByRole('button', { name: 'Show full value' }).click()
    await expect(review.getByTestId('correction-proposed')).toHaveText(iban)
    await review.getByRole('button', { name: 'Hide' }).click()
    await expect(review.getByTestId('correction-proposed')).toHaveText(`••••${tail}`)

    await panel.getByTestId('pick-up').click()
    await expect(hr).toHaveURL(/\/helixhr\/approvals$/)
    await hr.goto(`/helixhr/approvals/request/${name}`)
    await panel.getByTestId('done').click()
    await expect(hr).toHaveURL(/\/helixhr\/approvals$/)
    await hrContext.close()

    await page.goto('/helixhr/profile/bank')
    await expect(page.getByTestId('profile-field-iban')).toContainText(`••••${tail}`)
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
    // Retired category: the per-row buttons are gone, the banner speaks instead.
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
