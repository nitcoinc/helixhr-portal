import { test, expect, request, APIRequestContext } from '@playwright/test'

// P5-U14. Settings is HR-only, and the plan's own test list asks for two
// things the Python suite cannot prove on its own: that a direct navigation
// by a non-HR identity is refused by the SERVER (not merely hidden by nav),
// and that changing a category's route through this screen actually changes
// where the next request lands, end to end.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

async function loginAs(baseURL: string, user: string, pwd = PASSWORD): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  const response = await api.post('/api/method/login', { form: { usr: user, pwd } })
  if (!response.ok()) {
    throw new Error(`Login failed for ${user}: ${response.status()} ${await response.text()}`)
  }
  return api
}

async function getValue(api: APIRequestContext, doctype: string, filters: object, fieldname: string) {
  const response = await api.get(
    `/api/method/frappe.client.get_value?doctype=${encodeURIComponent(doctype)}&filters=` +
      encodeURIComponent(JSON.stringify(filters)) +
      `&fieldname=${fieldname}`,
  )
  return (await response.json())?.message?.[fieldname]
}

async function callMethod(api: APIRequestContext, method: string, data: object) {
  const response = await api.post(`/api/method/${method}`, { data });
  if (!response.ok()) {
    throw new Error(`${method} failed: ${response.status()} ${await response.text()}`)
  }
  return (await response.json())?.message
}

test('an HR identity reaches Settings; an employee has no nav entry and the server refuses a direct hit', async ({
  page,
  baseURL,
}, testInfo) => {
  if (testInfo.project.name === 'hr') {
    await page.goto('/helixhr/settings')
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
    await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible()

    // P8-U6: every section offers its own Desk link, server-gated by
    // `_can_open_desk` -- present for this System User HR identity.
    await expect(page.getByTestId('settings-desk-link')).toBeVisible()
    await page.getByTestId('settings-tab-leave-types').click()
    await expect(page.getByTestId('settings-desk-link')).toHaveAttribute('href', /\/desk\/leave-type$/)
    return
  }

  test.skip(testInfo.project.name !== 'employee', 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Settings' })).toHaveCount(0)

  // The server's own gate, not a client-side redirect: `get_portal_config`
  // throws PermissionError for a non-HR caller, and AsyncState renders that
  // as its 'forbidden' region.
  await page.goto('/helixhr/settings')
  await expect(page.locator('[data-async-state="settings:forbidden"]')).toBeVisible()
  await expect(page.getByText("You don't have access to this")).toBeVisible()
})

test('creating a usable leave type touches no more than the seven named fields', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-leave-types').click()
  await page.getByRole('button', { name: 'New leave type' }).click()

  const form = page.getByTestId('settings-leave-type-form')
  await expect(form).toBeVisible()
  // P5-KTD12: exactly the named set -- name, per-period allocation,
  // longest single request (U2), allow-negative (U2), carry-forward, LWP,
  // HR-approves. Counting inputs is what catches a "just one more field for
  // convenience" regression that a screenshot would not.
  await expect(form.locator('input, textarea, select')).toHaveCount(7)
})

test('U2: the longest-single-request limit saves and shows on the row', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  const name = `E2E U2 limit ${Date.now()}`
  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-leave-types').click()
  await page.getByRole('button', { name: 'New leave type' }).click()

  const form = page.getByTestId('settings-leave-type-form')
  await form.getByLabel('Name').fill(name)
  await form.getByLabel('Longest single request, in days').fill('3')
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('dialog')).toBeHidden()

  const row = page.getByTestId('settings-leave-type-row').filter({ hasText: name })
  await expect(row).toContainText('at most 3 in one request')
})

test('P8-U5: the leave type editor is a dialog, titled with the row being edited, closed by Escape', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-leave-types').click()

  const row = page.getByTestId('settings-leave-type-row').first()
  const rowName = (await row.locator('p').first().textContent())?.trim()
  await row.getByRole('button', { name: 'Edit' }).click()

  // A dialog, not an inline block appended after the whole list -- so
  // editing the row is visible without scrolling regardless of how many
  // leave types are above it, and its own title says which one is open.
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('heading', { name: new RegExp(`^Edit ${rowName}$`) })).toBeVisible()
  await expect(page.getByTestId('settings-leave-type-form')).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
})

test("changing a category's routed role changes where the next request goes", async ({
  page,
  baseURL,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')
  if (!baseURL) throw new Error('BASE_URL is required')

  const admin = await loginAs(baseURL, 'Administrator', 'admin')
  const originalRoute = await getValue(admin, 'HelixHR Request Category', { name: 'IT / Asset' }, 'route_to_role')

  const employee = await loginAs(baseURL, 'employee@helixhr.test')

  try {
    const before = await callMethod(employee, 'helixhr.api.create_my_request', {
      category: 'IT / Asset',
      subject: 'Settings e2e: before the route change',
      details: 'seeded by settings.spec.ts',
      operation_key: `settings-e2e-before-${Date.now()}`,
    })
    const beforeRoute = await getValue(admin, 'HR Request', { name: before.name }, 'routed_to_role')
    expect(beforeRoute).toBe(originalRoute)

    await page.goto('/helixhr/settings')
    const row = page.getByTestId('settings-category-row').filter({ hasText: 'IT / Asset' })
    await row.getByRole('button', { name: 'Edit' }).click()
    const form = page.getByTestId('settings-category-form')
    await form.getByLabel('Routes to').selectOption('HR Manager')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(form).toBeHidden()

    const after = await callMethod(employee, 'helixhr.api.create_my_request', {
      category: 'IT / Asset',
      subject: 'Settings e2e: after the route change',
      details: 'seeded by settings.spec.ts',
      operation_key: `settings-e2e-after-${Date.now()}`,
    })
    const afterRoute = await getValue(admin, 'HR Request', { name: after.name }, 'routed_to_role')
    expect(afterRoute).toBe('HR Manager')
  } finally {
    await admin.post('/api/method/frappe.client.set_value', {
      data: { doctype: 'HelixHR Request Category', name: 'IT / Asset', fieldname: 'route_to_role', value: originalRoute },
    })
    await admin.dispose()
    await employee.dispose()
  }
})

test('message text left Settings for the Email templates page (plan 2026-10-02-001 U10)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  // Wording belongs to the Email templates page (Portal Admin / System Manager).
  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
  await expect(page.getByTestId('settings-tab-templates')).toHaveCount(0)
})

test('the celebrations section left Settings for the Email templates page (plan 2026-10-04-004 U5)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-celebrations')).toHaveCount(0)
})

test('U2: HR edits the backdated leave rule and sees it after a reload', async ({ page, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the Leave rules tab is HR-only')
  if (!baseURL) throw new Error('BASE_URL is required')

  // Put the rule back exactly as it was; this suite shares one site.
  const admin = await loginAs(baseURL, 'Administrator', 'admin')
  const original = await callMethod(admin, 'helixhr.api.get_leave_rules', {})

  try {
    await page.goto('/helixhr/settings')
    await page.getByTestId('settings-tab-leave-rules').click()
    await expect(page.getByTestId('settings-leave-rules')).toBeVisible()

    await page.getByLabel('Working days back').fill('2')
    await page.getByTestId('settings-leave-rules').getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('Saved.')).toBeVisible()

    await page.reload()
    await expect(page.getByLabel('Working days back')).toHaveValue('2')

    // The server's refusal reaches the form (review fix: this branch had no
    // coverage). 400 is outside the doctype's 0-365 range.
    await page.getByLabel('Working days back').fill('400')
    await page.getByTestId('settings-leave-rules').getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('alert')).toContainText('between 0 and 365')
  } finally {
    await callMethod(admin, 'helixhr.api.save_leave_rules', {
      backdated_grace_days: original.backdated_grace_days,
      backdated_exempt_role: original.backdated_exempt_role,
    })
    await admin.dispose()
  }
})

test('U3: Report access and Portal roles are Portal Admin only', async ({ page }, testInfo) => {
  if (testInfo.project.name === 'portal-admin') {
    await page.goto('/helixhr/settings')
    // Its two sections, and none of the HR ones (the tab list derives from
    // the flags, KTD4/R5).
    await expect(page.getByTestId('settings-tab-report-access')).toBeVisible()
    await expect(page.getByTestId('settings-tab-portal-roles')).toBeVisible()
    await expect(page.getByTestId('settings-tab-categories')).toHaveCount(0)
    await expect(page.getByTestId('settings-tab-leave-rules')).toHaveCount(0)
    await page.getByTestId('settings-tab-portal-roles').click()
    await expect(page.getByTestId('settings-tab-portal-roles')).toHaveAttribute('aria-current', 'page')
    return
  }

  test.skip(testInfo.project.name !== 'hr', 'covered by the portal-admin branch above')

  // An HR Manager keeps the HR sections and no longer sees the two admin
  // tabs (R5).
  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
  await expect(page.getByTestId('settings-tab-leave-rules')).toBeVisible()
  await expect(page.getByTestId('settings-tab-report-access')).toHaveCount(0)
  await expect(page.getByTestId('settings-tab-portal-roles')).toHaveCount(0)
})
