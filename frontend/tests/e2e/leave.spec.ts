import { test, expect, request, APIRequestContext } from '@playwright/test'

// P2-U5. Leave, end to end: the ask sheet's server-derived preview, the
// manager's reason on a sent-back request, and withdrawal by lifecycle.
//
// The sent-back and withdrawable records are seeded through the API rather
// than produced by driving two identities through the UI. Both are *inputs*
// to what this file is about -- what the employee sees and may do about a
// decision that has already been made -- and the approval path itself is
// covered by timesheet-approval.spec.ts and the Python approval suite.

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const EMPLOYEE = 'employee@helixhr.test'

async function admin(baseURL: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  return api
}

async function asEmployee(baseURL: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: EMPLOYEE, pwd: PASSWORD } })
  return api
}

async function getValue(api: APIRequestContext, doctype: string, filters: object, field: string) {
  const response = await api.get(
    `/api/method/frappe.client.get_value?doctype=${encodeURIComponent(doctype)}&filters=` +
      encodeURIComponent(JSON.stringify(filters)) +
      `&fieldname=${field}`,
  )
  return (await response.json())?.message?.[field]
}

/** A calendar date `offset` days from today, kept inside the current year so
 * it stays inside the fixture's Leave Allocation period. Late in the year the
 * same offset is taken backwards instead; a back-dated request is as valid an
 * input to these assertions as a future one. */
function seedDate(offset: number) {
  const now = new Date()
  const forward = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset)
  const chosen =
    forward.getFullYear() === now.getFullYear()
      ? forward
      : new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${chosen.getFullYear()}-${pad(chosen.getMonth() + 1)}-${pad(chosen.getDate())}`
}

/** Remove anything a previous run left on this date, then create one Leave
 * Application there **as the employee**, through the portal's own method.
 *
 * The identity is load-bearing rather than incidental. The Leave Application
 * permission is `if_owner`, and `_leave_reason` only returns comments written
 * by somebody other than the record's owner -- an Administrator-created record
 * is therefore both undeletable by the employee and unable to carry a
 * manager's reason, which is not the state any real record is ever in.
 */
async function seedLeave(
  api: APIRequestContext,
  employeeApi: APIRequestContext,
  employee: string,
  date: string,
  options: { reject?: string } = {},
) {
  const existing = await api.get(
    '/api/method/frappe.client.get_list?doctype=Leave%20Application&filters=' +
      encodeURIComponent(JSON.stringify({ employee, from_date: date })) +
      '&fields=' +
      encodeURIComponent(JSON.stringify(['name'])) +
      '&limit_page_length=0',
  )
  for (const row of (await existing.json())?.message || []) {
    await api.post('/api/method/frappe.client.delete', {
      data: { doctype: 'Leave Application', name: row.name },
    })
  }

  const created = await employeeApi.post('/api/method/helixhr.api.apply_for_leave', {
    data: {
      leave_type: 'Casual Leave',
      from_date: date,
      to_date: date,
      description: 'Seeded by leave.spec.ts',
    },
  })
  const name = (await created.json())?.message?.name
  expect(name, 'seeding a Leave Application should succeed').toBeTruthy()

  if (options.reject) {
    // Administrator is an authorized actor in act_on_approval, so this is the
    // real portal path a rejection takes -- not a raw status write.
    const rejected = await api.post('/api/method/helixhr.api.act_on_approval', {
      data: {
        doctype: 'Leave Application',
        name,
        action: 'Reject',
        comment: options.reject,
      },
    })
    expect(rejected.ok(), await rejected.text()).toBeTruthy()
  }
  return name
}

async function removeLeave(api: APIRequestContext, name: string) {
  if (!name) return
  await api.post('/api/method/frappe.client.delete', {
    data: { doctype: 'Leave Application', name },
  })
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  // ── The ask sheet ─────────────────────────────────────────────────────
  test('the ask sheet shows the server-derived days and the approver before Send', async ({
    page,
  }) => {
    await page.goto('/helixhr/leave')
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByRole('heading', { name: 'Leave' })).toBeVisible()

    await page.getByRole('button', { name: 'Ask for leave' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    // The type is a chip carrying its own balance, not a select.
    const casual = dialog.getByRole('button', { name: /^Casual/ })
    await expect(casual).toBeVisible()
    await casual.click()
    await expect(casual).toHaveAttribute('aria-pressed', 'true')

    // Server-derived, from HRMS's own get_number_of_leave_days -- the browser
    // never counts days itself (P2-U5 scenario 2).
    const preview = dialog.locator('[data-testid="leave-preview"]')
    await expect(preview).toContainText(/working days?/)
    await expect(preview).toContainText('Goes to Manager')

    // Named before sending, so nobody has to guess where it went.
    await expect(dialog.getByRole('button', { name: 'Send to Manager' })).toBeEnabled()

    // Half day collapses the request to one day: To follows From and is no
    // longer editable, and there is no third date field to fall out of step
    // with either of them (scenario 3).
    await dialog.getByText('Half day', { exact: false }).first().click()
    await expect(dialog.getByLabel('To')).toBeDisabled()
    await expect(dialog.getByLabel('Half-day date')).toHaveCount(0)
  })

  // ── Scenario 1: the manager's reason, where the decision is ───────────
  test('a sent-back leave quotes the manager and offers Edit and resend', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const employeeApi = await asEmployee(baseURL!)
    const employee = await getValue(api, 'Employee', { user_id: EMPLOYEE }, 'name')
    const reason = 'Team offsite that day, can you shift it?'
    const name = await seedLeave(api, employeeApi, employee, seedDate(31), { reject: reason })

    try {
      await page.goto('/helixhr/leave')
      const row = page.locator('li', { hasText: 'Sent back' }).first()
      await expect(row).toBeVisible()
      await expect(row).toContainText(reason)
      await expect(row.getByRole('button', { name: 'Edit and resend' })).toBeVisible()

      // The same reason on the record's own URL, which is where Home and the
      // notification both land (P2-R12).
      await page.goto(`/helixhr/leave/${name}`)
      const detail = page.locator('[data-async-state^="leave-detail"]')
      await expect(detail).toContainText(reason)
      await expect(detail).toContainText('Sent back')

      // Edit and resend opens the ask sheet with the sent-back request's own
      // values already in it.
      await detail.getByRole('button', { name: 'Edit and resend' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await expect(page.getByRole('dialog').getByRole('button', { name: /^Casual/ })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
    } finally {
      await removeLeave(api, name)
      await employeeApi.dispose()
      await api.dispose()
    }
  })

  // ── Scenario 5: withdrawal is confirmed, and removes only that row ────
  test('withdrawing an open leave takes confirmation and then removes it', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const employeeApi = await asEmployee(baseURL!)
    const employee = await getValue(api, 'Employee', { user_id: EMPLOYEE }, 'name')
    let name = await seedLeave(api, employeeApi, employee, seedDate(35))

    try {
      await page.goto(`/helixhr/leave/${name}`)
      const detail = page.locator('[data-async-state^="leave-detail"]')
      await expect(detail).toContainText('Waiting for Manager')

      await detail.getByRole('button', { name: 'Withdraw' }).click()
      const confirm = page.getByRole('dialog')
      await expect(confirm).toContainText('Withdraw this leave request?')

      // Backing out leaves the record alone.
      await confirm.getByRole('button', { name: 'Keep it' }).click()
      await expect(page).toHaveURL(new RegExp(`/leave/${name}$`))

      await detail.getByRole('button', { name: 'Withdraw' }).click()
      await page.getByRole('dialog').getByRole('button', { name: 'Withdraw' }).click()

      // Back on the list, with the record gone and the balances refreshed
      // from the same single response.
      await expect(page).toHaveURL(/\/leave$/)
      await expect(page.locator('[data-async-state="leave-balances:ready"]')).toBeVisible()
      const gone = await getValue(api, 'Leave Application', { name }, 'name')
      expect(gone).toBeFalsy()
      name = ''
    } finally {
      await removeLeave(api, name)
      await employeeApi.dispose()
      await api.dispose()
    }
  })

  // ── Grouping and lifecycle copy ───────────────────────────────────────
  test('leave is grouped Coming up / Past rather than filtered by pills', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const employeeApi = await asEmployee(baseURL!)
    const employee = await getValue(api, 'Employee', { user_id: EMPLOYEE }, 'name')
    const name = await seedLeave(api, employeeApi, employee, seedDate(39))

    try {
      await page.goto('/helixhr/leave')
      await page.waitForLoadState('networkidle')

      // The four filter pills are gone; the grouping is a label over a run of
      // cards, which needs no interaction to read.
      await expect(page.getByRole('button', { name: 'Sent back', exact: true })).toHaveCount(0)
      const labels = page.locator('h2.label')
      await expect(labels.first()).toBeVisible()
      for (const text of await labels.allTextContents()) {
        expect(['Coming up', 'Past']).toContain(text.trim())
      }
    } finally {
      await removeLeave(api, name)
      await employeeApi.dispose()
      await api.dispose()
    }
  })
})
