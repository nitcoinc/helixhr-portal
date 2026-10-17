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

// CI run 38938677486: the test above typed into the field while the saved
// rule was still loading, the answer overwrote the typed value, and Save
// sent it back unchanged. Held open here, so the order is not left to timing.
test('the Leave rules form stays locked until the saved rule has loaded', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the Leave rules tab is HR-only')

  let release: () => void = () => {}
  const held = new Promise<void>((resolve) => (release = resolve))
  await page.route('**/api/method/helixhr.api.get_leave_rules', async (route) => {
    await held
    await route.continue()
  })

  await page.goto('/helixhr/settings')
  await page.getByTestId('settings-tab-leave-rules').click()
  const section = page.getByTestId('settings-leave-rules')
  await expect(page.getByLabel('Working days back')).toBeDisabled()
  await expect(section.getByRole('button', { name: 'Save' })).toBeDisabled()

  release()
  await expect(page.getByLabel('Working days back')).toBeEnabled()
  await expect(section.getByRole('button', { name: 'Save' })).toBeEnabled()
})

test('U3: Report access and People & access are Portal Admin only', async ({ page }, testInfo) => {
  if (testInfo.project.name === 'portal-admin') {
    await page.goto('/helixhr/settings')
    // Its two sections, and none of the HR ones (the tab list derives from
    // the flags, KTD4/R5).
    await expect(page.getByTestId('settings-tab-report-access')).toBeVisible()
    await expect(page.getByTestId('settings-tab-people-access')).toBeVisible()
    await expect(page.getByTestId('settings-tab-approvers')).toBeVisible()
    await expect(page.getByTestId('settings-tab-categories')).toHaveCount(0)
    await expect(page.getByTestId('settings-tab-leave-rules')).toHaveCount(0)
    await page.getByTestId('settings-tab-people-access').click()
    await expect(page.getByTestId('settings-tab-people-access')).toHaveAttribute('aria-current', 'page')
    // Plan 2026-10-15-001 U6: the replaced sections' old URLs land here.
    for (const old of ['portal-roles', 'company-access']) {
      await page.goto(`/helixhr/settings/${old}`)
      await expect(page).toHaveURL(/\/settings\/people-access$/)
    }
    return
  }

  test.skip(testInfo.project.name !== 'hr', 'covered by the portal-admin branch above')

  // An HR Manager keeps the HR sections and no longer sees the two admin
  // tabs (R5).
  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
  await expect(page.getByTestId('settings-tab-leave-rules')).toBeVisible()
  await expect(page.getByTestId('settings-tab-report-access')).toHaveCount(0)
  await expect(page.getByTestId('settings-tab-people-access')).toHaveCount(0)
  await expect(page.getByTestId('settings-tab-approvers')).toHaveCount(0)
})

test('plan 2026-10-07-001 U4: the Approvers cleanup previews in two groups', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'portal-admin', 'the Approvers tab is Portal Admin only')

  await page.goto('/helixhr/settings/approvers')
  await expect(page.getByTestId('settings-tab-approvers')).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { name: /^Will change \(\d+\)$/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /^Needs HR attention \(\d+\)$/ })).toBeVisible()

  // Nothing applies until a person is picked and the confirm step is passed.
  const fix = page.getByTestId('approvers-fix')
  if (await fix.count()) {
    await expect(fix).toBeDisabled()
    await page.getByRole('checkbox', { name: /^Fix / }).first().check()
    await fix.click()
    await expect(page.getByTestId('approvers-confirm')).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByTestId('approvers-confirm')).toHaveCount(0)
  } else {
    await expect(page.getByTestId('approvers-in-line')).toBeVisible()
  }
})

// Plan 2026-10-17-001 U11 (AI-05, AI-06): the Report access matrix, shorter
// and with one explanation instead of seventeen.
const ACCESS_FLAGS = ['hr_user_run', 'hr_user_export', 'dm_run', 'dm_export'] as const

test('U11: Report access fits a phone in folded families with a count each', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'portal-admin', 'Report access is Portal Admin only')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/helixhr/settings/report-access')
  const leave = page.getByTestId('access-family-leave')
  await expect(leave.locator('summary')).toHaveText(/Leave\s*·\s*\d+ of \d+ on for HR User/)

  // Was 4,546px with one card per report (AI-06).
  const height = await page.evaluate(() => document.documentElement.scrollHeight)
  expect(height).toBeLessThanOrEqual(1600)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  // Opened, a company-only report has no Delivery Manager controls at all.
  await leave.locator('summary').click()
  await expect(leave.getByRole('checkbox', { name: 'Leave ledger: HR User run', exact: true })).toBeVisible()
  await expect(leave.getByRole('checkbox', { name: 'Leave ledger: Delivery Manager run', exact: true })).toHaveCount(0)
})

test('U11: a not-applicable Delivery Manager cell is a dash, out of the Tab order, described by the column footnote', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'portal-admin', 'Report access is Portal Admin only')
  await page.goto('/helixhr/settings/report-access')
  const dash = page.getByTestId('access-na-leave_ledger-dm_run')
  await expect(dash).toBeVisible()
  await expect(dash).toHaveText('—')
  await expect(dash).toHaveAccessibleName('Leave ledger: Delivery Manager run: not available')
  await expect(dash).toHaveAccessibleDescription(/no project scope/)
  expect(await dash.evaluate((el) => el.tabIndex)).toBe(-1)
  // The reason is said once, under the column header -- not per row.
  await expect(page.getByText('no project scope')).toHaveCount(1)
  await expect(page.getByRole('checkbox', { name: 'Leave ledger: Delivery Manager run', exact: true })).toHaveCount(0)

  // Tab from the row's last real control skips both dashes.
  await page.getByRole('checkbox', { name: 'Leave ledger: HR User export', exact: true }).focus()
  await page.keyboard.press('Tab')
  const next = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || '')
  expect(next).not.toMatch(/^Leave ledger: Delivery Manager/)
})

test('U11: a column select-all saves every row in its family', async ({ page, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== 'portal-admin', 'Report access is Portal Admin only')
  const api = await loginAs(baseURL!, 'portal-admin@helixhr.test')
  const before = (await callMethod(api, 'helixhr.api.get_report_access', {})) as Array<Record<string, unknown>>
  const family = before.filter((row) => row.family === 'attendance')
  expect(family.length, 'the fixture catalog has Attendance reports').toBeGreaterThan(1)
  const target = !family.every((row) => row.hr_user_run)
  const changing = family.filter((row) => !!row.hr_user_run !== target).length
  try {
    await page.goto('/helixhr/settings/report-access')
    const all = page.getByTestId('access-all-attendance-hr_user_run')
    await all.setChecked(target)
    await expect(page.getByTestId('access-save-bar')).toContainText(`${changing} unsaved change`)
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText(new RegExp(`^Saved ${changing} reports?\\.$`))).toBeVisible()

    await page.reload()
    for (const row of family) {
      await expect(page.getByRole('checkbox', { name: `${row.label}: HR User run`, exact: true })).toBeChecked({
        checked: target,
      })
    }
    await expect(all).toBeChecked({ checked: target })
    const after = (await callMethod(api, 'helixhr.api.get_report_access', {})) as Array<Record<string, unknown>>
    expect(after.filter((row) => row.family === 'attendance').every((row) => !!row.hr_user_run === target)).toBe(true)
  } finally {
    await callMethod(api, 'helixhr.api.save_report_access', {
      rows: family.map((row) => ({ key: row.key, ...Object.fromEntries(ACCESS_FLAGS.map((flag) => [flag, row[flag]])) })),
    })
    await api.dispose()
  }
})
