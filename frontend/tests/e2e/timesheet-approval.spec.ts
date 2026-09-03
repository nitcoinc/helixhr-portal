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

  await api.dispose()
  return projectName
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

  await empPage.getByRole('combobox').nth(1).click()
  await empPage.getByRole('option', { name: 'Timesheet Approval Spec Project' }).click()
  await empPage.getByLabel('Hours').fill('4')
  await empPage.getByRole('button', { name: 'Save draft' }).click()
  await empPage.waitForTimeout(500)

  await empPage.getByRole('button', { name: 'Submit' }).click()
  await expect(empPage.getByText('Waiting for manager')).toBeVisible({ timeout: 10000 })

  const mgrCtx = await browser.newContext({ storageState: 'tests/.auth/manager.json' })
  const mgrPage = await mgrCtx.newPage()
  await mgrPage.goto('/helixhr/approvals')
  await expect(mgrPage.getByRole('heading', { name: 'Approvals' })).toBeVisible()

  const tsSection = mgrPage.getByTestId('approvals-timesheet-section')
  await expect(tsSection.getByText('Employee')).toBeVisible({ timeout: 10000 })

  await tsSection.getByRole('button', { name: 'Reject' }).click()
  const rejectDialog = mgrPage.getByRole('dialog')
  await expect(rejectDialog).toBeVisible()
  await rejectDialog.getByLabel('Comment').fill('Please double check your hours')
  await rejectDialog.getByRole('button', { name: 'Reject' }).click()
  await expect(tsSection.getByText('Nothing waiting on you.')).toBeVisible({ timeout: 10000 })

  await empPage.reload()
  await expect(empPage.getByText('Sent back').first()).toBeVisible({ timeout: 10000 })

  // The reason, not just the label. Asserting only "Sent back" is what let a
  // 403 on the comment lookup live in this flow undetected: the employee saw
  // that their week came back and never saw why.
  await expect(empPage.getByText(/Please double check your hours/)).toBeVisible({
    timeout: 10000,
  })

  await empPage.getByRole('button', { name: 'Edit and resubmit' }).click()
  await expect(empPage.getByRole('button', { name: 'Submit' })).toBeVisible({ timeout: 10000 })
  await empPage.waitForTimeout(500)
  await empPage.getByRole('button', { name: 'Submit' }).click()
  await expect(empPage.getByText('Waiting for manager')).toBeVisible({ timeout: 10000 })

  await mgrPage.goto('/helixhr/approvals')
  await expect(tsSection.getByText('Employee')).toBeVisible({ timeout: 10000 })
  await tsSection.getByRole('button', { name: 'Approve' }).click()
  await expect(tsSection.getByText('Nothing waiting on you.')).toBeVisible({ timeout: 10000 })

  await empCtx.close()
  await mgrCtx.close()
})
