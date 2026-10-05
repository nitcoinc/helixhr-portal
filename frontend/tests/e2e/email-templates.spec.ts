import { test, expect, request, type Page } from '@playwright/test'

// Plan 2026-10-02-001 U10; role-sectioned since plan 2026-10-04-004 U5
// (KTD3): the HR Manager gets the "Celebrations & holidays" group and the
// Notification Manager keeps the message templates, each served by its own
// server-gated endpoints. Runs in the `hr` project; the Notification
// Manager tests sign in as that identity (seeded by
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

async function signInAsNotificationManager(page: Page, baseURL: string | undefined) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  const login = await api.post('/api/method/login', {
    form: { usr: 'notification-manager@helixhr.test', pwd: PASSWORD },
  })
  expect(login.ok()).toBeTruthy()
  const state = await api.storageState()
  await api.dispose()
  await page.context().clearCookies()
  await page.context().addCookies(state.cookies)
}

test('the HR Manager opens the page on the celebrations group only (plan 2026-10-04-004 U5)', async ({ page }) => {
  // KTD3 inverted P8's assertion: the page is role-sectioned now, and the
  // HR Manager's own gate is the celebration group's endpoints.
  await page.goto('/helixhr/settings')
  await expect(page.getByRole('link', { name: 'Email templates' }).first()).toBeVisible()

  await page.goto('/helixhr/email-templates')
  await expect(page.getByTestId('email-template-group-celebrations')).toBeVisible()
  await expect(page.getByTestId('email-template-group-messages')).toHaveCount(0)
  await expect(page.getByTestId('email-template-list')).toHaveCount(0)
})

test.describe('Notification Manager', () => {
  test.describe.configure({ mode: 'serial' })
  test.use({ storageState: { cookies: [], origins: [] } })

  test.beforeEach(async ({ page, baseURL }) => {
    await signInAsNotificationManager(page, baseURL)
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
})

test('one company logo sits atop the page; each template may opt out of it', async ({ page, baseURL }) => {
  // Administrator: a System Manager may both edit message templates and set
  // the logo. The brand is the one `send_notification` resolves -- for a
  // caller with no Employee, the default company -- so pin one for the test.
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  expect((await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })).ok()).toBeTruthy()
  // Global Defaults.default_company is what `frappe.defaults` reads.
  const globals = await (
    await api.get('/api/method/frappe.client.get_value', {
      params: { doctype: 'Global Defaults', name: 'Global Defaults', fieldname: 'default_company' },
    })
  ).json()
  const previous = globals.message?.default_company || ''
  const company =
    previous ||
    (await (await api.get('/api/method/frappe.client.get_list', { params: { doctype: 'Company' } })).json())
      .message[0].name
  const setDefault = (value: string) =>
    api.post('/api/method/frappe.client.set_value', {
      form: { doctype: 'Global Defaults', name: 'Global Defaults', fieldname: 'default_company', value },
    })
  if (!previous) expect((await setDefault(company)).ok()).toBeTruthy()

  await page.context().clearCookies()
  await page.context().addCookies((await api.storageState()).cookies)
  try {
    await page.goto('/helixhr/email-templates')
    // The logo control is company-wide: once, above the list, before any
    // template is open -- never per template.
    const logo = page.getByTestId('company-logo')
    await expect(logo).toHaveCount(1)
    await expect(logo).toContainText(company)
    const item = page.getByTestId('email-template-leave_approved')
    await item.click()
    const preview = page.getByTestId('email-template-preview')
    await expect(preview.getByTestId('company-logo')).toHaveCount(0)
    await logo.getByTestId('company-logo-input').setInputFiles({ name: 'logo.png', mimeType: 'image/png', buffer: PNG })
    await expect(logo.getByTestId('company-logo-status')).toContainText('Logo saved')
    await expect(logo.getByRole('img')).toHaveAttribute('src', /\/files\/.+\.png$/)
    const frame = page.frameLocator('iframe[title="Preview of Leave approved"]')
    await expect(frame.locator('img').first()).toHaveAttribute('src', /\/files\/.+\.png$/)

    // Per template: unticking "Include company logo" swaps the image for the
    // company name in the preview, and saving it keeps the default wording.
    const editor = page.getByTestId('email-template-editor')
    const include = editor.getByLabel('Include company logo')
    await expect(include).toBeChecked()
    await include.uncheck()
    await expect(frame.locator('img')).toHaveCount(0)
    await expect(frame.getByText(company).first()).toBeVisible()
    await editor.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('email-template-status')).toHaveText('Saved.')
    await expect(item.getByTestId('email-template-state')).toHaveText('Default')
    await page.reload()
    await page.getByTestId('email-template-leave_approved').click()
    await expect(page.getByTestId('email-template-editor').getByLabel('Include company logo')).not.toBeChecked()
    await page.getByTestId('email-template-editor').getByLabel('Include company logo').check()
    await expect(frame.locator('img').first()).toHaveAttribute('src', /\/files\/.+\.png$/)
    await page.getByTestId('email-template-editor').getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('email-template-status')).toHaveText('Saved.')

    await logo.getByRole('button', { name: 'Remove' }).click()
    await expect(logo.getByTestId('company-logo-status')).toHaveText('Logo removed.')
    await expect(frame.locator('img')).toHaveCount(0)
    await expect(frame.getByText(company).first()).toBeVisible()
  } finally {
    await api.post('/api/method/helixhr.api.save_message_template', {
      form: { template_key: 'leave_approved', hide_logo: 0 },
    })
    await api.post('/api/method/helixhr.api.set_company_logo', { form: { company, remove: 1 } })
    if (!previous) await setDefault('')
    await api.dispose()
  }
})

test('P8-U12 moved to the celebrations group: HR authors the birthday email and switches to a selected audience', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the celebrations group is HR-only')

  await page.goto('/helixhr/email-templates?group=celebrations')
  await expect(page.getByTestId('email-template-group-celebrations')).toBeVisible()
  await expect(page.getByTestId('celebration-company-select')).toHaveCount(0, 'one company in scope, no selector')

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

test('the holiday event shows a frequency selector and the birthday one does not (R9)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the celebrations group is HR-only')

  await page.goto('/helixhr/email-templates?group=celebrations&event=holiday')
  await expect(page.getByTestId('email-template-celebration-holiday')).toBeVisible()
  await page.getByTestId('celebration-edit').click()
  await expect(page.getByTestId('celebration-form').getByLabel('How often')).toBeVisible()

  await page.getByTestId('email-template-celebration-birthday').click()
  await page.getByTestId('celebration-edit').click()
  await expect(page.getByTestId('celebration-form').getByLabel('How often')).toHaveCount(0)
})

test('/settings/celebrations redirects into the new group (R13)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  await page.goto('/helixhr/settings/celebrations')
  await expect(page).toHaveURL(/group=celebrations/)
  await expect(page.getByTestId('email-template-group-celebrations')).toBeVisible()
})

test('switching to Selected people with nobody picked is refused before saving', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the celebrations group is HR-only')

  await page.goto('/helixhr/email-templates?group=celebrations&event=work_anniversary')
  await page.getByTestId('celebration-edit').click()

  const form = page.getByTestId('celebration-form')
  await form.getByLabel('Send to').selectOption('Selected people')
  await form.getByRole('button', { name: 'Save' }).click()

  await expect(form.getByRole('alert')).toBeVisible()
  await expect(form).toBeVisible()
})
