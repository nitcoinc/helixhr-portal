import { test, expect } from '@playwright/test'

// A real one-page PDF (Frappe parses PDFs on save); same bytes as
// requests-documents.spec.ts.
const SAFE_PDF = Buffer.from(
  'JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNzIgNzIgXQovUGFyZW50IDIgMCBSCj4+CmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA1NCAwMDAwMCBuIAowMDAwMDAwMTEzIDAwMDAwIG4gCjAwMDAwMDAxNjIgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSA1Ci9Sb290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgoyNTQKJSVFT0YK',
  'base64',
)

const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

// Plan 2026-11-03-001 U9. Bank & IDs edit mode: a blank group fills in at
// once (password, no proof); a group on file goes to HR whole (proof); a
// refusal shows at its field and keeps what was typed. The seed
// (setup_playwright_fixtures) leaves passport blank and bank on file.
test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
  })

  test('no correction dialog is left on Bank & IDs', async ({ page }) => {
    await page.goto('/helixhr/profile/bank')
    await expect(page.getByTestId('bank-edit-start')).toBeVisible()
    await expect(page.getByRole('button', { name: /Request a correction/ })).toHaveCount(0)
  })

  test('a blank passport fills in now; a wrong password is refused at its field', async ({ page }) => {
    const number = `Z${String(Date.now()).slice(-7)}`
    await page.setViewportSize({ width: 360, height: 800 })
    await page.goto('/helixhr/profile/bank')
    await expect(page.getByTestId('profile-field-passport_number')).toContainText('Not recorded')
    await page.getByRole('button', { name: 'Edit bank and ID details' }).click()

    const editor = page.getByTestId('bank-edit')
    await editor.getByLabel('Passport number', { exact: true }).fill(number)
    await expect(editor.getByTestId('bank-edit-route-passport')).toHaveText('Fills in now')
    // Core fields of a first fill are asked for before anything is sent.
    await expect(editor.getByTestId('bank-edit-group-error-passport')).toContainText('also give')
    await editor.getByTestId('bank-edit-passport_number-confirm').fill(`${number}X`)
    await expect(editor.getByTestId('bank-edit-passport_number-error')).toContainText('don’t match')
    await editor.getByTestId('bank-edit-passport_number-confirm').fill(number)
    await expect(editor.getByTestId('bank-edit-passport_number-error')).toHaveCount(0)
    await editor.getByLabel('Date of issue').fill('2020-01-15')
    await editor.getByLabel(/valid/i).fill('2030-01-14')
    await editor.getByLabel('Place of issue').fill('Chennai')
    await expect(editor.getByTestId('bank-edit-proof')).toHaveCount(0)

    await editor.getByLabel('Your password').fill('not-the-password')
    await editor.getByTestId('bank-edit-send').click()
    await expect(editor.getByTestId('bank-edit-password-error')).toHaveText(/password isn.t right/)
    // Nothing typed is lost to a refusal.
    await expect(editor.getByLabel('Passport number', { exact: true })).toHaveValue(number)
    await expect(editor.getByLabel('Place of issue')).toHaveValue('Chennai')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360)

    await editor.getByLabel('Your password').fill(PASSWORD)
    await editor.getByTestId('bank-edit-send').click()
    const result = page.getByTestId('bank-edit-result')
    await expect(result).toContainText('Saved on your record')
    await expect(result).toContainText(`••••${number.slice(-4)}`)
    await expect(page.getByTestId('bank-edit-request-link')).toHaveCount(0)
    await expect(page.getByTestId('profile-field-passport_number')).toContainText(`••••${number.slice(-4)}`)
    await expect(page.locator('body')).not.toContainText(number)
    // Review B9: focus goes back to Edit.
    await expect(page.getByRole('button', { name: 'Edit bank and ID details' })).toBeFocused()
  })

  test('an IBAN change with bank on file goes to HR with proof, locks, and HR applies it', async ({ page, browser }) => {
    // IBAN rather than the account number, which profile-tabs.spec.ts pins
    // to the seed. Unique per run with a real ISO 13616 check (ERPNext
    // validates IBANs on save).
    const bban = `NWBK601613${String(Date.now()).slice(-8)}`
    const digits = `${bban}GB00`.replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55))
    const check = String(98n - (BigInt(digits) % 97n)).padStart(2, '0')
    const account = `GB${check}${bban}`
    const tail = account.slice(-4)
    await page.goto('/helixhr/profile/bank')
    await page.getByRole('button', { name: 'Edit bank and ID details' }).click()
    const editor = page.getByTestId('bank-edit')
    await expect(editor.getByTestId('bank-edit-bank_ac_no')).toContainText('On file: ••••')
    await editor.getByTestId('bank-edit-iban').locator('input').first().fill(account)
    await editor.getByTestId('bank-edit-iban-confirm').fill(account)
    await expect(editor.getByTestId('bank-edit-route-bank')).toHaveText('Goes to HR')
    await expect(editor.getByLabel('Your password')).toHaveCount(0)
    // Proof is required before Send.
    await expect(editor.getByTestId('bank-edit-send')).toBeDisabled()
    await editor.getByTestId('bank-edit-proof').setInputFiles({
      name: 'bank-letter.pdf',
      mimeType: 'application/pdf',
      buffer: SAFE_PDF,
    })
    await editor.getByTestId('bank-edit-send').click()
    const link = page.getByTestId('bank-edit-request-link')
    await expect(link).toBeVisible()
    const requestPath = await link.getAttribute('href')
    await expect(page.getByTestId('profile-pending-iban')).toHaveText('Waiting for HR')
    await expect(page.locator('body')).not.toContainText(account)

    // Locked in edit mode while the request is open (R17).
    await page.getByRole('button', { name: 'Edit bank and ID details' }).click()
    await expect(page.getByTestId('bank-edit-locked-iban')).toContainText('Waiting for HR')
    await page.getByRole('button', { name: 'Cancel' }).click()

    await page.goto(requestPath!)
    await expect(page.getByTestId('request-correction')).toContainText(`••••${tail}`)
    const name = page.url().split('/').pop()

    const hrContext = await browser.newContext({ storageState: 'tests/.auth/hr.json' })
    const hr = await hrContext.newPage()
    await hr.goto(`/helixhr/requests/${name}?tab=work`)
    const panel = hr.getByTestId('work-detail')
    const review = panel.getByTestId('correction-review')
    const row = review.getByTestId('correction-row').first()
    await expect(row.getByTestId('correction-proposed')).toHaveText(`••••${tail}`, { timeout: 10000 })
    await expect(review.getByRole('link', { name: /bank-letter\.pdf/ })).toBeVisible()
    await panel.getByTestId('pick-up').click()
    await expect(panel.getByTestId('done')).toBeVisible({ timeout: 10000 })
    await row.getByRole('button', { name: /Reveal/ }).click()
    await row.getByRole('button', { name: 'Show full value' }).click()
    await expect(row.getByTestId('correction-proposed')).toHaveText(account)
    await row.getByRole('button', { name: /Hide/ }).click()
    await panel.getByTestId('done').click()
    await expect(panel.getByTestId('request-thread')).toContainText('This request is closed.', { timeout: 10000 })
    await hrContext.close()

    await page.goto('/helixhr/profile/bank')
    await expect(page.getByTestId('profile-field-iban')).toContainText(`••••${tail}`)
    await expect(page.getByTestId('profile-pending-iban')).toHaveCount(0)
  })
})
