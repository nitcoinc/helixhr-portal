import { test, expect, request, APIRequestContext, Page } from '@playwright/test'

// P2-U4. Home is a list of exact obligations and Notifications is the loop
// that clears them, so what these travel are the two things that were wrong:
// where a row *goes*, and whether reading something actually closes it.
//
// The payloads are stubbed rather than seeded. The server semantics -- item
// identity, ordering, capability, the hr_note event and read state -- are
// asserted directly against the API in helixhr/tests/; what is left for the
// browser is the contract between that payload and the screen, and stubbing
// it is the only way to put two *specific* sent-back weeks and a known
// unread reply in front of a shared dev site that has neither.

const WEEK_START = '2026-08-31'

function week() {
  return {
    week_start: WEEK_START,
    week_end: '2026-09-06',
    total_hours: 12,
    timesheet_state: null,
    days: Array.from({ length: 7 }, (_, offset) => ({
      date: `2026-0${offset < 1 ? '8' : '9'}-${offset < 1 ? '31' : `0${offset}`}`,
      weekday: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][offset],
      day_of_month: offset < 1 ? 31 : offset,
      is_today: offset === 2,
      is_future: offset > 2,
      attendance: null,
      hours: 0,
      on_leave: false,
    })),
  }
}

function dashboard({ needsYou, failed = [] }) {
  return {
    message: {
      employee: {
        name: 'HR-EMP-00002',
        employee_name: 'Employee',
        designation: null,
        department: null,
        branch: null,
        reports_to: 'HR-EMP-00001',
        manager_name: 'Manager',
      },
      leave_balances: {},
      attendance_this_month: failed.includes('attendance_this_month') ? null : { Present: 3 },
      week: week(),
      needs_you: needsYou,
      failed_sections: failed,
    },
  }
}

function rejectedWeek(start: string, detail: string, ageDays: number) {
  return {
    id: `timesheet_rejected:TS-${start}`,
    kind: 'timesheet_rejected',
    notification: null,
    title: 'Your timesheet was sent back',
    detail,
    date: start,
    day: null,
    age_days: ageDays,
    action: 'Edit and resubmit',
    owner: 'you',
    urgency: 'blocked',
    tone: 'danger',
    to: { name: 'TimesheetWeek', params: { weekStart: start } },
  }
}

/** One collapsed "HR replied" row, shaped the way `_get_needs_you` sends it
 * after the KTD10 collapse: the id is the record, `count` the replies. */
function repliedRow(record: string, subject: string, count: number, newest: string) {
  return {
    id: `request_answered:${record}`,
    kind: 'request_answered',
    reference_doctype: 'HR Request',
    reference_name: record,
    count,
    notification: `NL-${record}`,
    title: count > 1 ? `HR replied · ${count} · ${subject}` : `HR replied about ${subject}`,
    detail: newest,
    date: '2026-09-01 09:30:00',
    day: null,
    age_days: 1,
    action: 'Read',
    owner: 'you',
    urgency: 'unread',
    tone: 'info',
    to: { name: 'RequestDetail', params: { name: record } },
  }
}

async function stubDashboard(page: Page, body: object) {
  await page.route('**/api/method/helixhr.api.get_dashboard*', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

test.describe('the Home action queue (P2-AE5)', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
  })

  test('two sent-back weeks each open their own week and their own reason', async ({ page }) => {
    await stubDashboard(
      page,
      dashboard({
        needsYou: {
          items: [
            rejectedWeek('2026-08-10', 'Friday hours are missing.', 26),
            rejectedWeek(WEEK_START, 'Add the project on Tuesday.', 5),
          ],
          more: 0,
          waiting: [],
        },
      }),
    )

    await page.goto('/helixhr')
    const queue = page.getByRole('region', { name: 'Needs you' })
    const rows = queue.getByRole('listitem')
    await expect(rows).toHaveCount(2)
    await expect(rows.first().getByText('“Friday hours are missing.”')).toBeVisible()

    // The bug: both rows linked to "/timesheet", which resolves to whichever
    // week is current when you follow it -- so the older item opened the
    // wrong week and the wrong reason.
    await rows.first().getByRole('link', { name: /Edit and resubmit/ }).click()
    await expect(page).toHaveURL(/\/helixhr\/timesheet\/2026-08-10$/)
  })

  test('leave waiting on a manager sits outside the queue and opens that leave', async ({
    page,
  }) => {
    await stubDashboard(
      page,
      dashboard({
        needsYou: {
          items: [],
          more: 0,
          waiting: [
            {
              id: 'leave_waiting:HR-LAP-2026-TEST',
              kind: 'leave_waiting',
              notification: null,
              title: 'Casual Leave waiting for your manager',
              detail: null,
              date: '2026-09-14',
              day: null,
              age_days: null,
              action: 'View',
              owner: 'manager',
              urgency: 'waiting',
              tone: 'muted',
              to: { name: 'LeaveDetail', params: { name: 'HR-LAP-2026-TEST' } },
            },
          ],
        },
      }),
    )

    await page.goto('/helixhr')
    const queue = page.getByRole('region', { name: 'Needs you' })
    // The queue itself is clear -- waiting on somebody else is not work.
    await expect(queue.getByText('Nothing needs you.')).toBeVisible()
    await expect(queue.getByRole('heading', { name: 'Waiting on others' })).toBeVisible()

    await queue.getByRole('link', { name: /Casual Leave waiting for your manager/ }).click()
    await expect(page).toHaveURL(/\/helixhr\/leave\/HR-LAP-2026-TEST$/)
  })

  // Plan 2026-10-17-001 U6 (EP-03, ED-08, KTD10). The server collapses
  // four replies on one request into one row; the row is the one link, and
  // opening it clears every log about that record, not just the newest.
  test('four replies are one row, and tapping it opens the request and clears it', async ({
    page,
  }) => {
    const record = 'HR-REQ-2026-U6'
    let cleared: Record<string, string> | null = null
    await page.route('**/api/method/helixhr.api.get_dashboard*', (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify(
          dashboard({
            needsYou: {
              items: cleared ? [] : [repliedRow(record, 'Address proof', 4, 'Collect it from reception.')],
              more: 0,
              waiting: [],
            },
          }),
        ),
      }),
    )
    await page.route('**/api/method/helixhr.api.mark_record_notifications_read*', (route) => {
      cleared = route.request().postDataJSON()
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ message: { cleared: 4, unread: 0 } }),
      })
    })
    await page.route('**/api/method/helixhr.api.get_request_view*', (route) =>
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ message: 'employee' }) }),
    )

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/helixhr')
    const queue = page.getByRole('region', { name: 'Needs you' })
    const rows = queue.getByRole('listitem')
    await expect(rows).toHaveCount(1)
    const row = rows.first()
    await expect(row.getByRole('link', { name: /HR replied · 4 · Address proof/ })).toBeVisible()
    await expect(row.getByText('“Collect it from reception.”')).toBeVisible()
    // EP-03: one line of title, the verb beside it -- a short row on a phone.
    const box = await row.boundingBox()
    expect(box!.height, `the row is ${box!.height}px tall`).toBeLessThanOrEqual(80)

    // Anywhere on the row: the date corner is not the link's text.
    await row.click({ position: { x: box!.width - 8, y: box!.height - 8 } })
    await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${record}$`))
    await expect.poll(() => cleared).toEqual({ reference_doctype: 'HR Request', reference_name: record })

    await page.goBack()
    await expect(queue.getByText('Nothing needs you.')).toBeVisible()
    await expect(queue.getByRole('link', { name: /HR replied/ })).toHaveCount(0)
  })

  test('two requests with different subjects read differently (HR-04)', async ({ page }) => {
    await stubDashboard(
      page,
      dashboard({
        needsYou: {
          items: [
            repliedRow('HR-REQ-2026-A', 'Address proof', 1, 'Ready at reception.'),
            repliedRow('HR-REQ-2026-B', 'Salary certificate', 1, 'Sent by email.'),
          ],
          more: 0,
          waiting: [],
        },
      }),
    )

    await page.goto('/helixhr')
    const queue = page.getByRole('region', { name: 'Needs you' })
    await expect(queue.getByRole('link', { name: /Address proof/ })).toHaveAttribute(
      'href',
      '/helixhr/requests/HR-REQ-2026-A',
    )
    await expect(queue.getByRole('link', { name: /Salary certificate/ })).toHaveAttribute(
      'href',
      '/helixhr/requests/HR-REQ-2026-B',
    )
  })

  // KTD11 / P4-R4: a final rejection waits on nobody, so it is "Decided" --
  // and it is still on Home, with its reason.
  test('a final rejection sits under Decided, not Waiting on others', async ({ page }) => {
    await stubDashboard(
      page,
      dashboard({
        needsYou: {
          items: [],
          more: 0,
          waiting: [],
          waiting_more: 0,
          decided: [
            {
              id: 'leave_final_rejected:HR-LAP-2026-U6',
              kind: 'leave_final_rejected',
              reference_doctype: 'Leave Application',
              reference_name: 'HR-LAP-2026-U6',
              count: 1,
              notification: null,
              title: 'Your Casual Leave was rejected',
              detail: 'Quarter close, we need everyone that week.',
              date: '2025-08-11',
              day: null,
              age_days: 430,
              action: 'View',
              owner: 'nobody',
              urgency: 'waiting',
              tone: 'muted',
              to: { name: 'LeaveDetail', params: { name: 'HR-LAP-2026-U6' } },
            },
          ],
          decided_more: 0,
        },
      }),
    )

    await page.goto('/helixhr')
    const queue = page.getByRole('region', { name: 'Needs you' })
    await expect(queue.getByRole('heading', { name: 'Decided' })).toBeVisible()
    await expect(queue.getByRole('heading', { name: 'Waiting on others' })).toHaveCount(0)
    const row = queue.locator('li[data-kind="leave_final_rejected"]')
    await expect(row.getByText('“Quarter close, we need everyone that week.”')).toBeVisible()
    await row.getByRole('link').click()
    await expect(page).toHaveURL(/\/helixhr\/leave\/HR-LAP-2026-U6$/)
  })

  // EP-09: "View all (N)" counts records -- seven collapsed rows standing for
  // twenty-one replies is seven -- and an employee's opens Notifications.
  test('View all counts records, not notifications', async ({ page }) => {
    await stubDashboard(
      page,
      dashboard({
        needsYou: {
          items: Array.from({ length: 7 }, (_, i) =>
            repliedRow(`HR-REQ-2026-V${i}`, `Subject ${i}`, 3, `Reply ${i}`),
          ),
          more: 0,
          waiting: [],
        },
      }),
    )

    await page.goto('/helixhr')
    const queue = page.getByRole('region', { name: 'Needs you' })
    await queue.getByRole('link', { name: 'View all (7)' }).click()
    await expect(page).toHaveURL(/\/helixhr\/notifications$/)
  })

  test('a failed attendance section labels only itself and offers Retry', async ({ page }) => {
    await stubDashboard(
      page,
      dashboard({
        needsYou: { items: [], more: 0, waiting: [] },
        failed: ['attendance_this_month'],
      }),
    )

    await page.goto('/helixhr')

    const attendance = page.locator('[data-async-state="attendance:unavailable"]')
    await expect(attendance).toBeVisible()
    await expect(attendance.getByText("We couldn't load your attendance")).toBeVisible()
    await expect(attendance.getByRole('button', { name: 'Retry' })).toBeVisible()

    // Everything else on the page still works, and nothing else claims to be
    // broken -- the whole point of naming the section (P2-R25).
    await expect(page.getByRole('region', { name: 'This week' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Needs you' })).toBeVisible()
    await expect(page.getByRole('link', { name: /Leave left/ })).toBeVisible()
    await expect(page.locator('[data-async-state$=":unavailable"]')).toHaveCount(1)
  })
})

// P2-U4 / P2-R13. Reading a notification has to move three things in the same
// interaction: the row, the shell's count, and the Home queue.

const LOG_UNREAD = {
  name: 'NL-REPLY-1',
  subject: 'HR replied about Address proof',
  description: '<div>Collect it from reception.</div>',
  document_type: 'HR Request',
  document_name: 'HR-REQ-2026-TEST',
  type: 'Alert',
  read: 0,
  from_user: null,
}

type Log = { name: string; read: number } & Record<string, unknown>

/** `helixhr.api.get_my_notifications`'s answer (U10): the page, and the
 * caller's unread total. */
function logs(rows: Log[], unread: number) {
  return { message: { logs: rows, unread } }
}

/** The server's own "today" (P2-U2's get_portal_bootstrap), not the test
 * runner's host clock. CI runs in UTC while this site's configured
 * timezone is Asia/Kolkata; near the UTC/IST day boundary (18:30 UTC =
 * midnight IST) the two clocks disagree about what day it is, which is
 * exactly the class of bug P2-U2 fixed in the app itself -- a first real
 * CI run hit it directly (19:07 UTC = 00:37 IST) and "Today" had nothing
 * in it. `page.request` shares the page's session, so this needs no
 * separate login. */
async function siteToday(page: Page): Promise<string> {
  const response = await page.request.get('/api/method/helixhr.api.get_portal_bootstrap')
  const body = await response.json()
  return body.message.today as string
}

/** `creation` is a naive site-timezone timestamp, exactly as Frappe stores
 * it. Pure calendar arithmetic on the anchor date string -- never parsed
 * back through `new Date(string)`, which is the exact mistake being fixed
 * here one level up. */
function stamp(anchor: string, daysAgo: number) {
  const [y, m, d] = anchor.split('-').map(Number)
  const noon = new Date(Date.UTC(y, m - 1, d, 12))
  noon.setUTCDate(noon.getUTCDate() - daysAgo)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${noon.getUTCFullYear()}-${pad(noon.getUTCMonth() + 1)}-${pad(noon.getUTCDate())} 09:30:00.000000`
}

/** Stubs the list read. `rows` is the whole set; the Unread view gets only
 * the unread ones, because the filter is the server's (U10). A function
 * lets a test change the set after a mark-read. */
async function stubNotifications(page: Page, rows: Log[] | (() => Log[]), unread: number | (() => number)) {
  const all = () => (typeof rows === 'function' ? rows() : rows)
  const total = () => (typeof unread === 'function' ? unread() : unread)
  await page.route('**/api/method/helixhr.api.get_portal_bootstrap*', async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    body.message.unread_notifications = total()
    await route.fulfill({ response, body: JSON.stringify(body) })
  })
  await page.route('**/api/method/helixhr.api.get_my_notifications*', (route) => {
    const unreadOnly = Number(route.request().postDataJSON()?.unread_only || 0)
    const subset = unreadOnly ? all().filter((row) => !row.read) : all()
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(logs(subset, total())) })
  })
  // The stubbed request does not exist, so the real resolver (plan
  // 2026-10-16-001 KTD8) would answer "none"; this employee owns it.
  await page.route('**/api/method/helixhr.api.get_request_view*', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ message: 'employee' }) }),
  )
}

function navBadge(page: Page) {
  return page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /Notifications/ })
}

test.describe('Notifications', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
  })

  test('buckets by day, and opening a reply clears it everywhere', async ({ page }) => {
    const today = await siteToday(page)
    let cleared: Record<string, string> | null = null
    await stubNotifications(
      page,
      [
        { ...LOG_UNREAD, creation: stamp(today, 0) },
        {
          name: 'NL-OLD-1',
          subject: 'Your week of 24 Aug was approved',
          description: '',
          document_type: 'Timesheet',
          document_name: 'TS-OLD',
          type: 'Alert',
          read: 1,
          creation: stamp(today, 1),
        },
      ],
      2,
    )
    await page.route('**/api/method/helixhr.api.mark_record_notifications_read*', (route) => {
      cleared = route.request().postDataJSON()
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ message: { cleared: 1, unread: 1 } }),
      })
    })

    await page.goto('/helixhr/notifications')

    await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Yesterday' })).toBeVisible()
    await expect(navBadge(page)).toHaveText(/Notifications\s*2/)

    const unreadRow = page.getByTestId('notification-row').first()
    await expect(unreadRow).toHaveAttribute('data-read', '0')
    // The reply itself, quoted on the row: the artboard's second line.
    await expect(unreadRow.getByText('“Collect it from reception.”')).toBeVisible()

    await unreadRow.click()

    // The exact record, not the Requests list.
    await expect(page).toHaveURL(/\/helixhr\/requests\/HR-REQ-2026-TEST$/)
    // ...and the count moved now, not at the next poll a minute later.
    await expect(navBadge(page)).toHaveText(/Notifications\s*1/)
    await expect
      .poll(() => cleared)
      .toEqual({ reference_doctype: 'HR Request', reference_name: 'HR-REQ-2026-TEST' })
  })

  // U10 (ED-04, EP-07): six logs about one request are one row, and opening
  // it clears all six in one call.
  test('six notifications for one request are one row that clears all six', async ({ page }) => {
    const today = await siteToday(page)
    const record = 'HR-REQ-2026-U10'
    let cleared: Record<string, string> | null = null
    const six: Log[] = Array.from({ length: 6 }, (_, i) => ({
      ...LOG_UNREAD,
      name: `NL-U10-${i}`,
      subject: i === 0 ? 'HR replied about Address proof' : `Earlier update ${i}`,
      document_name: record,
      creation: stamp(today, i === 0 ? 0 : 1),
    }))
    await stubNotifications(
      page,
      () => (cleared ? [] : six),
      () => (cleared ? 0 : 6),
    )
    await page.route('**/api/method/helixhr.api.mark_record_notifications_read*', (route) => {
      cleared = route.request().postDataJSON()
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ message: { cleared: 6, unread: 0 } }),
      })
    })

    await page.goto('/helixhr/notifications?unread=1')
    await expect(navBadge(page)).toHaveText(/Notifications\s*6/)

    const rows = page.getByTestId('notification-row')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('HR replied about Address proof')
    const toggle = page.getByTestId('notification-earlier-toggle')
    await expect(toggle).toHaveText('+5 earlier')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByTestId('notification-earlier').getByRole('listitem')).toHaveCount(5)

    await rows.first().click()
    await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${record}$`))
    await expect.poll(() => cleared).toEqual({ reference_doctype: 'HR Request', reference_name: record })
    // All six off the badge at once, not one.
    await expect(navBadge(page)).toHaveText(/^\s*Notifications\s*$/)

    await page.goBack()
    await expect(page.getByTestId('notification-row')).toHaveCount(0)
    await expect(page.getByText('Nothing unread')).toBeVisible()
  })

  test('Mark all read updates the list and the badge in one interaction', async ({ page }) => {
    const today = await siteToday(page)
    await stubNotifications(page, [{ ...LOG_UNREAD, creation: stamp(today, 0) }], 1)
    await page.route(
      '**/api/method/frappe.desk.doctype.notification_log.notification_log.mark_all_as_read*',
      (route) =>
        route.fulfill({ contentType: 'application/json', body: JSON.stringify({ message: null }) }),
    )

    await page.goto('/helixhr/notifications')
    await expect(navBadge(page)).toHaveText(/Notifications\s*1/)

    await page.getByRole('button', { name: 'Mark all read' }).click()

    await expect(page.getByTestId('notification-row').first()).toHaveAttribute('data-read', '1')
    // No badge at all, rather than a stale 1.
    await expect(navBadge(page)).toHaveText(/^\s*Notifications\s*$/)
  })

  test('Mark all read empties the Unread view', async ({ page }) => {
    const today = await siteToday(page)
    let markedAll = false
    const rows: Log[] = [
      { ...LOG_UNREAD, creation: stamp(today, 0) },
      { ...LOG_UNREAD, name: 'NL-OTHER', document_name: 'HR-REQ-2026-OTHER', creation: stamp(today, 2) },
    ]
    await stubNotifications(
      page,
      () => (markedAll ? rows.map((row) => ({ ...row, read: 1 })) : rows),
      () => (markedAll ? 0 : 2),
    )
    await page.route(
      '**/api/method/frappe.desk.doctype.notification_log.notification_log.mark_all_as_read*',
      (route) => {
        markedAll = true
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ message: null }) })
      },
    )

    await page.goto('/helixhr/notifications')
    await page.getByRole('group', { name: 'Show notifications' }).getByRole('button', { name: /Unread/ }).click()
    await expect(page).toHaveURL(/\/helixhr\/notifications\?unread=1$/)
    await expect(page.getByTestId('notification-row')).toHaveCount(2)

    await page.getByRole('button', { name: 'Mark all read' }).click()
    await expect(page.getByTestId('notification-row')).toHaveCount(0)
    await expect(page.getByText('Nothing unread')).toBeVisible()

    // A reload asks the server again and agrees.
    await page.reload()
    await expect(page.getByText('Nothing unread')).toBeVisible()
  })
})

// U10 (ED-04): the Unread filter is the server's, so a backlog larger than
// one page shows whole. Seeded for real, because the point is what the
// server returns, and removed afterwards.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const EMPLOYEE = 'employee@helixhr.test'

async function admin(baseURL: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
  return api
}

test.describe('Notifications at volume', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenario')
  })

  test('the Unread view shows all 60 unread, not a filter over the newest 50', async ({
    page,
    baseURL,
  }) => {
    const api = await admin(baseURL!)
    const tag = `U10-${Date.now()}`
    let names: string[] = []
    try {
      // Sixty different records, so record grouping cannot fold them.
      const inserted = await api.post('/api/method/frappe.client.insert_many', {
        data: {
          docs: Array.from({ length: 60 }, (_, i) => ({
            doctype: 'Notification Log',
            for_user: EMPLOYEE,
            type: 'Alert',
            subject: `${tag} update ${i}`,
            document_type: 'HR Request',
            document_name: `${tag}-${i}`,
          })),
        },
      })
      expect(inserted.ok()).toBeTruthy()
      names = (await inserted.json()).message

      await page.goto('/helixhr/notifications?unread=1')
      await expect(page.getByTestId('notification-row').filter({ hasText: tag })).toHaveCount(60)
    } finally {
      for (const name of names) {
        await api.post('/api/method/frappe.client.delete', {
          data: { doctype: 'Notification Log', name },
        })
      }
      await api.dispose()
    }
  })
})
