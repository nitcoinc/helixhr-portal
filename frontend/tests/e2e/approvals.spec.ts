import { test, expect, request, APIRequestContext, Locator } from '@playwright/test'

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
function seedMonday(weeksBack = 5): string {
  const now = new Date()
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7) - weeksBack * 7)
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
async function seedPendingWeek(baseURL: string, weeksBack = 5) {
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

  const monday = seedMonday(weeksBack)
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

  await page.goto('/helixhr/approvals?view=timesheets')
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()

  const queue = page.getByTestId('approvals-queue')
  // By record, not by position: the queue is oldest-first across both kinds,
  // so whatever else this site is holding, this is the week under test.
  const row = queue.locator(`[data-approval-name="${name}"]`)
  await expect(row).toBeVisible({ timeout: 10000 })

  // P2-AE6: nothing to decide with until the evidence is on screen.
  await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(0)

  await row.click()
  await expect(page).toHaveURL(new RegExp(`/helixhr/approvals/timesheet/${name}\\?view=timesheets$`))

  const panel = page.getByTestId('approval-detail')
  await expect(panel.getByText('Day total')).toBeVisible({ timeout: 10000 })
  await expect(panel.getByText(PROJECT_NAME)).toBeVisible()
  await expect(panel.getByText(/Long Monday, doctor on Friday/)).toBeVisible()
  await expect(panel.getByRole('button', { name: /^Approve/ })).toBeVisible()

  // P4-R1 / P4-KTD2. Three outcomes on a week, and Reject is not one of them:
  // a terminal state would lock a week whose hours still have to be recorded.
  // The button is absent rather than disabled, so nobody learns that a week
  // can be rejected (P4-U4).
  await expect(panel.getByTestId('send-back')).toBeVisible()
  await expect(panel.getByTestId('send-to-hr')).toBeVisible()
  await expect(panel.getByTestId('reject')).toHaveCount(0)

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

/**
 * P4-U4 / P4-R11, P4-AE2. The HR half of the queue, under the third identity
 * (`make_test_hr_manager_employee` -- an HR Manager who *has* an Employee
 * record, so `portal_home_page` lets them into the portal at all).
 *
 * Its own week, five weeks further back than the manager test's: both run in
 * this one file and one Timesheet per Monday-Sunday week (KTD10) means a
 * shared week would be two tests fighting over one record even at
 * `--workers=1`.
 */
test('HR works what a manager handed over, tagged and with the note, and has no Send to HR', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'this is the HR capability shape')
  test.setTimeout(90000)

  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const { name } = await seedPendingWeek(baseURL, 10)

  // The manager hands it over, with a note, through the same method the
  // screen calls -- so the stage change, the DocShare removal and the
  // HR-queue Notification are the real ones.
  const managerApi = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await managerApi.post('/api/method/login', {
    form: { usr: 'manager@helixhr.test', pwd: PASSWORD },
  })
  const modified = await getValue(managerApi, 'Timesheet', { name }, 'modified')
  const escalated = await managerApi.post('/api/method/helixhr.api.act_on_approval', {
    form: {
      doctype: 'Timesheet',
      name,
      action: 'Send to HR',
      comment: 'hours on Sunday',
      expected_modified: modified,
      expected_state: 'Pending Approval',
    },
  })
  expect(escalated.ok(), await escalated.text()).toBeTruthy()
  await managerApi.dispose()

  await page.goto('/helixhr/approvals?view=timesheets')
  const row = page.getByTestId('approvals-queue').locator(`[data-approval-name="${name}"]`)
  await expect(row).toBeVisible({ timeout: 15000 })
  // The chip is a word, not a tint: it is the only thing that says whether a
  // row is HR's work or this person's own as a line manager (P4-R11).
  await expect(row.getByTestId('hr-chip')).toBeVisible()
  await expect(row).toContainText('hours on Sunday')

  await row.click()
  const panel = page.getByTestId('approval-detail')
  await expect(panel.getByTestId('hr-chip')).toBeVisible({ timeout: 10000 })
  await expect(panel).toContainText('hours on Sunday')

  // R5: only a manager hands a request over. There is nowhere left to send
  // this, so HR never sees the button.
  await expect(panel.getByTestId('send-to-hr')).toHaveCount(0)
  await expect(panel.getByTestId('send-back')).toBeVisible()

  await panel.getByRole('button', { name: /^Approve/ }).click()

  const admin = await adminContext(baseURL)
  await expect
    .poll(async () => getValue(admin, 'Timesheet', { name }, 'workflow_state'), { timeout: 15000 })
    .toBe('Approved')
  await admin.post('/api/method/frappe.client.cancel', {
    form: { doctype: 'Timesheet', name },
  })
  await admin.post('/api/method/frappe.client.delete', {
    form: { doctype: 'Timesheet', name },
  })
  await admin.dispose()
})

/** A weekday far enough ahead that no other spec books it. */
function futureWeekday(): string {
  const now = new Date()
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 200 + (now.getUTCDate() % 30)))
  while (day.getUTCDay() === 0 || day.getUTCDay() === 6) day.setUTCDate(day.getUTCDate() + 1)
  return day.toISOString().slice(0, 10)
}

test('HR decides a manager-stage leave the approver has sat on, tagged with why', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'this is the HR capability shape')
  test.setTimeout(90000)

  // Plan 2026-10-02-001 U4. Seeded overdue rather than approver-away: an
  // away approver needs a submitted leave covering today, and that submit
  // fails whenever today is a holiday. The away branch is covered in
  // test_api_approvals.TestHrSeesStalledManagerLeave.
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const admin = await adminContext(baseURL)
  const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
  const date = futureWeekday()
  const inserted = await admin.post('/api/method/frappe.client.insert', {
    data: {
      doc: JSON.stringify({
        doctype: 'Leave Application',
        employee,
        leave_type: 'Casual Leave',
        from_date: date,
        to_date: date,
        description: 'u4 overdue',
        leave_approver: 'manager@helixhr.test',
      }),
    },
  })
  expect(inserted.ok(), await inserted.text()).toBeTruthy()
  const name = (await inserted.json()).message.name
  await admin.post('/api/method/frappe.client.set_value', {
    form: {
      doctype: 'Leave Application',
      name,
      fieldname: 'helixhr_pending_since',
      value: `${addDays(new Date().toISOString().slice(0, 10), -5)} 09:00:00`,
    },
  })

  try {
    await page.goto('/helixhr/approvals')
    const row = page.getByTestId('approvals-queue').locator(`[data-approval-name="${name}"]`)
    await expect(row).toBeVisible({ timeout: 15000 })
    await expect(row.getByTestId('hr-chip')).toBeVisible()
    await expect(row).toContainText('Overdue with approver')

    await row.click()
    const panel = page.getByTestId('approval-detail')
    await panel.getByRole('button', { name: /^Approve/ }).click()
    await expect
      .poll(async () => getValue(admin, 'Leave Application', { name }, 'docstatus'), { timeout: 15000 })
      .toBe(1)
  } finally {
    if ((await getValue(admin, 'Leave Application', { name }, 'docstatus')) === 1) {
      await admin.post('/api/method/frappe.client.cancel', { form: { doctype: 'Leave Application', name } })
    }
    await admin.post('/api/method/frappe.client.delete', { form: { doctype: 'Leave Application', name } })
    await admin.dispose()
  }
})

/**
 * Plan 2026-10-02-001 U12 / R26, moved by plan 2026-10-15-002 U5 (R8): the
 * Overdue tab became All pending with an Overdue-only toggle, and the old
 * `?view=overdue` link lands there with the toggle on.
 */
test('HR sees an overdue item under All pending, grouped under its approver', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'this is the HR capability shape')
  test.setTimeout(60000)

  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const admin = await adminContext(baseURL)
  const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
  const date = futureWeekday()
  const inserted = await admin.post('/api/method/frappe.client.insert', {
    data: {
      doc: JSON.stringify({
        doctype: 'Leave Application',
        employee,
        leave_type: 'Casual Leave',
        from_date: date,
        to_date: date,
        description: 'u12 overdue',
        leave_approver: 'manager@helixhr.test',
      }),
    },
  })
  expect(inserted.ok(), await inserted.text()).toBeTruthy()
  const name = (await inserted.json()).message.name
  await admin.post('/api/method/frappe.client.set_value', {
    form: {
      doctype: 'Leave Application',
      name,
      fieldname: 'helixhr_pending_since',
      value: `${addDays(new Date().toISOString().slice(0, 10), -5)} 09:00:00`,
    },
  })

  try {
    await page.goto('/helixhr/approvals')
    await page.getByRole('tab', { name: 'All pending', exact: true }).click()
    await expect(page).toHaveURL(/view=all-pending/)
    await expect(page.getByTestId('overdue-tab').locator(`[data-overdue-name="${name}"]`)).toBeVisible({
      timeout: 15000,
    })

    await page.goto('/helixhr/approvals?view=overdue')
    await expect(page.getByRole('tab', { name: 'All pending', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('overdue-toggle')).toHaveAttribute('aria-pressed', 'true')
    const row = page.getByTestId('overdue-tab').locator(`[data-overdue-name="${name}"]`)
    await expect(row).toBeVisible({ timeout: 15000 })
    // Seeded five UTC days back; the site counts in its own time zone, which
    // may already be a day ahead.
    await expect(row).toContainText(/[56] days/)
    await expect(row).toContainText('limit 2')
    await expect(row.locator('xpath=ancestor::section[@data-testid="overdue-group"]/h2')).toContainText(
      'Manager',
    )
  } finally {
    await admin.post('/api/method/frappe.client.delete', { form: { doctype: 'Leave Application', name } })
    await admin.dispose()
  }
})

test('an employee has no All pending tab, even by URL', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'employee', 'this is the non-HR capability shape')

  await page.goto('/helixhr/approvals?view=overdue')
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'All pending', exact: true })).toHaveCount(0)
  await expect(page.getByTestId('overdue-tab')).toHaveCount(0)
})

/** Plan 2026-10-15-002 U5 (R1, R3): a manager's Approvals tab is My team with
 * no HR lane, and timesheets live on their own tab. */
test('a manager sees My team and no HR lane; timesheets have their own tab', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the line-manager capability shape')

  // Start on Leave & attendance by URL: which tab is the default depends on
  // what happens to be waiting (Approvals.vue `firstWaitingTab`), and the
  // default tab's own URL carries no `?view=`.
  await page.goto('/helixhr/approvals?view=queue')
  await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible()
  await expect(page.getByRole('tab', { name: /^Leave & attendance/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('lane-hr')).toHaveCount(0)
  await expect(page.getByTestId('approvals-queue').locator('[data-approval-kind="timesheet"]')).toHaveCount(0)
  await page.getByRole('tab', { name: /^Timesheets/ }).click()
  await expect(page).not.toHaveURL(/view=queue/)
  await expect(page.getByRole('tab', { name: /^Timesheets/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('timesheet-oversight')).toHaveCount(0)

  // MH-01: the active tab carries a real 2px heading-ink bar. `border-ink-*`
  // generated no CSS, so the bar was missing; the inactive tab stays clear.
  const bar = (name: RegExp) =>
    page.getByRole('tab', { name }).evaluate((el) => {
      const style = getComputedStyle(el)
      return `${style.borderBottomWidth} ${style.borderBottomColor}`
    })
  await expect.poll(() => bar(/^Timesheets/)).toBe('2px rgb(26, 23, 20)')
  expect(await bar(/^Leave/)).toBe('2px rgba(0, 0, 0, 0)')
})

/** Plan 2026-10-15-002 R6 (AE2): HR Manager's Timesheets tab lists every
 * pending week in the company by approver -- here the oversight fixture's
 * group from `setup_playwright_fixtures`. */
test('HR sees All pending timesheets grouped by approver on the Timesheets tab', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'this is the HR capability shape')

  // From All pending by URL, for the same reason as the manager test above:
  // the default tab depends on what is waiting and carries no `?view=`.
  await page.goto('/helixhr/approvals?view=all-pending')
  await expect(page.getByRole('tab', { name: 'All pending', exact: true })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: /^Timesheets/ }).click()
  await expect(page).not.toHaveURL(/view=all-pending/)
  await expect(page.getByRole('tab', { name: /^Timesheets/ })).toHaveAttribute('aria-selected', 'true')
  const oversight = page.getByTestId('timesheet-oversight')
  await expect(oversight.getByRole('heading', { name: /All pending timesheets/ })).toBeVisible()
  const group = oversight.locator('[data-testid="oversight-group"][data-approver="oversight-manager@helixhr.test"]')
  await expect(group).toBeVisible({ timeout: 15000 })
  await expect(group.getByTestId('oversight-row')).not.toHaveCount(0)
})

/**
 * P5-U11. The routed-worker capability shape, under the fourth identity
 * (`it-team@helixhr.test`, seeded by `make_test_it_user` -- P5-U2). Runs
 * against the single `IT / Asset` request `ensure_test_it_request` seeds:
 * one request, picked up, questioned and finished, so this is single-run-
 * per-site the same way the manager and HR tests above already are --
 * re-running against a site this has already consumed needs a fresh site
 * (see docs/runbook.md's Verify section).
 */
test('an IT identity has no Approvals nav item and works requests from Team inbox', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the routed-worker capability shape')
  test.setTimeout(60000)

  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const admin = await adminContext(baseURL)
  const name = await getValue(
    admin,
    'HR Request',
    { category: 'IT / Asset', routed_to_role: 'IT Team', status: 'Open' },
    'name',
  )
  await admin.dispose()
  expect(name, 'ensure_test_it_request should have seeded one IT / Asset request').toBeTruthy()

  // R3: IT user with no reports has no Approvals nav item
  await page.goto('/helixhr')
  await expect(page.getByRole('link', { name: 'Approvals' })).toHaveCount(0)

  // R2: Works requests from Team inbox
  await page.goto('/helixhr/requests?tab=work')
  // The row shows the subject, not the ID; find it by its link.
  const row = page.getByTestId('work-row').filter({ has: page.locator(`a[href*="/requests/${name}"]`) })
  await expect(row).toBeVisible({ timeout: 10000 })
  await row.click()
  await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${name}`))

  const panel = page.getByTestId('work-detail')
  await expect(panel.getByTestId('pick-up')).toBeVisible({ timeout: 10000 })
  await panel.getByTestId('pick-up').click()
  await expect(panel.getByTestId('done')).toBeVisible({ timeout: 10000 })
})

// ── Plan 2026-10-04-002 U4 ────────────────────────────────────────────────
// The Requests page's second tab, under the routed-worker identity. Own
// request, filed by the employee fixture through the portal's own method, so
// it never touches the single-run request the IT lifecycle test above
// consumes.
test('the Requests page gains a To work on tab for a routed worker', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the routed-worker capability shape')
  test.setTimeout(60000)

  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const admin = await adminContext(baseURL)
  const employeeApi = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await employeeApi.post('/api/method/login', { form: { usr: EMPLOYEE, pwd: PASSWORD } })
  const subject = `U4 work tab ${Date.now()}`

  const seeded = await employeeApi.post('/api/method/helixhr.api.create_my_request', {
    data: {
      category: 'IT / Asset',
      subject,
      details: 'Seeded for the work tab',
      operation_key: crypto.randomUUID(),
    },
  })
  expect(seeded.ok(), await seeded.text()).toBeTruthy()
  const name = (await seeded.json())?.message?.name as string

  try {
    // R6: a routed worker gets the two-item tab bar; their own list is the
    // default tab, and the work list starts empty of their own requests.
    await page.goto('/helixhr/requests')
    const tabs = page.getByTestId('requests-tabs')
    await expect(tabs.getByRole('tab', { name: 'My requests' })).toBeVisible()
    await expect(tabs.getByRole('tab', { name: /^Team inbox/ })).toBeVisible()
    await expect(tabs.getByRole('tab', { name: 'My requests' })).toHaveAttribute('aria-selected', 'true')

    // R7/R9: the work tab lives in the URL and lists what is routed here.
    await tabs.getByRole('tab', { name: /^Team inbox/ }).click()
    await expect(page).toHaveURL(/tab=work/)
    const row = page.locator('[data-testid="work-row"]', { hasText: subject })
    await expect(row).toBeVisible({ timeout: 10000 })

    // R8: the detail is the same decision surface the Approvals queue opens,
    // and picking up moves the row under Mine.
    await row.click()
    await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${name}`))
    const panel = page.getByTestId('work-detail')
    await expect(panel.getByTestId('pick-up')).toBeVisible({ timeout: 10000 })
    await panel.getByTestId('pick-up').click()
    await expect(panel.getByTestId('done')).toBeVisible({ timeout: 10000 })

    const chips = page.getByTestId('work-filter-chips')
    await chips.getByRole('button', { name: 'Mine' }).click()
    await expect(page).toHaveURL(/tab=work&state=mine/)
    await expect(page.locator('[data-testid="work-row"]', { hasText: subject })).toBeVisible()

    // R9: a notification-shaped link reproduces the tab and the filter.
    await page.goto('/helixhr/requests?tab=work&state=mine')
    await expect(
      page.getByTestId('work-filter-chips').getByRole('button', { name: 'Mine' }),
    ).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('[data-testid="work-row"]', { hasText: subject })).toBeVisible()

    // The other chips restore the same way; ?state=waiting selects Open and
    // scrolls to the waiting group.
    await page.goto('/helixhr/requests?tab=work&state=waiting')
    await expect(
      page.getByTestId('work-filter-chips').getByRole('button', { name: 'Open' }),
    ).toHaveAttribute('aria-pressed', 'true')

    // Keyboard: the tablist switches with the arrow keys.
    await page.goto('/helixhr/requests')
    await page.getByRole('tab', { name: 'My requests' }).focus()
    await page.keyboard.press('ArrowRight')
    await expect(page).toHaveURL(/tab=work/)
    await page.keyboard.press('ArrowLeft')
    await expect(page).not.toHaveURL(/tab=work/)
  } finally {
    await admin.post('/api/method/frappe.client.delete', {
      data: { doctype: 'HR Request', name },
    })
    await employeeApi.dispose()
    await admin.dispose()
  }
})

// Plan 2026-10-05-001 U3. A change request's Accept cancels an approved week
// and reopens it, so it never fires on one click, and it is out of reach
// while the manager is writing a Decline.
async function loginAs(baseURL: string, usr: string) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr, pwd: PASSWORD } })
  return api
}

async function seedChangeRequest(baseURL: string, weeksBack: number) {
  const { name, monday } = await seedPendingWeek(baseURL, weeksBack)
  const manager = await loginAs(baseURL, 'manager@helixhr.test')
  const approved = await manager.post('/api/method/frappe.model.workflow.apply_workflow', {
    form: { doc: JSON.stringify({ doctype: 'Timesheet', name }), action: 'Approve' },
  })
  expect(approved.ok(), await approved.text()).toBeTruthy()
  await manager.dispose()

  const employee = await loginAs(baseURL, EMPLOYEE)
  const raised = await employee.post('/api/method/helixhr.api.raise_timesheet_change', {
    form: { week_start: monday, comment: 'Tuesday should be six hours, not two' },
  })
  expect(raised.ok(), await raised.text()).toBeTruthy()
  const change = (await raised.json()).message.name
  await employee.dispose()
  return { timesheet: name, monday, change }
}

async function changeStatus(baseURL: string, change: string) {
  const admin = await adminContext(baseURL)
  const status = await getValue(admin, 'HelixHR Timesheet Change', { name: change }, 'status')
  await admin.dispose()
  return status
}

test('Decline hides Accept, and cancelling the reason brings it back', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(60000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const { timesheet, change } = await seedChangeRequest(baseURL, 9)

  await page.goto(`/helixhr/approvals/change/${change}`)
  const panel = page.getByTestId('approval-detail')
  await expect(panel.getByTestId('accept')).toBeVisible({ timeout: 10000 })

  await panel.getByTestId('decline').click()
  await expect(panel.getByTestId('accept')).toHaveCount(0)
  await panel.getByTestId('reason-cancel').click()
  await expect(panel.getByTestId('accept')).toBeVisible()

  await panel.getByTestId('decline').click()
  await panel.getByTestId('decision-reason').getByRole('textbox').fill('The hours match the project record.')
  await panel.getByTestId('decline').click()
  await expect.poll(() => changeStatus(baseURL, change), { timeout: 10000 }).toBe('Declined')

  const admin = await adminContext(baseURL)
  expect(await getValue(admin, 'Timesheet', { name: timesheet }, 'workflow_state')).toBe('Approved')
  await admin.dispose()
})

test('Accept needs a confirm: cancel leaves it open, confirm reopens the week', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(60000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const { timesheet, monday, change } = await seedChangeRequest(baseURL, 11)

  await page.goto(`/helixhr/approvals/change/${change}`)
  const panel = page.getByTestId('approval-detail')
  await panel.getByTestId('accept').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText(/cancels the approved week and reopens it/)).toBeVisible()
  await expect(dialog.getByTestId('accept-cancel')).toBeFocused()

  await dialog.getByTestId('accept-cancel').click()
  await expect(dialog).toHaveCount(0)
  expect(await changeStatus(baseURL, change)).toBe('Open')

  await panel.getByTestId('accept').click()
  await page.getByRole('dialog').getByTestId('accept-confirm').click()
  await expect.poll(() => changeStatus(baseURL, change), { timeout: 10000 }).toBe('Accepted')

  const admin = await adminContext(baseURL)
  expect(await getValue(admin, 'Timesheet', { name: timesheet }, 'docstatus')).toBe(2)
  await admin.dispose()
  const employee = await loginAs(baseURL, EMPLOYEE)
  const week = await employee.get(`/api/method/helixhr.api.get_my_week?week_start=${monday}`)
  expect((await week.json()).message.timesheet.workflow_state).toBe('Draft')
  await employee.dispose()
})

// ── Plan 2026-10-17-001 U7 ────────────────────────────────────────────────
// Approvals: one evidence surface for phone and desktop, then Approve all,
// select-all, keyboard triage and focus after a row leaves. Every row these
// tests assert on is seeded here and removed in `finally`; counts that the
// site's leftovers could move are read from the server first rather than
// assumed.

const MANAGER = 'manager@helixhr.test'
const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1440, height: 900 }

/** A clean one-day leave for the employee, with the manager as approver:
 * far enough ahead for no short-notice flag, one day of a five-day balance. */
async function seedLeave(baseURL: string, date: string, description: string) {
  const admin = await adminContext(baseURL)
  const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
  const inserted = await admin.post('/api/method/frappe.client.insert', {
    data: {
      doc: JSON.stringify({
        doctype: 'Leave Application',
        employee,
        leave_type: 'Casual Leave',
        from_date: date,
        to_date: date,
        description,
        leave_approver: MANAGER,
      }),
    },
  })
  expect(inserted.ok(), await inserted.text()).toBeTruthy()
  const name = (await inserted.json()).message.name as string
  await admin.dispose()
  return name
}

async function removeRecord(baseURL: string, doctype: string, name: string) {
  const admin = await adminContext(baseURL)
  if ((await getValue(admin, doctype, { name }, 'docstatus')) === 1) {
    await admin.post('/api/method/frappe.client.cancel', { form: { doctype, name } })
  }
  await admin.post('/api/method/frappe.client.delete', { form: { doctype, name } })
  await admin.dispose()
}

/** A weekday about 40 weeks back: nothing else on this site books it. */
function pastWeekday(): string {
  const day = new Date()
  day.setUTCDate(day.getUTCDate() - 284)
  while (day.getUTCDay() === 0 || day.getUTCDay() === 6) day.setUTCDate(day.getUTCDate() - 1)
  return day.toISOString().slice(0, 10)
}

/** One attendance request already with the manager, through the portal's
 * own two methods. Never batchable: `approve_clean_items` refuses it. */
async function seedAttendanceRequest(baseURL: string) {
  const day = pastWeekday()
  const admin = await adminContext(baseURL)
  const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
  const listed = await admin.get(
    '/api/method/frappe.client.get_list?doctype=Attendance%20Request&filters=' +
      encodeURIComponent(JSON.stringify({ employee, from_date: ['<=', day], to_date: ['>=', day] })) +
      '&fields=' +
      encodeURIComponent(JSON.stringify(['name'])) +
      '&limit_page_length=0',
  )
  // Read before dispose: a disposed context's responses are gone with it.
  const rows: { name: string }[] = (await listed.json())?.message || []
  await admin.dispose()
  for (const row of rows) await removeRecord(baseURL, 'Attendance Request', row.name)

  const api = await loginAs(baseURL, EMPLOYEE)
  const created = await api.post('/api/method/helixhr.api.create_my_attendance_request', {
    form: { from_date: day, to_date: day, reason: 'Work From Home', explanation: 'u7 approve all' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const draft = (await created.json()).message
  const sent = await api.post('/api/method/helixhr.api.send_my_attendance_request', {
    form: { name: draft.name, expected_modified: draft.modified },
  })
  expect(sent.ok(), await sent.text()).toBeTruthy()
  await api.dispose()
  return draft.name as string
}

/** The queue row fields these tests read (`get_my_approvals().pending`). */
type QueueRow = { name: string; kind: string; lane?: string; needs_look?: boolean }

/** The manager's queue as the server answers it right now. */
async function managerQueue(baseURL: string) {
  const api = await loginAs(baseURL, MANAGER)
  const response = await api.get('/api/method/helixhr.api.get_my_approvals')
  const message = (await response.json()).message
  await api.dispose()
  return message as { total: number; total_is_capped: boolean; pending: QueueRow[] }
}

/** What Approve all would take from the manager's My team lane on the
 * Leave & attendance tab (lib/approvalLanes.js `isBatchable`). */
function eligibleLeave(pending: QueueRow[]) {
  return pending.filter(
    (row) => (row.lane || 'team') === 'team' && row.kind === 'leave' && !row.needs_look,
  )
}

test('characterization: phone inline and desktop panel show the same evidence and decisions', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(120000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const { name: week } = await seedPendingWeek(baseURL, 13)
  const leave = await seedLeave(baseURL, addDays(futureWeekday(), 35), 'u7 characterization leave')

  /** The evidence both layouts must carry, and the decision ids. */
  async function expectEvidence(scope: Locator, texts: (string | RegExp)[], present: string[], absent: string[]) {
    for (const text of texts) await expect(scope).toContainText(text, { timeout: 10000 })
    for (const id of present) await expect(scope.getByTestId(id)).toBeVisible()
    for (const id of absent) await expect(scope.getByTestId(id)).toHaveCount(0)
  }

  try {
    const weekTexts = [PROJECT_NAME, '14.5', 'Long Monday, doctor on Friday']
    const weekIds = ['approve', 'send-back', 'send-to-hr']

    await page.setViewportSize(PHONE)
    await page.goto('/helixhr/approvals?view=timesheets')
    const weekRow = page.getByTestId('approvals-queue').locator(`[data-approval-name="${week}"]`)
    await expect(weekRow).toBeVisible({ timeout: 15000 })
    await weekRow.locator('button[aria-expanded]').click()
    await expect(page).toHaveURL(new RegExp(`/approvals/timesheet/${week}`))
    // The phone opens the record under its own row; the aside is desktop's.
    await expectEvidence(weekRow, weekTexts, weekIds, ['reject'])
    await expect(page.getByTestId('approval-detail')).toHaveCount(0)

    await page.setViewportSize(DESKTOP)
    const panel = page.getByTestId('approval-detail')
    await expectEvidence(panel, weekTexts, weekIds, ['reject'])
    await expect(weekRow.getByTestId('decision-actions')).toHaveCount(0)

    const leaveTexts = ['Casual Leave', 'Days', 'u7 characterization leave']
    const leaveIds = ['approve', 'reject']

    await page.setViewportSize(PHONE)
    await page.goto('/helixhr/approvals?view=queue')
    const leaveRow = page.getByTestId('approvals-queue').locator(`[data-approval-name="${leave}"]`)
    await expect(leaveRow).toBeVisible({ timeout: 15000 })
    await leaveRow.locator('button[aria-expanded]').click()
    await expectEvidence(leaveRow, leaveTexts, leaveIds, [])
    await expect(page.getByTestId('approval-detail')).toHaveCount(0)

    await page.setViewportSize(DESKTOP)
    await expectEvidence(page.getByTestId('approval-detail'), leaveTexts, leaveIds, [])
  } finally {
    await removeRecord(baseURL, 'Timesheet', week)
    await removeRecord(baseURL, 'Leave Application', leave)
  }
})

test('Approve all on a phone: two taps, a polite toast, the badge drops, attendance stays', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(120000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const start = futureWeekday()
  // Seeded inside `try`, so a seed that fails part-way still removes what it
  // made: a leftover leave holds a day of the five-day balance and an
  // overlap on its date for the rest of the run.
  const leaves: string[] = []
  let attendance = ''
  try {
    leaves.push(await seedLeave(baseURL, addDays(start, 21), 'u7 approve all one'))
    leaves.push(await seedLeave(baseURL, addDays(start, 28), 'u7 approve all two'))
    attendance = await seedAttendanceRequest(baseURL)

    // On a fresh site this is exactly the two leaves above; a long-lived
    // site's own clean leftovers join the batch, and the count follows them.
    const before = await managerQueue(baseURL)
    const eligible = eligibleLeave(before.pending)
    for (const name of leaves) {
      expect(eligible.map((row) => row.name), `${name} should look normal`).toContain(name)
    }
    const count = eligible.length
    expect(count).toBeLessThanOrEqual(60)

    await page.setViewportSize(PHONE)
    const loaded = page.waitForResponse((r) => r.url().includes('helixhr.api.get_my_approvals'))
    await page.goto('/helixhr/approvals?view=queue')
    const first = (await (await loaded).json()).message
    const queue = page.getByTestId('approvals-queue')
    await expect(queue.locator(`[data-approval-name="${attendance}"]`)).toBeVisible({ timeout: 15000 })

    // No checkboxes on a phone (R20), but the lane still has its batch.
    await expect(queue.getByTestId('bulk-check-leave')).toHaveCount(0)
    const approveAll = page.getByTestId('lane-team').getByTestId('approve-all')
    await expect(approveAll).toHaveText(`Approve all ${count} that look normal`)

    await approveAll.click()
    const dialog = page.getByRole('dialog')
    const label = dialog.getByTestId('bulk-confirm-label')
    await expect(label).toContainText(`Approve ${count} ${count === 1 ? 'item' : 'items'}`)
    const sentence = await label.textContent()

    // Rotating with the confirm open keeps the batch: it came from the
    // lane, not from checkboxes that 640px would clear.
    await page.setViewportSize({ width: 700, height: 390 })
    await expect(dialog.getByTestId('bulk-confirm-label')).toHaveText(sentence || '')
    await expect(dialog.getByTestId('bulk-confirm')).toHaveText(`Approve ${count}`)

    await dialog.getByTestId('bulk-confirm').click()
    await expect(page.locator('[data-testid="toast"][data-tone="success"]').filter({ hasText: `${count} approved` })).toBeVisible({
      timeout: 15000,
    })
    await expect(page.locator('[data-testid="toast"]').filter({ hasText: 'approved' })).toHaveCount(1)
    await expect(page.locator('[data-testid="toast"][data-tone="error"]')).toHaveCount(0)

    for (const name of leaves) await expect(queue.locator(`[data-approval-name="${name}"]`)).toHaveCount(0)
    await expect(queue.locator(`[data-approval-name="${attendance}"]`)).toBeVisible()
    await expect(page.getByTestId('lane-team').getByTestId('approve-all')).toHaveCount(0)
    // The two leaves are Decided by me now.
    await expect(page.getByTestId('decided-toggle')).toBeVisible()

    const expected = first.total - count
    const badge = page.getByTestId('tab-badge-approvals')
    if (expected > 0) await expect(badge).toHaveText(`${expected}${first.total_is_capped ? '+' : ''}`)
    else await expect(badge).toHaveCount(0)

    const admin = await adminContext(baseURL)
    for (const name of leaves) expect(await getValue(admin, 'Leave Application', { name }, 'status')).toBe('Approved')
    expect(await getValue(admin, 'Attendance Request', { name: attendance }, 'docstatus')).toBe(0)
    await admin.dispose()
  } finally {
    for (const name of leaves) await removeRecord(baseURL, 'Leave Application', name)
    if (attendance) await removeRecord(baseURL, 'Attendance Request', attendance)
  }
})

test('Approve all never counts a flagged week, and hides when nothing is eligible', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(90000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  // 14.5 hours against a full week: "Hours differ from expected".
  const { name } = await seedPendingWeek(baseURL, 15)

  try {
    const before = await managerQueue(baseURL)
    const mine = before.pending.find((row) => row.name === name)
    expect(mine?.needs_look, 'the seeded week should be flagged').toBeTruthy()
    const eligible = before.pending.filter(
      (row) => row.kind === 'timesheet' && row.lane !== 'hr' && !row.needs_look,
    ).length

    await page.setViewportSize(PHONE)
    await page.goto('/helixhr/approvals?view=timesheets')
    const lane = page.getByTestId('lane-yours')
    await expect(lane.getByTestId('queue-section-flagged').locator(`[data-approval-name="${name}"]`)).toBeVisible({
      timeout: 15000,
    })
    if (eligible) await expect(lane.getByTestId('approve-all')).toHaveText(`Approve all ${eligible} that look normal`)
    else await expect(lane.getByTestId('approve-all')).toHaveCount(0)
    // Change requests and flagged rows have no Approve all of their own.
    await expect(lane.getByTestId('queue-section-flagged').getByTestId('approve-all')).toHaveCount(0)
    await expect(lane.getByTestId('queue-section-change').getByTestId('approve-all')).toHaveCount(0)
  } finally {
    await removeRecord(baseURL, 'Timesheet', name)
  }
})

test('keyboard triage is off until turned on, then `a` opens the confirm and approves nothing', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(90000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const leave = await seedLeave(baseURL, addDays(futureWeekday(), 42), 'u7 keyboard')

  try {
    await page.setViewportSize(DESKTOP)
    await page.goto('/helixhr/approvals?view=queue')
    const toggle = page.locator(`[data-approval-name="${leave}"] [data-row-toggle]`)
    await expect(toggle).toBeVisible({ timeout: 15000 })

    // Off: no key acts.
    await toggle.focus()
    await page.keyboard.press('a')
    await page.keyboard.press('x')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByTestId('bulk-bar')).toHaveCount(0)

    await page.getByTestId('shortcuts-hint').click()
    await expect(page.locator('#approvals-shortcuts')).toContainText('Approve, after the confirm')
    await page.getByTestId('shortcuts-toggle').click()
    await expect(page.getByTestId('shortcuts-toggle')).toHaveAttribute('aria-checked', 'true')

    // On, but focus outside the list: a key is just a key.
    await page.getByRole('heading', { name: 'Approvals', level: 1 }).click()
    await page.keyboard.press('a')
    await expect(page.getByRole('dialog')).toHaveCount(0)

    // Inside the list: `a` opens the confirm, and nothing is approved by it.
    await toggle.focus()
    await page.keyboard.press('a')
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByTestId('bulk-confirm')).toHaveText('Approve 1')
    await dialog.getByRole('button', { name: 'Back' }).click()
    await expect(dialog).toHaveCount(0)
    const admin = await adminContext(baseURL)
    expect(await getValue(admin, 'Leave Application', { name: leave }, 'status')).toBe('Open')
    await admin.dispose()

    // The flag is this user's, and survives a reload.
    await page.reload()
    await page.getByTestId('shortcuts-hint').click()
    await expect(page.getByTestId('shortcuts-toggle')).toHaveAttribute('aria-checked', 'true')
    const stored = await page.evaluate((user) => localStorage.getItem(`helixhr.approvals.shortcuts:${user}`), MANAGER)
    expect(stored).toBe('1')
  } finally {
    await removeRecord(baseURL, 'Leave Application', leave)
  }
})

test('after a decision, focus moves to the next row (KTD16)', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(90000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const start = futureWeekday()
  // Same person, older first: the two rows sit next to each other.
  const first = await seedLeave(baseURL, addDays(start, 49), 'u7 focus one')
  const second = await seedLeave(baseURL, addDays(start, 56), 'u7 focus two')

  try {
    await page.setViewportSize(DESKTOP)
    await page.goto(`/helixhr/approvals/leave/${first}?view=queue`)
    const panel = page.getByTestId('approval-detail')
    await panel.getByTestId('approve').click()
    await expect(page.locator(`[data-approval-name="${first}"]`)).toHaveCount(0, { timeout: 15000 })
    await expect(page.locator(`[data-approval-name="${second}"] [data-row-toggle]`)).toBeFocused()
  } finally {
    await removeRecord(baseURL, 'Leave Application', first)
    await removeRecord(baseURL, 'Leave Application', second)
  }
})

// Approvals search: an old decision -- older than the 14-day Decided
// receipt -- is findable by its record name, the Decided list opens on its
// own and says "all time", the pending lanes narrow to what matches, and the
// shell's badge never takes a searched answer for the queue.
test('Approvals search finds a decision older than the receipt and leaves the badge alone', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'manager', 'this is the manager capability shape')
  test.setTimeout(90000)
  const baseURL = process.env.BASE_URL || 'http://localhost:8080'
  const start = futureWeekday()
  const waiting = await seedLeave(baseURL, addDays(start, 63), 'search pending leave')
  const old = await seedLeave(baseURL, addDays(start, 70), 'search old decision')

  try {
    const admin = await adminContext(baseURL)
    const modified = await getValue(admin, 'Leave Application', { name: old }, 'modified')
    const manager = await loginAs(baseURL, MANAGER)
    const rejected = await manager.post('/api/method/helixhr.api.act_on_approval', {
      form: {
        doctype: 'Leave Application',
        name: old,
        action: 'Reject',
        comment: 'search spec',
        expected_modified: modified,
        expected_state: 'Open',
      },
    })
    expect(rejected.ok(), await rejected.text()).toBeTruthy()
    await manager.dispose()
    const backdated = await admin.post('/api/method/helixhr.tests.utils.backdate_decided_leave', {
      form: { name: old, days: 40 },
    })
    expect(backdated.ok(), await backdated.text()).toBeTruthy()
    await admin.dispose()

    await page.setViewportSize(DESKTOP)
    await page.goto('/helixhr/approvals?view=queue')
    const queue = page.getByTestId('approvals-queue')
    await expect(queue.locator(`[data-approval-name="${waiting}"]`)).toBeVisible({ timeout: 15000 })
    // Outside the 14-day receipt, so not listed until searched for.
    await expect(queue.locator(`[data-decided-name="${old}"]`)).toHaveCount(0)
    const badge = page.getByTestId('nav-badge-approvals')
    await expect(badge).toBeVisible()
    const badgeText = await badge.textContent()

    await page.getByTestId('approvals-search').fill(old)
    await expect(page.getByTestId('approvals-search-hint')).toBeVisible({ timeout: 15000 })
    const toggle = page.getByTestId('decided-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(toggle).toContainText('all time')
    const row = queue.locator(`[data-decided-name="${old}"]`)
    await expect(row).toBeVisible()
    await expect(row).toContainText('Decided')
    // The pending lanes narrow to what matches: the waiting leave is not it.
    await expect(queue.locator(`[data-approval-name="${waiting}"]`)).toHaveCount(0)
    await expect(badge).toHaveText(badgeText || '')

    // Clearing the search is the plain queue again.
    await page.getByTestId('approvals-search').fill('')
    await expect(queue.locator(`[data-approval-name="${waiting}"]`)).toBeVisible({ timeout: 15000 })
    await expect(page.getByTestId('approvals-search-hint')).toHaveCount(0)
    await expect(queue.locator(`[data-decided-name="${old}"]`)).toHaveCount(0)
    await expect(badge).toHaveText(badgeText || '')

    // Nothing matches: one plain empty state that names the term.
    await page.getByTestId('approvals-search').fill('zz-no-such-approval')
    await expect(page.getByText('No approvals match “zz-no-such-approval”')).toBeVisible({ timeout: 15000 })
  } finally {
    await removeRecord(baseURL, 'Leave Application', waiting)
    await removeRecord(baseURL, 'Leave Application', old)
  }
})
