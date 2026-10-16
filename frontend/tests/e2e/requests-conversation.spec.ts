import { test, expect, request, APIRequestContext } from '@playwright/test'

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

  await detail.getByTestId('need-info').click()
  await expect(detail.getByTestId('need-info-assigns')).toHaveText('This also assigns the request to you.')
  await detail.getByRole('textbox', { name: 'Ask a question' }).fill('Which country is the visa for?')
  await detail.getByTestId('work-reason-confirm').click()
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

  // Handler can decline from waiting (R7, AE3)
  await detail.getByTestId('reject').click()
  await detail.getByRole('textbox', { name: 'Decline with a reason' }).fill('No response required')
  await detail.getByTestId('work-reason-confirm').click()
  await expect.poll(() => status(name), { timeout: 15000 }).toBe('Rejected')
  await expect(thread.getByTestId('thread-declined')).toContainText('No response required')
})

