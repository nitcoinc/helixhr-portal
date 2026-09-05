import { test, expect, request, Browser } from '@playwright/test'

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

// A real Project the employee may book time on, created once per run
// via the same admin API path setup_playwright_fixtures itself uses --
// this spec needs one that neither auth.setup.ts nor the other specs
// already guarantee.
async function ensureProject(baseURL) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })

  const existing = await api.get(
    '/api/method/frappe.client.get_value?doctype=Project&filters=' +
      encodeURIComponent(JSON.stringify({ project_name: 'Timesheet Approval Spec Project' })) +
      '&fieldname=name',
  )
  const existingBody = await existing.json()
  let projectName = existingBody?.message?.name

  if (!projectName) {
    const companyResp = await api.get(
      '/api/method/frappe.client.get_value?doctype=Employee&filters=' +
        encodeURIComponent(JSON.stringify({ user_id: 'employee@helixhr.test' })) +
        '&fieldname=company',
    )
    const company = (await companyResp.json()).message.company

    const created = await api.post('/api/method/frappe.client.insert', {
      data: {
        doc: JSON.stringify({
          doctype: 'Project',
          project_name: 'Timesheet Approval Spec Project',
          status: 'Open',
          company,
        }),
      },
    })
    projectName = (await created.json()).message.name
  }

  const permExists = await api.get(
    '/api/method/frappe.client.get_value?doctype=User Permission&filters=' +
      encodeURIComponent(
        JSON.stringify({ user: 'employee@helixhr.test', allow: 'Project', for_value: projectName }),
      ) +
      '&fieldname=name',
  )
  if (!(await permExists.json())?.message?.name) {
    await api.post('/api/method/frappe.client.insert', {
      data: {
        doc: JSON.stringify({
          doctype: 'User Permission',
          user: 'employee@helixhr.test',
          allow: 'Project',
          for_value: projectName,
        }),
      },
    })
  }

  await clearCurrentWeek(api)

  await api.dispose()
  return projectName
}

/**
 * This test files, sends back, refixes and approves *this* week, and one
 * employee has one Timesheet per week (KTD10). So a second run starts on the
 * week the first run left Approved -- read-only, with nothing to enter --
 * which is the "depends on state" flakiness recorded in the runbook. The
 * week is cleared as Administrator first, along with any residue from
 * earlier runs, so the spec starts from the same place every time.
 *
 * A generous window (the fortnight around the host's own Monday) rather than
 * an exact Monday: the site's timezone decides which week the *server* calls
 * current, and the two can disagree by a day at the boundary.
 */
async function clearCurrentWeek(api) {
  const employeeResp = await api.get(
    '/api/method/frappe.client.get_value?doctype=Employee&filters=' +
      encodeURIComponent(JSON.stringify({ user_id: 'employee@helixhr.test' })) +
      '&fieldname=name',
  )
  const employee = (await employeeResp.json())?.message?.name
  if (!employee) return

  const now = new Date()
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7))
  const from = new Date(monday)
  from.setUTCDate(from.getUTCDate() - 7)
  const to = new Date(monday)
  to.setUTCDate(to.getUTCDate() + 7)

  const sheets = await api.get(
    '/api/method/frappe.client.get_list?doctype=Timesheet&filters=' +
      encodeURIComponent(
        JSON.stringify({
          employee,
          start_date: ['between', [from.toISOString().slice(0, 10), to.toISOString().slice(0, 10)]],
        }),
      ) +
      '&fields=' +
      encodeURIComponent(JSON.stringify(['name', 'docstatus'])) +
      '&limit_page_length=0',
  )
  for (const row of (await sheets.json())?.message || []) {
    if (row.docstatus === 1) {
      await api.post('/api/method/frappe.client.cancel', {
        form: { doctype: 'Timesheet', name: row.name },
      })
    }
    await api.post('/api/method/frappe.client.delete', {
      form: { doctype: 'Timesheet', name: row.name },
    })
  }
}

/**
 * The Timesheet the employee has just sent, by name. The approvals queue is
 * one mixed list ordered oldest-first (P2-U7), and other specs leave their
 * own pending weeks on this site -- so "the first timesheet row" is not
 * necessarily this week, and acting on the wrong one silently passes the
 * next few assertions for the wrong reason.
 */
async function pendingWeekName(baseURL) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  const employeeResp = await api.get(
    '/api/method/frappe.client.get_value?doctype=Employee&filters=' +
      encodeURIComponent(JSON.stringify({ user_id: 'employee@helixhr.test' })) +
      '&fieldname=name',
  )
  const employee = (await employeeResp.json())?.message?.name
  // Bounded to the fortnight around this week, because another spec keeps a
  // pending week of its own for this same employee, five weeks back.
  const now = new Date()
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7))
  const from = new Date(monday)
  from.setUTCDate(from.getUTCDate() - 7)
  const to = new Date(monday)
  to.setUTCDate(to.getUTCDate() + 7)

  const response = await api.get(
    '/api/method/frappe.client.get_value?doctype=Timesheet&filters=' +
      encodeURIComponent(
        JSON.stringify({
          employee,
          workflow_state: 'Pending Approval',
          start_date: ['between', [from.toISOString().slice(0, 10), to.toISOString().slice(0, 10)]],
        }),
      ) +
      '&fieldname=name',
  )
  const name = (await response.json())?.message?.name
  await api.dispose()
  return name
}

test('employee submits a week, manager rejects with a comment, employee edits and resubmits, manager approves', async ({
  browser,
}: {
  browser: Browser
}, testInfo) => {
  // This test drives both identities itself via explicit browser
  // contexts, so it only needs to run once -- not once per Playwright
  // project (which would resubmit against the same "this week" a second
  // time and hit the already-Approved state from the first run).
  test.skip(testInfo.project.name !== 'employee', 'runs once, not per project')
  test.setTimeout(60000)

  await ensureProject(process.env.BASE_URL || 'http://localhost:8080')

  const empCtx = await browser.newContext({ storageState: 'tests/.auth/employee.json' })
  const empPage = await empCtx.newPage()

  await empPage.goto('/helixhr/timesheet')
  await expect(empPage.getByRole('heading', { name: 'Timesheet' })).toBeVisible()

  // P2-U6 replaced the five-select row grid with the project x day grid at
  // `lg:` (this project is Desktop Chrome) and the day-first list below it.
  const grid = empPage.getByTestId('week-grid')
  await grid.getByRole('button', { name: 'Add a project row' }).click()
  await grid
    .getByLabel(/^Project for row/)
    .selectOption({ label: 'Timesheet Approval Spec Project' })
  await grid.locator('input[type="number"]').first().fill('4')
  await empPage.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(empPage.getByText(/^Saved/)).toBeVisible({ timeout: 10000 })

  await empPage.getByRole('button', { name: 'Submit week' }).click()
  await expect(empPage.locator('[data-status="Pending Approval"]:visible')).toBeVisible({ timeout: 10000 })

  const mgrCtx = await browser.newContext({ storageState: 'tests/.auth/manager.json' })
  const mgrPage = await mgrCtx.newPage()
  await mgrPage.goto('/helixhr/approvals')
  await expect(mgrPage.getByRole('heading', { name: 'Approvals' })).toBeVisible()

  // P2-U7 replaced the two sections (Leave, Timesheets) with one mixed
  // queue, and the reject dialog with the reason field on the same surface
  // as the evidence: the manager reads the week, then decides it.
  const queue = mgrPage.getByTestId('approvals-queue')
  // The queue mixes leave and timesheets from everybody who reports to this
  // manager (P2-U7), so this addresses the record rather than a position,
  // and "it worked" is *this* record leaving -- not the list emptying.
  const week = await pendingWeekName(process.env.BASE_URL || 'http://localhost:8080')
  const weekRow = queue.locator(`[data-approval-name="${week}"]`)
  await expect(weekRow).toBeVisible({ timeout: 10000 })

  await weekRow.click()
  const panel = mgrPage.getByTestId('approval-detail')
  await expect(panel.getByText('Day total')).toBeVisible({ timeout: 10000 })

  // P2-U3 renamed the manager's action to the word the employee already
  // sees on the row ("Sent back"); "Reject" was the Frappe verb.
  await panel
    .getByLabel('Send back with a reason (required to send back)')
    .fill('Please double check your hours')
  await panel.getByRole('button', { name: 'Send back' }).click()
  await expect(weekRow).toHaveCount(0, { timeout: 10000 })

  await empPage.reload()
  await expect(empPage.locator('[data-status="Rejected"]:visible')).toBeVisible({ timeout: 10000 })

  // The reason, not just the label. Asserting only "Sent back" is what let a
  // 403 on the comment lookup live in this flow undetected: the employee saw
  // that their week came back and never saw why.
  await expect(empPage.getByText(/Please double check your hours/)).toBeVisible({
    timeout: 10000,
  })

  // One button, one verb, one outcome (P2-U6 step 7). "Edit and resubmit"
  // used to perform only the workflow reopen, leaving the employee on a
  // Draft with their fix neither saved nor sent; `submit_my_week` reopens,
  // saves and sends in one transaction.
  await empPage.getByRole('button', { name: 'Send again' }).click()
  await expect(empPage.locator('[data-status="Pending Approval"]:visible')).toBeVisible({ timeout: 10000 })

  await mgrPage.goto('/helixhr/approvals')
  await expect(weekRow).toBeVisible({ timeout: 10000 })
  await weekRow.click()
  await expect(panel.getByText('Day total')).toBeVisible({ timeout: 10000 })
  await panel.getByRole('button', { name: /^Approve/ }).click()
  await expect(weekRow).toHaveCount(0, { timeout: 10000 })

  await empCtx.close()
  await mgrCtx.close()
})
