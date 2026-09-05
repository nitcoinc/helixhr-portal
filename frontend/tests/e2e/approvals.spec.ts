import { test, expect, request, APIRequestContext } from '@playwright/test'

// P2-U7. The two things the Python suite cannot prove: that the evidence is
// on screen *before* the decision is available (P2-AE6 in the browser), and
// that a decision made against a record that moved underneath the manager is
// refused with an explanation rather than silently applied.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const EMPLOYEE = 'employee@helixhr.test'
const PROJECT_NAME = 'Approvals Spec Project'

/** A week well clear of the current one, which timesheet-entry.spec.ts and
 * timesheet-approval.spec.ts both work in. One Timesheet per Monday-Sunday
 * week (KTD10), so two specs sharing a week would fight over one record. */
function seedMonday(): string {
  const now = new Date()
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7) - 35)
  return monday.toISOString().slice(0, 10)
}

function addDays(date: string, days: number): string {
  const parsed = new Date(`${date}T00:00:00Z`)
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return parsed.toISOString().slice(0, 10)
}

async function adminContext(baseURL: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
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

/**
 * A pending week for the employee, on a week of this spec's own, submitted
 * through the portal's own method so the workflow transition and the
 * Pending-Approval DocShare are the real ones rather than a fixture's
 * imitation of them.
 */
async function seedPendingWeek(baseURL: string) {
  const admin = await adminContext(baseURL)
  const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
  const company = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'company')

  let project = await getValue(admin, 'Project', { project_name: PROJECT_NAME }, 'name')
  if (!project) {
    const created = await admin.post('/api/method/frappe.client.insert', {
      data: {
        doc: JSON.stringify({
          doctype: 'Project',
          project_name: PROJECT_NAME,
          status: 'Open',
          company,
        }),
      },
    })
    project = (await created.json()).message.name
  }
  const permission = await getValue(
    admin,
    'User Permission',
    { user: EMPLOYEE, allow: 'Project', for_value: project },
    'name',
  )
  if (!permission) {
    await admin.post('/api/method/frappe.client.insert', {
      data: {
        doc: JSON.stringify({
          doctype: 'User Permission',
          user: EMPLOYEE,
          allow: 'Project',
          for_value: project,
        }),
      },
    })
  }

  const monday = seedMonday()
  // Start from nothing: a previous run left this week Approved, and an
  // approved week has no decision left in it.
  const existing = await admin.get(
    '/api/method/frappe.client.get_list?doctype=Timesheet&filters=' +
      encodeURIComponent(JSON.stringify({ employee, start_date: ['between', [monday, addDays(monday, 6)]] })) +
      '&fields=' +
      encodeURIComponent(JSON.stringify(['name', 'docstatus'])) +
      '&limit_page_length=0',
  )
  for (const row of (await existing.json())?.message || []) {
    if (row.docstatus === 1) {
      await admin.post('/api/method/frappe.client.cancel', {
        form: { doctype: 'Timesheet', name: row.name },
      })
    }
    await admin.post('/api/method/frappe.client.delete', {
      form: { doctype: 'Timesheet', name: row.name },
    })
  }
  await admin.dispose()

  const employeeApi = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await employeeApi.post('/api/method/login', { form: { usr: EMPLOYEE, pwd: PASSWORD } })
  const submitted = await employeeApi.post('/api/method/helixhr.api.submit_my_week', {
    form: {
      week_start: monday,
      rows: JSON.stringify([
        { date: monday, project, task: '', hours: 8, note: 'Long Monday, doctor on Friday' },
        { date: addDays(monday, 1), project, task: '', hours: 6.5, note: '' },
      ]),
    },
  })
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  await employeeApi.dispose()

  const admin2 = await adminContext(baseURL)
  const name = await getValue(admin2, 'Timesheet', { employee, start_date: monday }, 'name')
  await admin2.dispose()
  return { name, monday }
}

test('a manager reads the whole week before Approve exists, and a stale decision is refused', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(60000)

  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const { name } = await seedPendingWeek(baseURL)

  await page.goto('/helixhr/approvals')
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()

  const queue = page.getByTestId('approvals-queue')
  // By record, not by position: the queue is oldest-first across both kinds,
  // so whatever else this site is holding, this is the week under test.
  const row = queue.locator(`[data-approval-name="${name}"]`)
  await expect(row).toBeVisible({ timeout: 10000 })

  // P2-AE6: nothing to decide with until the evidence is on screen.
  await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(0)

  await row.click()
  await expect(page).toHaveURL(new RegExp(`/helixhr/approvals/timesheet/${name}$`))

  const panel = page.getByTestId('approval-detail')
  await expect(panel.getByText('Day total')).toBeVisible({ timeout: 10000 })
  await expect(panel.getByText(PROJECT_NAME)).toBeVisible()
  await expect(panel.getByText(/Long Monday, doctor on Friday/)).toBeVisible()
  await expect(panel.getByRole('button', { name: /^Approve/ })).toBeVisible()

  // Refresh lands on the same decision (KTD5).
  await page.reload()
  await expect(page.getByTestId('approval-detail').getByText('Day total')).toBeVisible({
    timeout: 10000,
  })

  // Now move the record under the manager, exactly as a second approver or
  // an HR edit would, and decide against what is still on screen.
  const admin = await adminContext(baseURL)
  const changed = await admin.post('/api/method/frappe.client.set_value', {
    form: { doctype: 'Timesheet', name, fieldname: 'note', value: 'HR touched this' },
  })
  expect(changed.ok(), await changed.text()).toBeTruthy()
  await admin.dispose()

  await page.getByTestId('approval-detail').getByRole('button', { name: /^Approve/ }).click()
  await expect(page.getByRole('alert').filter({ hasText: /Reload/ })).toBeVisible({ timeout: 10000 })

  // Refused, not applied -- and the queue still holds the item rather than
  // quietly dropping it (P2-U7 step 4).
  await expect(page.getByTestId('approvals-queue').getByTestId('approval-row')).not.toHaveCount(0)

  const check = await adminContext(baseURL)
  expect(await getValue(check, 'Timesheet', { name }, 'workflow_state')).toBe('Pending Approval')
  // Put the site back: a week left pending here is a week every other spec's
  // manager has to look past.
  await check.post('/api/method/frappe.client.delete', {
    form: { doctype: 'Timesheet', name },
  })
  await check.dispose()
})

test('an employee with nobody reporting to them has no approvals at all', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'employee', 'this is the non-manager capability shape')

  // The other half of P2-U7 scenario 6: capability is a server answer, so a
  // user with no decisions gets no nav entry and an empty queue -- not a
  // page that lists somebody else's pending work, which is what the
  // unfiltered `frappe.client.get_list` on this page used to do.
  await page.goto('/helixhr/approvals')
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()
  await expect(page.getByTestId('approvals-queue').getByText('Nothing waiting on you')).toBeVisible({
    timeout: 10000,
  })
  await expect(page.getByRole('link', { name: 'Approvals' })).toHaveCount(0)
})
