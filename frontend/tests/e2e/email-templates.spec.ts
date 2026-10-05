import { test, expect, request, type Page } from '@playwright/test'

// Plan 2026-10-02-001 U10: the Email templates page -- the shared theme,
// message templates and celebrations & holidays -- is Portal Admin / System
// Manager only. Runs in the `hr` project (the refused identity); the Portal
// Admin tests sign in as `portal-admin@helixhr.test` (seeded by
// `setup_playwright_fixtures`) on a clean context.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
// A valid 1x1 PNG for the logo upload.
const PNG = Buffer.from(
  '89504e470d0a1a0a0000000d4948445200000001000000010806000000' +
    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082',
  'hex',
)

// HR is the refused hat; the other identities add nothing here.
test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'runs once, in the hr project')
})

async function signInAsPortalAdmin(page: Page, baseURL: string | undefined) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  const login = await api.post('/api/method/login', {
    form: { usr: 'portal-admin@helixhr.test', pwd: PASSWORD },
  })
  expect(login.ok()).toBeTruthy()
  const state = await api.storageState()
  await api.dispose()
  await page.context().clearCookies()
  await page.context().addCookies(state.cookies)
}

test('an HR Manager has no Email templates nav item and is sent home from the page', async ({ page }) => {
  await page.goto('/helixhr/settings')
  await expect(page.getByRole('heading', { name: 'Settings' }).first()).toBeVisible()
  // The Admin group is collapsed by default; open it so the check means something.
  const admin = page.getByRole('navigation', { name: 'Main' }).getByRole('button', { name: 'Admin' })
  if ((await admin.getAttribute('aria-expanded')) === 'false') await admin.click()
  await expect(page.getByRole('link', { name: 'Settings' }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: 'Email templates' })).toHaveCount(0)

  await page.goto('/helixhr/email-templates')
  await expect(page).not.toHaveURL(/email-templates/)
  await expect(page.getByTestId('email-template-groups')).toHaveCount(0)
})

test.describe('Portal Admin', () => {
  test.describe.configure({ mode: 'serial' })
  test.use({ storageState: { cookies: [], origins: [] } })

  test.beforeEach(async ({ page, baseURL }) => {
    await signInAsPortalAdmin(page, baseURL)
  })

  test('sees the Email templates nav item and all three groups', async ({ page }) => {
    await page.goto('/helixhr')
    const admin = page.getByRole('navigation', { name: 'Main' }).getByRole('button', { name: 'Admin' })
    if ((await admin.getAttribute('aria-expanded')) === 'false') await admin.click()
    await expect(page.getByRole('link', { name: 'Email templates' }).first()).toBeVisible()
    await page.goto('/helixhr/email-templates')
    for (const group of ['theme', 'messages', 'celebrations']) {
      await expect(page.getByTestId(`email-template-group-${group}`)).toBeVisible()
    }
  })

  test('edits the shared theme: colour, footer and logo update the live preview', async ({ page }) => {
    await page.goto('/helixhr/email-templates?group=theme')
    const theme = page.getByTestId('email-theme')
    await expect(theme).toBeVisible()
    // Start from the default, whatever an earlier run left behind.
    await theme.getByRole('button', { name: 'Reset to default' }).click()
    await theme.getByRole('button', { name: 'Reset', exact: true }).click()
    await expect(page.getByTestId('email-theme-status')).toHaveText('Back to the default theme.')

    const frame = page.frameLocator('iframe[title="Preview of the email theme"]')
    await expect(frame.locator('div[style*="background:#ffffff;color:#1f2328"]')).toHaveCount(1)

    // A preset re-renders the preview at once, text turning white on navy.
    await theme.getByRole('radio', { name: 'Navy' }).check({ force: true })
    await expect(frame.locator('div[style*="background:#0b2545;color:#ffffff"]')).toHaveCount(1)

    // A bad custom hex is refused before it reaches the server.
    await theme.getByRole('radio', { name: 'Custom' }).check({ force: true })
    await page.getByTestId('email-theme-custom-hex').fill('#12')
    await expect(theme.getByText('Use a 6-digit hex code like #0B2545.')).toBeVisible()
    await expect(theme.getByRole('button', { name: 'Save theme' })).toBeDisabled()
    await page.getByTestId('email-theme-custom-hex').fill('#F5D76E')
    await expect(frame.locator('div[style*="background:#f5d76e;color:#1f2328"]')).toHaveCount(1)

    const stamp = `E2E footer ${Date.now()}`
    await theme.getByLabel('Footer text').fill(`${stamp} {{ company }}`)
    await page.getByTestId('email-theme-preview').getByRole('button', { name: 'Update preview' }).click()
    await expect(frame.getByText(stamp)).toBeVisible()

    await theme.getByRole('button', { name: 'Save theme' }).click()
    await expect(page.getByTestId('email-theme-status')).toHaveText('Theme saved. Every email now uses it.')

    // The logo: uploaded once, shown in the preview by URL (sent mail embeds it).
    await page.getByTestId('email-theme-logo-input').setInputFiles({ name: 'logo.png', mimeType: 'image/png', buffer: PNG })
    await expect(page.getByTestId('email-theme-status')).toContainText('Logo saved')
    await expect(page.getByTestId('email-theme-logo')).toHaveAttribute('src', /\/files\/.+\.png$/)
    await expect(frame.locator('img').first()).toHaveAttribute('src', /^\/files\/.+\.png$/)

    // The message template preview uses the same theme, and a template may
    // still leave the logo out.
    await page.getByTestId('email-template-group-messages').click()
    await page.getByTestId('email-template-leave_approved').click()
    const messageFrame = page.frameLocator('iframe[title="Preview of Leave approved"]')
    await expect(messageFrame.locator('div[style*="background:#f5d76e"]')).toHaveCount(1)
    await expect(messageFrame.locator('img').first()).toHaveAttribute('src', /^\/files\/.+\.png$/)
    const include = page.getByTestId('email-template-editor').getByLabel('Include company logo')
    await include.uncheck()
    await expect(messageFrame.locator('img')).toHaveCount(0)
    await include.check()
  })

  test('theme code without {{ content }} is refused; valid code wraps the preview', async ({ page }) => {
    await page.goto('/helixhr/email-templates?group=theme')
    const theme = page.getByTestId('email-theme')
    await expect(theme).toBeVisible()
    await theme.getByRole('button', { name: 'Advanced: theme code' }).click()
    await page.getByTestId('email-theme-use-code').check()
    await page.getByTestId('email-theme-code').fill('<div>{{ logo }}</div>')
    await theme.getByRole('button', { name: 'Save theme' }).click()
    await expect(page.getByTestId('email-theme-error')).toContainText('content')

    await page.getByTestId('email-theme-code').fill(
      '<section data-e2e="custom-theme" style="border-top:4px solid {{ brand_color }}">{{ logo }}<h2>{{ subject }}</h2>{{ content }}</section>',
    )
    await page.getByTestId('email-theme-preview').getByRole('button', { name: 'Update preview' }).click()
    const frame = page.frameLocator('iframe[title="Preview of the email theme"]')
    await expect(frame.locator('section[data-e2e="custom-theme"]')).toHaveCount(1)
    await expect(page.locator('iframe[title="Preview of the email theme"]')).toHaveAttribute('sandbox', '')

    // Back to the default look, so other specs see the shipped theme.
    await theme.getByRole('button', { name: 'Reset to default' }).click()
    await theme.getByRole('button', { name: 'Reset', exact: true }).click()
    await expect(page.getByTestId('email-theme-status')).toHaveText('Back to the default theme.')
    await page.getByTestId('email-theme').getByRole('button', { name: 'Remove' }).click()
    await expect(page.getByTestId('email-theme-status')).toHaveText('Logo removed.')
  })

  test('edits, previews, saves and resets Leave approved', async ({ page }) => {
    await page.goto('/helixhr/email-templates')
    const item = page.getByTestId('email-template-leave_approved')
    await expect(item).toBeVisible()

    // Start from Default, whatever an earlier run left behind.
    await item.click()
    const editor = page.getByTestId('email-template-editor')
    const resetButton = editor.getByRole('button', { name: 'Reset to default' })
    if (await resetButton.isVisible()) {
      await resetButton.click()
      await page.getByRole('button', { name: 'Reset', exact: true }).click()
    }
    await expect(item.getByTestId('email-template-state')).toHaveText('Default')

    const stamp = `E2E ${Date.now()}`
    await editor.getByLabel('Subject').fill(`${stamp} {{ leave_type }}`)
    await editor.getByLabel('Body').fill('<p>Approved by </p>')
    // Variable buttons insert at the caret and give focus back to the body.
    await editor.getByLabel('Body').press('End')
    await editor.getByRole('button', { name: 'Insert approver_name' }).click()
    await expect(editor.getByLabel('Body')).toHaveValue('<p>Approved by </p>{{ approver_name }}')
    await expect(editor.getByLabel('Body')).toBeFocused()

    const preview = page.getByTestId('email-template-preview')
    await preview.getByRole('button', { name: 'Update preview' }).click()
    await expect(preview.getByText(`${stamp} Casual Leave`)).toBeVisible()
    const frame = page.frameLocator('iframe[title="Preview of Leave approved"]')
    await expect(frame.getByText('Meera Shah')).toBeVisible()

    await editor.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('email-template-status')).toHaveText('Saved.')
    await expect(item.getByTestId('email-template-state')).toHaveText('Custom')

    await editor.getByRole('button', { name: 'Reset to default' }).click()
    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await expect(item.getByTestId('email-template-state')).toHaveText('Default')
    await expect(editor.getByLabel('Subject')).toHaveValue('Your {{ leave_type }} was approved')
  })

  test('a typo variable is refused inline under the body', async ({ page }) => {
    await page.goto('/helixhr/email-templates')
    await page.getByTestId('email-template-leave_rejected').click()
    const editor = page.getByTestId('email-template-editor')
    await editor.getByLabel('Body').fill('<p>{{ aprover_name }}</p>')
    await editor.getByRole('button', { name: 'Save' }).click()
    await expect(editor.getByRole('alert')).toContainText('aprover_name')
    await expect(editor.getByLabel('Body')).toHaveAttribute('aria-invalid', 'true')
    await expect(editor.getByRole('button', { name: 'Save' })).toBeEnabled()
    await expect(page.getByTestId('email-template-leave_rejected').getByTestId('email-template-state')).toHaveText(
      'Default',
    )
  })

  test('a script in the preview runs nothing in the portal', async ({ page }) => {
    let dialogs = 0
    page.on('dialog', async (dialog) => {
      dialogs += 1
      await dialog.dismiss()
    })
    await page.goto('/helixhr/email-templates')
    await page.getByTestId('email-template-leave_cancelled').click()
    const editor = page.getByTestId('email-template-editor')
    await editor.getByLabel('Body').fill('<p>hello</p><script>alert(1)</script>')
    const preview = page.getByTestId('email-template-preview')
    await preview.getByRole('button', { name: 'Update preview' }).click()
    await expect(page.frameLocator('iframe[title="Preview of Leave cancelled"]').getByText('hello')).toBeVisible()
    await expect(page.locator('iframe[title="Preview of Leave cancelled"]')).toHaveAttribute('sandbox', '')
    expect(dialogs).toBe(0)
  })

  test('a locked security notice has no Off control', async ({ page }) => {
    await page.goto('/helixhr/email-templates')
    await page.getByTestId('email-template-bank_change_applied').click()
    const editor = page.getByTestId('email-template-editor')
    await expect(editor.getByText('Security notice: always sent')).toBeVisible()
    await expect(editor.getByTestId('email-template-enabled')).toHaveCount(0)
    await expect(editor.getByLabel('Subject')).toBeDisabled()
  })

  test('P8-U12 moved to the celebrations group: the Portal Admin authors the birthday email and switches to a selected audience', async ({ page }) => {

    await page.goto('/helixhr/email-templates?group=celebrations')
    await expect(page.getByTestId('email-template-group-celebrations')).toBeVisible()

    await page.getByTestId('email-template-celebration-birthday').click()
    await page.getByTestId('celebration-edit').click()
    const form = page.getByTestId('celebration-form')
    await expect(form).toBeVisible()

    const subject = `E2E birthday subject ${Date.now()}`
    await form.getByLabel('Send this reminder').check()
    await form.getByLabel('Subject').fill(subject)
    await form.getByLabel('Body').fill('Cheers, {{ names }}')
    // The per-reminder logo opt-out, saved with the rest of the form.
    const includeLogo = form.getByLabel('Include company logo')
    await includeLogo.check()
    await includeLogo.uncheck()

    // Switch to a selected audience and pick one person; the search returns
    // only the selected company's employees (R12).
    await form.getByLabel('Send to').selectOption('Selected people')
    // A rerun finds the last run's pick saved, and a picked person drops out
    // of the search: clear the list so the pick below is always fresh.
    const picked = form.getByRole('listitem').getByRole('button', { name: 'Remove' })
    while (await picked.count()) await picked.first().click()
    await form.getByLabel('Add a person').fill('Manager')
    const match = form.getByRole('button', { name: /Manager/ }).first()
    await expect(match).toBeVisible()
    await match.click()
    await expect(form.getByText('Nobody selected yet.')).toHaveCount(0)

    await form.getByRole('button', { name: 'Save' }).click()
    await expect(form).toBeHidden()

    // Read back from the server, not just the in-memory response.
    await page.reload()
    await page.getByTestId('email-template-celebration-birthday').click()
    await expect(page.getByTestId('celebration-form')).toHaveCount(0)
    await expect(page.getByRole('tabpanel').or(page.locator('.surface-card')).filter({ hasText: '1 selected' })).toBeVisible()

    await page.getByTestId('celebration-edit').click()
    await expect(page.getByTestId('celebration-form').getByLabel('Subject')).toHaveValue(subject)
    await expect(page.getByTestId('celebration-form').getByLabel('Include company logo')).not.toBeChecked()
    // Back on, so the next run (and the real birthday mail) carries the logo.
    await page.getByTestId('celebration-form').getByLabel('Include company logo').check()
    await page.getByTestId('celebration-form').getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('celebration-form')).toBeHidden()
  })

  test('the holiday event shows a frequency selector and the birthday one does not (R9)', async ({ page }) => {

    await page.goto('/helixhr/email-templates?group=celebrations&event=holiday')
    await expect(page.getByTestId('email-template-celebration-holiday')).toBeVisible()
    await page.getByTestId('celebration-edit').click()
    await expect(page.getByTestId('celebration-form').getByLabel('How often')).toBeVisible()

    await page.getByTestId('email-template-celebration-birthday').click()
    await page.getByTestId('celebration-edit').click()
    await expect(page.getByTestId('celebration-form').getByLabel('How often')).toHaveCount(0)
  })

  test('/settings/celebrations redirects into the new group (R13)', async ({ page }) => {

    await page.goto('/helixhr/settings/celebrations')
    await expect(page).toHaveURL(/group=celebrations/)
    await expect(page.getByTestId('email-template-group-celebrations')).toBeVisible()
  })

  test('switching to Selected people with nobody picked is refused before saving', async ({ page }) => {

    await page.goto('/helixhr/email-templates?group=celebrations&event=work_anniversary')
    await page.getByTestId('celebration-edit').click()

    const form = page.getByTestId('celebration-form')
    await form.getByLabel('Send to').selectOption('Selected people')
    await form.getByRole('button', { name: 'Save' }).click()

    await expect(form.getByRole('alert')).toBeVisible()
    await expect(form).toBeVisible()
  })
})
