import { test, expect, request, Browser, APIRequestContext } from '@playwright/test'

// P2-U6. Weekly entry, the copy actions, history, and the duplicate-action
// protection -- everything the employee does *before* a manager sees the
// week. The employee/manager cycle stays in timesheet-approval.spec.ts.
//
// Every test works on a week ten weeks in the past, addressed by its Monday
// through `/timesheet/:weekStart`. Nothing here touches "this week", which
// timesheet-approval.spec.ts submits and which the dashboard specs read.

const SITE_HOST = process.env.SITE_HOST || 'test_site'
const BASE_URL = process.env.BASE_URL || 'http://localhost:8080'
const PROJECT_NAME = 'Timesheet Entry Spec Project'
const PHONE = { width: 390, height: 844 }

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10)
}

/** The Monday of the week `weeksBack` before today, in UTC throughout so the
 * host's own timezone cannot move which week the spec is about. */
function mondayWeeksBack(weeksBack: number) {
  const now = new Date()
  const date = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7) - weeksBack * 7)
  return isoDate(date)
}

const SOURCE_WEEK = mondayWeeksBack(11)
const TARGET_WEEK = mondayWeeksBack(10)

async function admin(): Promise<APIRequestContext> {
  const api = await request.newContext({
    baseURL: BASE_URL,
    extraHTTPHeaders: { Host: SITE_HOST },
  })
  await api.post('/api/method/login', { form: { usr: 'Administrator', pwd: 'admin' } })
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

/** A Project this employee may book time on, plus a clean slate for the two
 * weeks the spec writes to -- a leftover draft from a previous run would
 * otherwise make "copy into an empty week" untestable. */
async function setUp() {
  const api = await admin()

  let projectName = await getValue(api, 'Project', { project_name: PROJECT_NAME }, 'name')
  if (!projectName) {
    const company = await getValue(
      api,
      'Employee',
      { user_id: 'employee@helixhr.test' },
      'company',
    )
    const created = await api.post('/api/method/frappe.client.insert', {
      data: {
        doc: JSON.stringify({
          doctype: 'Project',
          project_name: PROJECT_NAME,
          status: 'Open',
          company,
        }),
      },
    })
    projectName = (await created.json()).message.name
  }

  const permission = await getValue(
    api,
    'User Permission',
    { user: 'employee@helixhr.test', allow: 'Project', for_value: projectName },
    'name',
  )
  if (!permission) {
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
  await clearSpecWeeks()
}

/**
 * The two weeks this spec owns, emptied. Run before the first test so
 * "copy into an empty week" is testable, and again after the last one so
 * the week this spec sends does not sit in the manager's queue while
 * timesheet-approval.spec.ts is counting it.
 */
async function clearSpecWeeks() {
  const api = await admin()
  const employee = await getValue(api, 'Employee', { user_id: 'employee@helixhr.test' }, 'name')
  const existing = await api.get(
    '/api/method/frappe.client.get_list?doctype=Timesheet&filters=' +
      encodeURIComponent(
        JSON.stringify({
          employee,
          start_date: ['between', [SOURCE_WEEK, addDays(TARGET_WEEK, 6)]],
        }),
      ) +
      '&fields=' +
      encodeURIComponent(JSON.stringify(['name', 'docstatus'])) +
      '&limit_page_length=0',
  )
  for (const row of (await existing.json())?.message || []) {
    if (row.docstatus === 1) {
      await api.post('/api/method/frappe.client.cancel', {
        form: { doctype: 'Timesheet', name: row.name },
      })
    }
    await api.post('/api/method/frappe.client.delete', {
      form: { doctype: 'Timesheet', name: row.name },
    })
  }
  await api.dispose()
}

function addDays(iso: string, days: number) {
  const date = new Date(`${iso}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return isoDate(date)
}

async function phone(browser: Browser) {
  const context = await browser.newContext({
    storageState: 'tests/.auth/employee.json',
    viewport: PHONE,
    hasTouch: true,
    isMobile: true,
  })
  return context
}

// Serial: the second test opens the week the first one filed. One employee
// has one timesheet per week (KTD10), so these cannot be independent without
// each one owning its own week and re-seeding it.
test.describe.serial('timesheet entry', () => {
  // These drive the employee identity only; running them again as the
  // manager project would re-enter the same week as somebody who does not
  // own it.
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'runs once, as the employee')
  })

  test.afterAll(async () => {
    await clearSpecWeeks()
  })

  test('a week is entered day by day on a phone, saved, and copied forward', async ({
    browser,
  }: {
    browser: Browser
  }) => {
    test.setTimeout(90000)
    await setUp()

    const context = await phone(browser)
    const page = await context.newPage()

    // --- the source week: one line on its Monday -------------------------
    await page.goto(`/helixhr/timesheet/${SOURCE_WEEK}`)
    await expect(page.getByRole('heading', { name: 'Timesheet' })).toBeVisible()
    // The day picker opens on the week's Monday when today is elsewhere.
    await expect(page.getByRole('tab', { selected: true })).toHaveAttribute(
      'aria-label',
      /^Mon /,
    )

    // Scoped to the phone layout: both layouts are in the DOM at every
    // width, and only one of them is displayed.
    const dayList = page.getByTestId('week-days')
    await dayList.getByRole('button', { name: /Add time to Mon/ }).click()
    await dayList.getByLabel(/^Project for row/).selectOption({ label: PROJECT_NAME })
    const hours = dayList.getByLabel(new RegExp(`^Hours on ${PROJECT_NAME}$`))
    await hours.fill('4')
    await hours.blur()

    // Per-day and weekly totals, and the 0.25 stepper.
    await expect(page.getByText('4 of 40 hours this week')).toBeVisible()
    await dayList.getByRole('button', { name: `More time on ${PROJECT_NAME}` }).click()
    await expect(hours).toHaveValue('4.25')

    await expect(page.getByText('Unsaved changes')).toBeVisible()
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Saved just now')).toBeVisible({ timeout: 10000 })

    // --- the target week: copy last week --------------------------------
    await page.goto(`/helixhr/timesheet/${TARGET_WEEK}`)
    await expect(page.getByText('0 of 40 hours this week')).toBeVisible()

    await page.getByRole('button', { name: 'Copy last week' }).click()
    await expect(page.getByText('4.25 of 40 hours this week')).toBeVisible({ timeout: 10000 })

    // A second copy would overwrite rows that are already there, so it asks.
    await page.getByRole('button', { name: 'Copy last week' }).click()
    const confirm = page.getByRole('dialog')
    await expect(confirm.getByText('Replace this week?')).toBeVisible()
    await confirm.getByRole('button', { name: "Keep what's here" }).click()
    await expect(page.getByText('4.25 of 40 hours this week')).toBeVisible()

    // --- send it, twice ---------------------------------------------------
    // A double tap must produce one transition, not two: the button is
    // disabled while in flight and `submit_my_week` refuses the second
    // request's stale `modified` behind it (P2-U6 scenario 7).
    await page.getByRole('button', { name: 'Submit week' }).dblclick()
    await expect(page.locator('[data-status="Pending Approval"]:visible')).toBeVisible({ timeout: 15000 })

    const api = await admin()
    const employee = await getValue(api, 'Employee', { user_id: 'employee@helixhr.test' }, 'name')
    const sheets = await api.get(
      '/api/method/frappe.client.get_list?doctype=Timesheet&filters=' +
        encodeURIComponent(
          JSON.stringify({ employee, start_date: TARGET_WEEK, docstatus: ['!=', 2] }),
        ) +
        '&fields=' +
        encodeURIComponent(JSON.stringify(['name', 'workflow_state'])) +
        '&limit_page_length=0',
    )
    const rows = (await sheets.json())?.message || []
    expect(rows).toHaveLength(1)
    expect(rows[0].workflow_state).toBe('Pending Approval')
    await api.dispose()

    // A week that is waiting on somebody else is read-only.
    await expect(page.getByRole('button', { name: 'Submit week' })).toHaveCount(0)

    // --- the source week is untouched by the copy ------------------------
    await page.goto(`/helixhr/timesheet/${SOURCE_WEEK}`)
    await expect(page.getByText('4.25 of 40 hours this week')).toBeVisible()
    await expect(page.locator('[data-status="Pending Approval"]:visible')).toHaveCount(0)

    await context.close()
  })

  test('past weeks opens the exact week it names', async ({ browser }: { browser: Browser }) => {
    test.setTimeout(60000)
    const context = await phone(browser)
    const page = await context.newPage()

    await page.goto('/helixhr/timesheet/history')
    await expect(page.getByRole('heading', { name: 'Past weeks' })).toBeVisible()

    // The row for the week the first test filed, by its own hours.
    const row = page.locator(`a[href$="/timesheet/${SOURCE_WEEK}"]`).first()
    await expect(row).toBeVisible({ timeout: 10000 })
    await row.click()

    await expect(page).toHaveURL(new RegExp(`/timesheet/${SOURCE_WEEK}$`))
    await expect(page.getByText('4.25 of 40 hours this week')).toBeVisible({ timeout: 10000 })

    // Refresh keeps the same week (P2-R12).
    await page.reload()
    await expect(page.getByText('4.25 of 40 hours this week')).toBeVisible({ timeout: 10000 })

    await context.close()
  })

  test('India and the US see the same Monday-Sunday week', async ({
    browser,
  }: {
    browser: Browser
  }) => {
    test.setTimeout(60000)
    // P2-AE3, P2-U6 scenario 6. The week is the *user's* calendar week,
    // resolved from the server's bootstrap timezone -- the browser's own
    // zone is never an input. This page computed its week with
    // `new Date('YYYY-MM-DD').getDay()` until P2-U6, which put an employee
    // west of Greenwich on the previous week.
    const labels: string[] = []
    for (const timezoneId of ['Asia/Kolkata', 'America/Los_Angeles']) {
      const context = await browser.newContext({
        storageState: 'tests/.auth/employee.json',
        viewport: PHONE,
        timezoneId,
      })
      const page = await context.newPage()
      await page.goto('/helixhr/timesheet')
      const days = page.getByRole('tab')
      await expect(days).toHaveCount(7)
      labels.push(
        (await days.allTextContents()).join('|') +
          '::' +
          (await page.getByRole('tab').first().getAttribute('aria-label')),
      )
      await context.close()
    }
    expect(labels[0]).toBe(labels[1])
  })
})
