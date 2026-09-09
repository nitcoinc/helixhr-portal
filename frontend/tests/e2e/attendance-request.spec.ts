import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// P3-U6 scenarios 1 and 2 / P3-AE7, P3-AE8. The two halves of "Fix a day" in
// a real browser: the employee raising a request and seeing who has it, and
// the manager reading it, sending it on, and being refused when they send it
// back with nothing or decide against evidence that has moved.
//
// Chromium `employee` and `manager` projects. The mobile WebKit project is
// deliberately out: it runs the read-shaped flows only, and both halves of
// this one write.

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const EMPLOYEE = 'employee@helixhr.test'
const MANAGER = 'manager@helixhr.test'

/** The server's own today, not the runner's host clock (the site runs in
 * Asia/Kolkata and CI in UTC). */
async function siteToday(page: Page): Promise<string> {
  const response = await page.request.get('/api/method/helixhr.api.get_portal_bootstrap')
  return (await response.json()).message.today as string
}

/** String date math, never `new Date('YYYY-MM-DD')` on a real timezone. */
function addDays(date: string, days: number): string {
  const parsed = new Date(`${date}T00:00:00Z`)
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return parsed.toISOString().slice(0, 10)
}

/**
 * A day well in the past, and a different one per test.
 *
 * Past, because the fixture holiday list only carries rows for this calendar
 * year and the seeded attendance sits near today: a day 200-odd days back is
 * a plain working day with nothing recorded on it, which is exactly the "No
 * record" day the feature exists for. Per test, because HRMS refuses two
 * overlapping Attendance Requests for one employee and the projects run in
 * parallel.
 */
function windowFor(today: string, slot: number): string {
  return addDays(today, -(200 + slot * 7))
}

async function adminContext(baseURL: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  return api
}

async function loginContext(baseURL: string, user: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: user, pwd: PASSWORD } })
  return api
}

async function getValue(
  api: APIRequestContext,
  doctype: string,
  filters: object,
  fieldname: string,
) {
  const response = await api.get(
    `/api/method/frappe.client.get_value?doctype=${encodeURIComponent(doctype)}&filters=` +
      encodeURIComponent(JSON.stringify(filters)) +
      `&fieldname=${fieldname}`,
  )
  return (await response.json())?.message?.[fieldname]
}

/** Start from nothing: a previous run's request over the same day would be
 * refused by HRMS's own overlap check before this spec got anywhere. */
async function clearRequestsOn(baseURL: string, day: string) {
  const admin = await adminContext(baseURL)
  const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
  const listed = await admin.get(
    '/api/method/frappe.client.get_list?doctype=Attendance%20Request&filters=' +
      encodeURIComponent(JSON.stringify({ employee, from_date: ['<=', day], to_date: ['>=', day] })) +
      '&fields=' +
      encodeURIComponent(JSON.stringify(['name', 'docstatus'])) +
      '&limit_page_length=0',
  )
  for (const row of (await listed.json())?.message || []) {
    if (row.docstatus === 1) {
      await admin.post('/api/method/frappe.client.cancel', {
        form: { doctype: 'Attendance Request', name: row.name },
      })
    }
    await admin.post('/api/method/frappe.client.delete', {
      form: { doctype: 'Attendance Request', name: row.name },
    })
  }
  await admin.dispose()
}

/**
 * One request already with the manager, raised through the portal's own two
 * methods so the workflow transition, the DocShare and the notification are
 * the real ones rather than a fixture's imitation of them.
 */
async function seedPendingRequest(baseURL: string, day: string, explanation: string) {
  await clearRequestsOn(baseURL, day)
  const api = await loginContext(baseURL, EMPLOYEE)
  const created = await api.post('/api/method/helixhr.api.create_my_attendance_request', {
    form: { from_date: day, to_date: day, reason: 'Work From Home', explanation },
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

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'this is the employee half')
  })

  // P3-U6 scenario 1 (employee half) and scenario 3.
  test('the preview counts the days before Send, and a sent request says who has it', async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(60000)
    const day = windowFor(await siteToday(page), 0)
    await clearRequestsOn(baseURL!, day)

    // `?fix=<date>` is the same pointer the day sheet and the check-in
    // sheet's fallback use, so this opens the sheet on the day under test
    // without 7 months of calendar paging.
    await page.goto(`/helixhr/attendance?fix=${day}`)
    const sheet = page.getByRole('dialog')
    await expect(sheet.getByRole('button', { name: 'Work From Home' })).toBeVisible({
      timeout: 10000,
    })

    // P3-R13: the count is the server's, and it is on screen before Send.
    const preview = page.getByTestId('attendance-request-preview')
    await expect(preview).toContainText('1 day would be marked as Work From Home', {
      timeout: 10000,
    })
    // P4-R6: one step. The manager's Approve submits the request and writes
    // the Attendance; HR is involved only if the manager hands it over.
    await expect(preview).toContainText('Goes to Manager for approval.')

    // A range that reaches into the future adds days nothing can say about
    // yet; extending it over the whole week is enough to move the number.
    await sheet.getByLabel('To').fill(addDays(day, 3))
    await expect(preview).toContainText('4 days would be marked as Work From Home', {
      timeout: 10000,
    })
    await sheet.getByLabel('To').fill(day)
    await expect(preview).toContainText('1 day would be marked as Work From Home', {
      timeout: 10000,
    })

    await sheet
      .getByLabel('Anything your manager should know (optional)')
      .fill('Fibre cut on my street')
    await sheet.getByRole('button', { name: 'Send to Manager' }).click()

    // P3-R17: the request, its two steps, and who has it -- without opening
    // anything.
    const column = page.getByTestId('attendance-requests')
    await expect(column.getByText('Waiting for Manager')).toBeVisible({ timeout: 10000 })
    await expect(column.getByTestId('step-strip').first()).toBeVisible()

    await column.getByRole('button').first().click()
    const detail = page.getByTestId('attendance-request-detail')
    await expect(detail).toContainText('Fibre cut on my street', { timeout: 10000 })
    await expect(detail.getByRole('button', { name: 'Withdraw' })).toBeVisible()

    // P3-U6 scenario 4. `/attendance/requests/:name` is what Home's "Needs
    // you" row and every notification about this request link to, so it has
    // to open the request on its own -- refresh included.
    const admin = await adminContext(baseURL!)
    const employee = await getValue(admin, 'Employee', { user_id: EMPLOYEE }, 'name')
    const name = await getValue(admin, 'Attendance Request', { employee, from_date: day }, 'name')
    await admin.dispose()

    await page.goto(`/helixhr/attendance/requests/${name}`)
    await expect(page.getByTestId('attendance-request-detail')).toContainText(
      'Fibre cut on my street',
      { timeout: 10000 },
    )

    await clearRequestsOn(baseURL!, day)
  })

  // P3-U9 regression. A failure whose message survives tag-stripping as
  // nothing at all -- markup with no words in it, which is what a proxy or a
  // bare `frappe.throw("<br>")` produces -- left `error` as '', and the error
  // line is `v-if="error"`: Send did nothing and said nothing.
  test('a Send that fails without a message still says something', async ({ page, baseURL }) => {
    test.setTimeout(60000)
    const day = windowFor(await siteToday(page), 0)
    await clearRequestsOn(baseURL!, day)

    await page.goto(`/helixhr/attendance?fix=${day}`)
    const sheet = page.getByRole('dialog')
    await expect(sheet.getByRole('button', { name: 'Work From Home' })).toBeVisible({
      timeout: 10000,
    })
    await expect(page.getByTestId('attendance-request-preview')).toContainText(
      'would be marked as Work From Home',
      { timeout: 10000 },
    )

    // The draft never gets made, and the failure carries no sentence: its
    // one server message is markup that strips to an empty string.
    await page.route('**/api/method/helixhr.api.create_my_attendance_request', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ _server_messages: JSON.stringify(['{"message": "<br>"}']) }),
      }),
    )
    await sheet.getByRole('button', { name: 'Send to Manager' }).click()

    await expect(sheet.getByRole('alert')).toContainText('Something went wrong. Please try again.', {
      timeout: 10000,
    })

    await clearRequestsOn(baseURL!, day)
  })
})

test.describe('manager', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'manager', 'this is the manager half')
  })

  // P3-U6 scenario 1 (manager half) / P3-AE7
  test('the third kind carries the quote, and Send to HR moves it on', async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(60000)
    const day = windowFor(await siteToday(page), 1)
    const name = await seedPendingRequest(baseURL!, day, 'Client site all day')

    await page.goto('/helixhr/approvals')
    const row = page.getByTestId('approvals-queue').locator(`[data-approval-name="${name}"]`)
    await expect(row).toBeVisible({ timeout: 10000 })
    await expect(row).toContainText('Work From Home')
    // P2-AE6 still holds for the third kind: nothing to decide with until the
    // evidence is on screen.
    await expect(page.getByRole('button', { name: 'Send to HR' })).toHaveCount(0)

    await row.click()
    await expect(page).toHaveURL(new RegExp(`/helixhr/approvals/attendance/${name}$`))

    const panel = page.getByTestId('approval-detail')
    await expect(panel).toContainText('Client site all day', { timeout: 10000 })
    await expect(panel).toContainText('the calendar shows nothing recorded')

    // P4-U4: Send to HR opens its optional note first, then fires. Every
    // outcome that carries words to another person is confirmed once.
    await panel.getByRole('button', { name: 'Send to HR' }).click()
    await expect(panel.getByTestId('hr-note')).toBeVisible()
    await panel.getByRole('button', { name: 'Send to HR' }).click()

    const admin = await adminContext(baseURL!)
    await expect
      .poll(
        async () => getValue(admin, 'Attendance Request', { name }, 'workflow_state'),
        { timeout: 10000 },
      )
      .toBe('Pending HR')
    // HR's step writes the Attendance, not the manager's (P3-R15).
    const attendance = await admin.get(
      '/api/method/frappe.client.get_list?doctype=Attendance&filters=' +
        encodeURIComponent(JSON.stringify({ attendance_request: name })) +
        '&limit_page_length=0',
    )
    expect((await attendance.json())?.message || []).toHaveLength(0)
    await admin.dispose()

    await clearRequestsOn(baseURL!, day)
  })

  // P3-U6 scenario 2 / P3-AE8
  test('Send back needs a reason, and a decision against stale evidence is refused', async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(60000)
    const day = windowFor(await siteToday(page), 2)
    const name = await seedPendingRequest(baseURL!, day, 'Worked from home')

    await page.goto(`/helixhr/approvals/attendance/${name}`)
    const panel = page.getByTestId('approval-detail')
    await expect(panel.getByRole('button', { name: 'Send to HR' })).toBeVisible({ timeout: 10000 })

    // P4-U4. All four outcomes are on screen, because the server said all
    // four are legal on a Pending Manager attendance request for its own
    // manager (P4-R1) -- and Approve is the decision again, not a hand-over.
    await expect(panel.getByRole('button', { name: /^Approve 1 day$/ })).toBeVisible()
    await expect(panel.getByTestId('send-back')).toBeVisible()
    await expect(panel.getByTestId('reject')).toBeVisible()
    await expect(panel.getByTestId('send-to-hr')).toBeVisible()

    // Client-side: no reason, no send back. The first tap opens the surface,
    // the second is the one that would fire.
    await panel.getByTestId('send-back').click()
    await expect(panel.getByTestId('decision-reason')).toContainText('Send back with a reason')
    await panel.getByTestId('send-back').click()
    await expect(page.getByText('Say what should change before sending it back.')).toBeVisible()

    // P4-U4. Switching outcomes empties and relabels the field: a sentence
    // written to ask for a change must never be submittable as the
    // justification for a terminal no.
    const reasonBox = panel.getByTestId('decision-reason')
    await reasonBox.getByRole('textbox').fill('add the Friday hours')
    await panel.getByTestId('reject').click()
    await expect(reasonBox).toContainText('Reject with a reason')
    await expect(reasonBox.getByRole('textbox')).toHaveValue('')
    await expect(page.getByText('Say what should change before sending it back.')).toHaveCount(0)
    await panel.getByTestId('reject').click()
    await expect(page.getByText('Say why before rejecting this.')).toBeVisible()

    // Server-side: the same refusal, with the browser out of the way. Its own
    // logged-in API context rather than `page.request`, which carries the
    // session cookie but not the CSRF token the browser puts on every write.
    const managerApi = await loginContext(baseURL!, MANAGER)
    const before = await getValue(managerApi, 'Attendance Request', { name }, 'modified')
    const refused = await managerApi.post('/api/method/helixhr.api.act_on_approval', {
      form: {
        doctype: 'Attendance Request',
        name,
        action: 'Reject',
        expected_modified: before,
        expected_state: 'Pending Manager',
      },
    })
    expect(refused.ok()).toBeFalsy()
    expect(await refused.text()).toContain('Say why before rejecting this.')
    await managerApi.dispose()

    // Now move the record under the manager, exactly as an HR edit would, and
    // decide against what is still on screen.
    const admin = await adminContext(baseURL!)
    const changed = await admin.post('/api/method/frappe.client.set_value', {
      form: {
        doctype: 'Attendance Request',
        name,
        fieldname: 'explanation',
        value: 'HR touched this',
      },
    })
    expect(changed.ok(), await changed.text()).toBeTruthy()

    await panel.getByTestId('send-to-hr').click()
    await panel.getByTestId('send-to-hr').click()
    await expect(page.getByRole('alert').filter({ hasText: /Reload/ })).toBeVisible({
      timeout: 10000,
    })
    expect(await getValue(admin, 'Attendance Request', { name }, 'workflow_state')).toBe(
      'Pending Manager',
    )
    await admin.dispose()

    await clearRequestsOn(baseURL!, day)
  })
})
