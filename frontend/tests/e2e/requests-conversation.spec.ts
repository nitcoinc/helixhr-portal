import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// Plan 2026-10-15-003 U4. Both sides of a request's conversation in the
// browser. Each test files its own request through the portal's own method,
// so it never depends on another spec's seeded rows. The other side acts
// through the API, logged in as that fixture identity.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const BASE_URL = process.env.BASE_URL || 'http://localhost:8080'
const EMPLOYEE = 'employee@helixhr.test'
const HR = 'hr-manager-employee@helixhr.test'

async function login(user: string, pwd = PASSWORD): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: { Host: SITE_HOST } })
  const response = await api.post('/api/method/login', { form: { usr: user, pwd } })
  expect(response.ok(), `login ${user}`).toBeTruthy()
  return api
}

async function call(api: APIRequestContext, method: string, form: Record<string, string>) {
  const response = await api.post(`/api/method/helixhr.api.${method}`, { form })
  expect(response.ok(), await response.text()).toBeTruthy()
  return (await response.json()).message
}

async function fileRequest(category: string, subject: string) {
  const employee = await login(EMPLOYEE)
  const created = await call(employee, 'create_my_request', {
    category,
    subject,
    details: 'Opened by the conversation spec.',
    operation_key: crypto.randomUUID(),
  })
  await employee.dispose()
  return created.name as string
}

async function decide(user: string, name: string, action: string, comment?: string) {
  const api = await login(user)
  const detail = await call(api, 'get_approval_detail', { kind: 'request', name })
  await call(api, 'act_on_approval', {
    doctype: 'HR Request',
    name,
    action,
    ...(comment ? { comment } : {}),
    expected_modified: detail.modified,
    expected_state: detail.state,
  })
  await api.dispose()
}

async function status(name: string) {
  const admin = await login('Administrator', 'admin')
  const response = await admin.get(
    `/api/method/frappe.client.get_value?doctype=HR%20Request&fieldname=status&filters=${encodeURIComponent(
      JSON.stringify({ name }),
    )}`,
  )
  const value = (await response.json()).message.status
  await admin.dispose()
  return value
}

test('employee comments while Open, replies to Need info, and a Done request is read-only', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'employee', 'the employee side')
  test.setTimeout(60000)
  const name = await fileRequest('HR Letter', 'Conversation spec: employee side')

  await page.goto(`/helixhr/requests/${name}`)
  const thread = page.getByTestId('request-thread')
  await expect(thread).toBeVisible({ timeout: 10000 })
  await thread.getByLabel('Add a comment').fill('Any update on this?')
  await thread.getByTestId('thread-post').click()
  await expect(thread.getByRole('list', { name: 'Conversation' })).toContainText('Any update on this?')
  await expect(thread.getByTestId('thread-box')).toHaveValue('')
  expect(await status(name)).toBe('Open')

  await decide(HR, name, 'Need info', 'Which month do you need it for?')
  await page.reload()
  await expect(thread.getByRole('list', { name: 'Conversation' })).toContainText('Which month do you need it for?', {
    timeout: 10000,
  })
  await thread.getByLabel('Reply').fill('August, please.')
  await thread.getByTestId('thread-post').click()
  await expect.poll(() => status(name), { timeout: 15000 }).toBe('In Progress')

  await decide(HR, name, 'Done')
  await page.reload()
  await expect(thread).toContainText('This request is closed.', { timeout: 10000 })
  await expect(thread.getByTestId('thread-box')).toHaveCount(0)
  await expect(thread.getByTestId('thread-new-request')).toBeVisible()
})

test('HR sees the employee by name and asks for info straight from Open', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the HR handler side')
  test.setTimeout(60000)
  const name = await fileRequest('HR Letter', 'Conversation spec: HR side')
  const employee = await login(EMPLOYEE)
  await call(employee, 'reply_to_my_request', { name, message: 'Needed for a visa.' })
  await employee.dispose()

  await page.goto(`/helixhr/requests/${name}?tab=work`)
  const detail = page.getByTestId('work-detail')
  const thread = detail.getByTestId('request-thread')
  await expect(thread).toContainText('Needed for a visa.', { timeout: 10000 })
  await expect(thread.getByLabel(/^Comment \(visible to .+\)$/)).toBeVisible()

  // R9: one message box; Need info is its "Send and wait for reply".
  await expect(detail.getByTestId('need-info')).toHaveCount(0)
  await expect(thread.getByTestId('need-info-assigns')).toHaveText('This also assigns the request to you.')
  await thread.getByTestId('thread-box').fill('Which country is the visa for?')
  await thread.getByTestId('thread-send-and-wait').click()
  await expect.poll(() => status(name), { timeout: 15000 }).toBe('Waiting on Employee')
})

test('IT comments on its request from Approvals at phone width and the employee sees it', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'the routed IT worker')
  test.setTimeout(60000)
  const name = await fileRequest('IT / Asset', 'Conversation spec: IT side')

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/helixhr/approvals/request/${name}`)
  const thread = page.getByTestId('request-thread').filter({ visible: true })
  await expect(thread).toBeVisible({ timeout: 10000 })
  await thread.getByTestId('thread-box').fill('A replacement ships Friday.')
  await thread.getByTestId('thread-post').click()
  await expect(thread.getByRole('list', { name: 'Conversation' })).toContainText('A replacement ships Friday.')
  expect(await status(name)).toBe('Open')

  const employee = await login(EMPLOYEE)
  const mine = await call(employee, 'get_my_request', { name })
  await employee.dispose()
  expect(mine.thread.map((entry: { message: string }) => entry.message)).toContain('A replacement ships Friday.')
})

test('handler Send and wait moves to waiting, employee sees Needs your reply banner, handler declines waiting request', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the HR handler side')
  test.setTimeout(60000)
  const name = await fileRequest('HR Letter', 'Conversation spec: AE2/AE3 flow')

  // Handler opens in team inbox
  await page.goto(`/helixhr/requests/${name}?tab=work`)
  const detail = page.getByTestId('work-detail')
  const thread = detail.getByTestId('request-thread')

  // Send and wait for reply from message box
  await thread.getByTestId('thread-box').fill('Which specific date?')
  await thread.getByTestId('thread-send-and-wait').click()
  await expect.poll(() => status(name), { timeout: 15000 }).toBe('Waiting on Employee')

  // Verify thread has question entry
  await expect(thread.getByTestId('thread-question')).toContainText('Which specific date?')

  // R11: the employee's read carries the waiting status and the question
  // entry the "Needs your reply" banner highlights
  const employee = await login(EMPLOYEE)
  const mine = await call(employee, 'get_my_request', { name })
  await employee.dispose()
  expect(mine.status).toBe('Waiting on Employee')
  expect(mine.thread.find((entry: { kind: string }) => entry.kind === 'question')?.message).toBe('Which specific date?')

  // Handler can decline from waiting (R7, AE3)
  await detail.getByTestId('reject').click()
  await detail.getByRole('textbox', { name: 'Decline with a reason' }).fill('No response required')
  await detail.getByTestId('work-reason-confirm').click()
  await expect.poll(() => status(name), { timeout: 15000 }).toBe('Rejected')
  await expect(thread.getByTestId('thread-declined')).toContainText('No response required')
})


// Plan 2026-10-17-001 U8 (R13). Triage from the inbox list: search, Assign
// to me on the row, the stale-row refusal, and the phone layout. Each test
// files its own requests under one stamp and searches for it, so the
// long-lived site's other rows never decide what is on screen.
async function searchInbox(page: Page, text: string) {
  await page.goto('/helixhr/requests?tab=work')
  await page.getByTestId('work-search').fill(text)
}

function inboxRow(page: Page, subject: string) {
  return page.getByTestId('work-row').filter({ hasText: subject })
}

test('HR assigns a New request to themselves from the list, without opening it', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the HR handler side')
  test.setTimeout(60000)
  const stamp = `U8 assign ${Date.now()}`
  const name = await fileRequest('HR Letter', `${stamp} letter`)

  await searchInbox(page, stamp)
  const row = inboxRow(page, `${stamp} letter`)
  await expect(page.locator('[data-group="new"]').getByTestId('work-row').filter({ hasText: stamp })).toHaveCount(1, {
    timeout: 10000,
  })
  await row.getByTestId('row-pick-up').click()

  await expect(page.getByTestId('toast').filter({ hasText: `Assigned to you: ${stamp} letter` })).toBeVisible()
  await expect(page).toHaveURL(/\/requests\?tab=work$/)
  await expect(page.locator('[data-group="in_progress"]').getByTestId('work-row').filter({ hasText: stamp })).toHaveCount(1)
  // Picked up by me: nothing left to assign on this row.
  await expect(row.getByTestId('row-pick-up')).toHaveCount(0)
  // KTD16: the New group emptied, so focus lands on the first group heading.
  await expect(page.locator('[data-group] h3').first()).toBeFocused()
  expect(await status(name)).toBe('In Progress')
})

test('a row somebody else just took is refused with the server sentence and refreshes', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the HR handler side')
  test.setTimeout(60000)
  const stamp = `U8 race ${Date.now()}`
  const name = await fileRequest('HR Letter', `${stamp} letter`)

  await searchInbox(page, stamp)
  const row = inboxRow(page, `${stamp} letter`)
  await expect(row.getByTestId('row-pick-up')).toBeVisible({ timeout: 10000 })
  // The other handler wins while this list is on screen.
  const admin = await login('Administrator', 'admin')
  const detail = await call(admin, 'get_approval_detail', { kind: 'request', name })
  await call(admin, 'act_on_approval', {
    doctype: 'HR Request',
    name,
    action: 'Pick up',
    expected_modified: detail.modified,
    expected_state: detail.state,
  })
  await admin.dispose()
  expect(await status(name)).toBe('In Progress')

  await row.getByTestId('row-pick-up').click()
  await expect(page.locator('[data-testid="toast"][data-tone="error"]')).toBeVisible()
  await expect(page.locator('[data-group="in_progress"]').getByTestId('work-row').filter({ hasText: stamp })).toHaveCount(1)
  await expect(row.getByTestId('row-pick-up')).toHaveCount(0)
})

test('the inbox search narrows by subject and clearing it restores the list', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the HR handler side')
  test.setTimeout(60000)
  const stamp = `U8 search ${Date.now()}`
  await fileRequest('HR Letter', `${stamp} alpha`)
  await fileRequest('HR Letter', `${stamp} beta`)

  await searchInbox(page, `${stamp} alpha`)
  const rows = page.getByTestId('work-row')
  await expect(rows).toHaveCount(1, { timeout: 10000 })
  await expect(rows.first()).toContainText(`${stamp} alpha`)

  await page.getByTestId('work-search').fill(stamp)
  await expect(rows.filter({ hasText: stamp })).toHaveCount(2)

  await page.getByTestId('work-search').fill('')
  await expect.poll(() => rows.count(), { timeout: 10000 }).toBeGreaterThan(1)
  await expect(page.locator('[data-async-state="work-list:ready"]')).toBeVisible()
})

test('at 390px an inbox row keeps the whole requester name clear of its badges', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the HR handler side')
  test.setTimeout(60000)
  const stamp = `U8 phone ${Date.now()}`
  const name = await fileRequest('HR Letter', `${stamp} a subject long enough to wrap on a phone`)
  const hr = await login(HR)
  const employeeName = (await call(hr, 'get_approval_detail', { kind: 'request', name })).employee_name
  await hr.dispose()

  await page.setViewportSize({ width: 390, height: 844 })
  await searchInbox(page, stamp)
  const row = inboxRow(page, stamp)
  await expect(row).toHaveCount(1, { timeout: 10000 })
  const requester = row.getByTestId('work-requester')
  await expect(requester).toHaveText(employeeName)
  // Normal case, not the 11px uppercase group label (AI-19).
  expect(await requester.evaluate((el) => getComputedStyle(el).textTransform)).toBe('none')
  // Nothing clipped, and the badges sit on their own line below the subject.
  expect(await requester.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
  const [person, link, button] = await Promise.all([
    requester.boundingBox(),
    row.getByRole('link').boundingBox(),
    row.getByTestId('row-pick-up').boundingBox(),
  ])
  expect(person && link && button).toBeTruthy()
  expect(person!.y + person!.height).toBeLessThanOrEqual(link!.y + 0.5)
  expect(button!.y).toBeGreaterThanOrEqual(link!.y + link!.height - 0.5)
})

test('an IT handler can assign IT rows from the list and never sees an HR-routed one', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'the routed IT worker')
  test.setTimeout(60000)
  const stamp = `U8 it ${Date.now()}`
  await fileRequest('IT / Asset', `${stamp} laptop`)
  await fileRequest('HR Letter', `${stamp} letter`)

  await searchInbox(page, stamp)
  await expect(inboxRow(page, `${stamp} laptop`).getByTestId('row-pick-up')).toBeVisible({ timeout: 10000 })
  await expect(inboxRow(page, `${stamp} letter`)).toHaveCount(0)
  await expect(page.getByTestId('row-pick-up')).toHaveCount(1)
  // One kind in this inbox, so no kind tag repeats "IT request" on every row.
  await expect(inboxRow(page, `${stamp} laptop`)).not.toContainText('IT request')
})
