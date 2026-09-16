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

test('creating a usable leave type touches no more than the five named fields', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-leave-types').click()
  await page.getByRole('button', { name: 'New leave type' }).click()

  const form = page.getByTestId('settings-leave-type-form')
  await expect(form).toBeVisible()
  // P5-KTD12: exactly the named set -- name, maximum days, carry-forward,
  // LWP, HR-approves. Counting inputs is what catches a "just one more
  // field for convenience" regression that a screenshot would not.
  await expect(form.locator('input, textarea, select')).toHaveCount(5)
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

test('editing a template and reloading shows the edit', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  const stamp = `Settings e2e ${Date.now()}`
  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-templates').click()

  const row = page
    .getByTestId('settings-template-row')
    .filter({ hasText: 'request_arrival' })
  await row.getByRole('button', { name: 'Edit' }).click()
  const form = page.getByTestId('settings-template-form')
  await form.getByLabel('Subject').fill(stamp)
  await form.getByLabel('Use this wording instead of the default').check()
  await form.getByRole('button', { name: 'Save' }).click()
  await expect(form).toBeHidden()

  await page.reload()
  await page.getByTestId('settings-tab-templates').click()
  await page
    .getByTestId('settings-template-row')
    .filter({ hasText: 'request_arrival' })
    .getByRole('button', { name: 'Edit' })
    .click()
  await expect(page.getByTestId('settings-template-form').getByLabel('Subject')).toHaveValue(stamp)
  // Whether the next arrival mail actually renders this wording is proved
  // server-side (helixhr/tests/test_api_config.py, test_reminders.py) --
  // firing a real request and inspecting the Email Queue body here would
  // duplicate that coverage for no new signal, at the cost of a slow,
  // mail-account-dependent e2e test.
})

test('a rejected save keeps the user input', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'settings is an HR-only screen')

  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-templates').click()
  const row = page
    .getByTestId('settings-template-row')
    .filter({ hasText: 'request_status_changed' })
  await row.getByRole('button', { name: 'Edit' }).click()

  const form = page.getByTestId('settings-template-form')
  const tooLong = 'x'.repeat(141)
  await form.getByLabel('Subject').fill(tooLong)
  await form.getByRole('button', { name: 'Save' }).click()

  await expect(form.getByRole('alert')).toBeVisible()
  await expect(form.getByLabel('Subject')).toHaveValue(tooLong)
})
