import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// P2-U9 step 5: the upload policy checks the bytes, not only the name, so a
// fixture attachment has to be a real file. One page, produced once by pypdf.
const SAFE_PDF = Buffer.from(
  'JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNzIgNzIgXQovUGFyZW50IDIgMCBSCj4+CmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA1NCAwMDAwMCBuIAowMDAwMDAwMTEzIDAwMDAwIG4gCjAwMDAwMDAxNjIgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSA1Ci9Sb290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgoyNTQKJSVFT0YK',
  'base64',
)

// P2-U8. What the browser has to prove here is the *contract between two
// steps*: a request that commits, a file that may not, and one URL that both
// of them end up on.
//
// Ownership, the idempotency key's collision behaviour, company scope and the
// bounded page are asserted directly against the API in
// `helixhr/tests/test_hr_request.py`. What is left for a browser is what only
// a browser can be wrong about -- that the sheet states the rule, that a
// failed upload is told truthfully next to a Retry that targets the request
// that was made, that opening a reply clears it, and that a document link
// says where it goes before you follow it.

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

/** One request, made the way the portal makes one: the session-scoped method
 * with its own operation key. Role Employee has no generic create on HR
 * Request any more, so there is no other way to seed one as the employee. */
async function seedRequest(
  employeeApi: APIRequestContext,
  subject: string,
  extra: Record<string, string> = {},
) {
  const created = await employeeApi.post('/api/method/helixhr.api.create_my_request', {
    data: {
      category: 'HR Letter',
      subject,
      details: 'Seeded by requests-documents.spec.ts',
      operation_key: crypto.randomUUID(),
      ...extra,
    },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  return (await created.json())?.message?.name as string
}

/** HR replying, through a real save so the controller stamps `replied_on` and
 * `helixhr.events.hr_request_on_update` writes the Notification Log the queue
 * reads (P2-KTD6). `hr_note` only, deliberately not also `status`: Frappe's
 * own "HelixHR Request Status Changed" fixture Notification's channel is
 * "System Notification", which core dispatches via
 * `frappe.desk.doctype.notification_log.notification_log.enqueue_create_notification`
 * -- a genuine background job (confirmed against frappe/email/doctype/
 * notification/notification.py), not something synchronous within this
 * save. Changing status in the same call used to also fire that job, which
 * lands at a real but non-deterministic delay (measured several hundred ms
 * on this bench) -- consistently landing *after* mark_my_request_read on
 * CI's timing, reopening the row this test had just confirmed cleared. That
 * is Frappe core's own async behaviour, not this app's code, and this test
 * is about the reply-notification path specifically (KTD6, synchronous, ours
 * to guarantee) -- so it only changes the one field that path actually
 * reacts to. */
async function hrReplies(api: APIRequestContext, name: string, note: string) {
  const response = await api.post('/api/method/frappe.client.set_value', {
    data: { doctype: 'HR Request', name, fieldname: 'hr_note', value: note },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
}

async function removeRequest(api: APIRequestContext, name: string) {
  if (!name) return
  await api.post('/api/method/frappe.client.delete', { data: { doctype: 'HR Request', name } })
}

async function seedDocumentLink(
  api: APIRequestContext,
  fields: { title: string; url: string; description?: string; company?: string },
) {
  const created = await api.post('/api/method/frappe.client.insert', {
    data: { doc: JSON.stringify({ doctype: 'HelixHR Document Link', ...fields }) },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  return (await created.json())?.message?.name as string
}

async function removeDocumentLink(api: APIRequestContext, name: string) {
  if (!name) return
  await api.post('/api/method/frappe.client.delete', {
    data: { doctype: 'HelixHR Document Link', name },
  })
}

function detailPanel(page: Page) {
  return page.locator('[data-async-state^="request-detail"]')
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  // ── The sheet: four explained tiles and the rule stated up front ──────
  test('the new-request sheet offers four explained categories and states the attachment rule', async ({
    page,
  }) => {
    await page.goto('/helixhr/requests')
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.getByRole('heading', { name: 'Requests' })).toBeVisible()

    await page.getByRole('button', { name: 'New request' }).click()
    const sheet = page.getByRole('dialog')
    await expect(sheet).toBeVisible()

    for (const [title, hint] of [
      ['HR letter', 'Address, employment, visa'],
      ['Payroll', 'Payslip, tax, overtime'],
      ['IT / asset', 'Laptop, access, badge'],
      ['Something else', 'Anything HR can help with'],
    ]) {
      const tile = sheet.getByRole('button', { name: new RegExp(`^${title}`) })
      await expect(tile).toBeVisible()
      await expect(tile).toContainText(hint)
    }

    // HR letter is the resting choice, and it says so rather than looking so.
    await expect(sheet.getByRole('button', { name: /^HR letter/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await sheet.getByRole('button', { name: /^Payroll/ }).click()
    await expect(sheet.getByRole('button', { name: /^Payroll/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    // The limits are on screen before the file picker is used, not after the
    // upload is refused.
    await expect(sheet).toContainText('PDF, PNG or JPEG image, or a Word or Excel document')
    await expect(sheet).toContainText('10')

    // Nothing to send until it has a subject.
    await expect(sheet.getByRole('button', { name: 'Send to HR' })).toBeDisabled()
  })

  // ── Sending, and landing on the record that was made ──────────────────
  test('sending a request opens that request, with its timeline', async ({ page, baseURL }) => {
    const api = await admin(baseURL!)
    const subject = `P2-U8 send ${Date.now()}`
    let name = ''

    try {
      await page.goto('/helixhr/requests')
      await page.getByRole('button', { name: 'New request' }).click()
      const sheet = page.getByRole('dialog')
      await sheet.getByLabel('Subject').fill(subject)
      await sheet.getByLabel('Details').fill('Needed for a new bank account.')
      await sheet.getByRole('button', { name: 'Send to HR' }).click()

      // The URL is the record (P2-R12, KTD5), and it survives a reload.
      await expect(page).toHaveURL(/\/requests\/[^/]+$/)
      name = page.url().split('/').pop()!

      const detail = detailPanel(page)
      await expect(detail).toContainText(subject)
      await expect(detail).toContainText('Needed for a new bank account.')
      await expect(detail.locator('[data-testid="request-timeline"]')).toContainText('Sent')

      await page.reload()
      await expect(detailPanel(page)).toContainText(subject)
    } finally {
      await removeRequest(api, name)
      await api.dispose()
    }
  })

  // ── Scenario 1/AE7, in the browser: the request stands, the file didn't ─
  test('a failed upload keeps the request and retries the file against it', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const subject = `P2-U8 partial ${Date.now()}`
    let name = ''

    // Fail the *attachment* call once and only once. The create call is
    // untouched, which is exactly the state P2-R18 is about: a committed
    // request whose file did not make it.
    let failuresLeft = 1
    await page.route('**/api/method/helixhr.api.attach_to_my_request', async (route) => {
      if (failuresLeft > 0) {
        failuresLeft -= 1
        await route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
        return
      }
      await route.fallback()
    })

    try {
      await page.goto('/helixhr/requests')
      await page.getByRole('button', { name: 'New request' }).click()
      const sheet = page.getByRole('dialog')
      await sheet.getByLabel('Subject').fill(subject)
      await sheet.locator('input[type="file"]').setInputFiles({
        name: 'bank-form.pdf',
        mimeType: 'application/pdf',
        buffer: SAFE_PDF,
      })
      await sheet.getByRole('button', { name: 'Send to HR' }).click()

      await expect(page).toHaveURL(/\/requests\/[^/]+$/)
      name = page.url().split('/').pop()!

      // The truth, on the record it is about: the request was sent, the file
      // was not, and nothing here says the request failed.
      const failure = page.locator('[data-testid="upload-failed"]')
      await expect(failure).toContainText('bank-form.pdf didn’t upload')
      await expect(failure).toContainText('Your request was sent; only the file failed.')

      // And the request really exists, once.
      const stored = await api.get(
        '/api/method/frappe.client.get_count?doctype=HR%20Request&filters=' +
          encodeURIComponent(JSON.stringify({ subject })),
      )
      expect((await stored.json())?.message).toBe(1)

      // Retry sends the same bytes to the same request.
      await failure.getByRole('button', { name: 'Retry upload' }).click()
      await expect(page.locator('[data-testid="upload-failed"]')).toHaveCount(0)
      await expect(detailPanel(page).getByRole('link', { name: /bank-form\.pdf/ })).toBeVisible()

      const files = await api.get(
        '/api/method/frappe.client.get_count?doctype=File&filters=' +
          encodeURIComponent(
            JSON.stringify({ attached_to_doctype: 'HR Request', attached_to_name: name }),
          ),
      )
      expect((await files.json())?.message, 'the file must be attached exactly once').toBe(1)
    } finally {
      await removeRequest(api, name)
      await api.dispose()
    }
  })

  // ── Scenario 6: the notification lands on the request and stops asking ─
  test('an HR reply notification opens that request and clears itself', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const employeeApi = await asEmployee(baseURL!)
    const subject = `P2-U8 reply ${Date.now()}`
    let name = ''

    try {
      name = await seedRequest(employeeApi, subject)
      await hrReplies(api, name, 'Attached the signed letter.')

      // The reply is an obligation on the list before it is read.
      await page.goto('/helixhr/requests')
      const row = page.locator('[data-testid="request-row"]', { hasText: subject })
      await expect(row).toHaveAttribute('data-unread', '1')
      await expect(page.getByRole('heading', { name: 'Needs you' })).toBeVisible()

      // Opening the request is reading the reply: the detail clears the
      // obligation and says so, on the reply it is about.
      await row.getByRole('link', { name: subject }).click()
      await expect(page).toHaveURL(new RegExp(`/requests/${name}$`))
      const detail = detailPanel(page)
      await expect(detail).toContainText('Attached the signed letter.')
      await expect(detail.locator('[data-testid="marked-as-read"]')).toBeVisible()

      // Cleared for good, not just on this render.
      await page.goto('/helixhr/requests')
      await expect(
        page.locator('[data-testid="request-row"]', { hasText: subject }),
      ).toHaveAttribute('data-unread', '0')

      // And the notification itself lands on that record rather than the
      // list, whichever way round the two are read (P2-R12).
      await page.goto('/helixhr/notifications')
      await page.locator('[data-testid="notification-row"]', { hasText: subject }).first().click()
      await expect(page).toHaveURL(new RegExp(`/requests/${name}$`))
      await expect(detailPanel(page)).toContainText(subject)
    } finally {
      await removeRequest(api, name)
      await employeeApi.dispose()
      await api.dispose()
    }
  })

  // ── Documents: grouping, search, and where a link is about to send you ─
  test('documents are grouped, searchable, and say where each link goes', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const stamp = Date.now()
    const everyone = `P2-U8 handbook ${stamp}`
    const mine = `P2-U8 holiday list ${stamp}`
    let everyoneName = ''
    let mineName = ''

    try {
      const company = await api.get(
        '/api/method/frappe.client.get_value?doctype=Employee&filters=' +
          encodeURIComponent(JSON.stringify({ user_id: EMPLOYEE })) +
          '&fieldname=company',
      )
      const companyName = (await company.json())?.message?.company

      everyoneName = await seedDocumentLink(api, {
        title: everyone,
        url: 'https://example.com/policies/handbook.pdf',
        description: 'Working hours, conduct, benefits',
      })
      mineName = await seedDocumentLink(api, {
        title: mine,
        url: 'https://intranet.example.com/holidays',
        description: 'Public and company holidays',
        company: companyName,
      })

      await page.goto('/helixhr/documents')
      await expect(page.getByRole('heading', { name: 'Documents' })).toBeVisible()

      // The two scopes P2-R19 enforces, made visible as the two groups.
      await expect(page.getByRole('heading', { name: 'For everyone' })).toBeVisible()
      await expect(page.getByRole('heading', { name: companyName })).toBeVisible()

      // The host, and the type derived from the address.
      const handbook = page.getByRole('link', { name: new RegExp(everyone) })
      await expect(handbook).toContainText('example.com')
      await expect(handbook).toContainText('PDF')
      await expect(handbook).toHaveAttribute('target', '_blank')
      await expect(handbook).toHaveAttribute('rel', /noopener/)
      await expect(page.getByRole('link', { name: new RegExp(mine) })).toContainText(
        'intranet.example.com',
      )

      // Search narrows both groups at once, and a search that matches nothing
      // is not the empty catalogue (P2-R2).
      await page.getByLabel('Search').fill('holiday')
      await expect(page.getByRole('link', { name: new RegExp(mine) })).toBeVisible()
      await expect(page.getByRole('link', { name: new RegExp(everyone) })).toHaveCount(0)

      await page.getByLabel('Search').fill('nothing matches this at all')
      await expect(page.locator('[data-testid="documents-no-match"]')).toContainText('Ask HR')
      await expect(page.locator('[data-async-state="documents:empty"]')).toHaveCount(0)
    } finally {
      await removeDocumentLink(api, everyoneName)
      await removeDocumentLink(api, mineName)
      await api.dispose()
    }
  })

  // ── AE2, at the routes a browser can actually reach ───────────────────
  test('forged company filters and generic routes cannot widen Documents', async ({ baseURL }) => {
    const api = await admin(baseURL!)
    const employeeApi = await asEmployee(baseURL!)
    const stamp = Date.now()
    let otherName = ''

    try {
      // A link belonging to a company this employee is not in.
      const other = await api.post('/api/method/frappe.client.insert', {
        data: {
          doc: JSON.stringify({
            doctype: 'Company',
            company_name: `P2-U8 Other ${stamp}`,
            abbr: `PU${stamp % 10000}`,
            default_currency: 'USD',
            country: 'United States',
          }),
        },
      })
      const otherCompany = (await other.json())?.message?.name
      otherName = await seedDocumentLink(api, {
        title: `P2-U8 other office policy ${stamp}`,
        url: 'https://example.com/other',
        company: otherCompany,
      })

      // The portal method, the generic list with a forged filter, and the
      // REST resource route all answer the same way.
      const viaPortal = await employeeApi.get('/api/method/helixhr.api.get_my_documents')
      const portalNames = ((await viaPortal.json())?.message || []).map(
        (row: { name: string }) => row.name,
      )
      expect(portalNames).not.toContain(otherName)

      const forged = await employeeApi.get(
        '/api/method/frappe.client.get_list?doctype=HelixHR%20Document%20Link&filters=' +
          encodeURIComponent(JSON.stringify({ company: otherCompany })) +
          '&fields=' +
          encodeURIComponent(JSON.stringify(['name'])) +
          '&limit_page_length=0',
      )
      expect(((await forged.json())?.message || []).length).toBe(0)

      const resource = await employeeApi.get(
        `/api/resource/HelixHR Document Link/${encodeURIComponent(otherName)}`,
      )
      expect(resource.status(), 'the REST resource route must refuse it too').toBe(403)

      // And an unsafe link cannot exist to be rendered in the first place.
      const unsafe = await api.post('/api/method/frappe.client.insert', {
        data: {
          doc: JSON.stringify({
            doctype: 'HelixHR Document Link',
            title: `P2-U8 unsafe ${stamp}`,
            url: 'javascript:alert(1)',
          }),
        },
      })
      expect(unsafe.ok(), 'a javascript: URL must be refused').toBeFalsy()
    } finally {
      await removeDocumentLink(api, otherName)
      await employeeApi.dispose()
      await api.dispose()
    }
  })
})
