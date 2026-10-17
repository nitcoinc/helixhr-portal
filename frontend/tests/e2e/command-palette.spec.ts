import { test, expect, request, APIRequestContext, Browser, Page } from '@playwright/test'

// Plan 2026-10-17-001 U5 (R7, KTD2, KTD13). The command palette: ⌘K / Ctrl-K
// or "/" on a desktop, a search button on a phone. Pages and actions come
// from the rail's own gates (lib/nav.js); people and requests from one
// rate-limited `portal_search` read; reports from the catalog.
//
// In every project's testMatch: each test gates on the hat it is written for.
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const BASE_URL = process.env.BASE_URL || 'http://localhost:8080'
const COLLEAGUE = 'Directory Colleague'
const SEARCH_METHOD = '**/api/method/helixhr.api.portal_search*'

// Every destination lib/nav.js knows, so "exactly the rail's pages" can be
// checked in both directions.
const ALL_PAGES = [
  'Home',
  'Notifications',
  'Leave',
  'Timesheet',
  'My projects',
  'Attendance',
  'Requests',
  'Payslips',
  'Holidays',
  'Documents',
  'Directory',
  'Team',
  'Roster',
  'Approvals',
  'People',
  'Reports',
  'Organisation',
  'Projects',
  'Settings',
  'Email templates',
  'Profile',
]

const palette = (page: Page) => page.getByRole('dialog', { name: 'Search' })
const paletteInput = (page: Page) => page.getByTestId('palette-input')
const group = (page: Page, id: string) => page.getByTestId(`palette-group-${id}`)

async function openWithShortcut(page: Page) {
  await page.keyboard.press('ControlOrMeta+k')
  await expect(palette(page)).toBeVisible()
}

async function login(user: string): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: { Host: SITE_HOST } })
  const response = await api.post('/api/method/login', { form: { usr: user, pwd: PASSWORD } })
  expect(response.ok(), `login ${user}`).toBeTruthy()
  return api
}

async function fileRequest(user: string, subject: string) {
  const api = await login(user)
  const response = await api.post('/api/method/helixhr.api.create_my_request', {
    form: {
      category: 'HR Letter',
      subject,
      details: 'Filed by command-palette.spec.ts.',
      operation_key: crypto.randomUUID(),
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const name = (await response.json()).message.name as string
  await api.dispose()
  return name
}

/** A page in a fresh context, signed in as `user` with a session of its own,
 * so signing it out never touches the shared storage-state session. */
async function signedIn(browser: Browser, user: string) {
  const api = await login(user)
  const state = await api.storageState()
  await api.dispose()
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } })
  await context.addCookies(state.cookies)
  return { context, page: await context.newPage() }
}

/** The rail's labels, collapsed groups included (they are only hidden). */
async function railLabels(page: Page) {
  const texts = await page.locator('aside nav[aria-label="Main"] a span.flex-1').allTextContents()
  return texts.map((text) => text.trim())
}

/** Which of ALL_PAGES the palette offers, asked one label at a time. */
async function palettePages(page: Page) {
  // People and requests are not what this asks; keep the server out of it.
  await page.route(SEARCH_METHOD, (route) =>
    route.fulfill({ json: { message: { people: [], requests: [], inbox: [], failed: [] } } }),
  )
  const offered: string[] = []
  for (const label of ALL_PAGES) {
    await paletteInput(page).fill(label)
    const option = group(page, 'pages').getByRole('option', { name: label, exact: true })
    await expect(paletteInput(page)).toHaveValue(label)
    if (await option.count()) offered.push(label)
  }
  return offered
}

test.describe('employee, desktop', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'desktop employee scenarios')
    await page.setViewportSize({ width: 1440, height: 900 })
  })

  test('Ctrl-K, "pay", Enter lands on Payslips with focus in the input first', async ({ page }) => {
    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await openWithShortcut(page)
    await expect(paletteInput(page)).toBeFocused()
    await paletteInput(page).fill('pay')
    await expect(group(page, 'pages').getByRole('option', { name: 'Payslips' })).toHaveAttribute(
      'data-highlighted',
      '',
    )
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/helixhr\/payslips$/)
    await expect(palette(page)).toHaveCount(0)
  })

  test("a colleague's name opens the directory filtered to them", async ({ page }) => {
    await page.goto('/helixhr/')
    await openWithShortcut(page)
    await paletteInput(page).fill(COLLEAGUE)
    const person = group(page, 'people').getByRole('option', { name: new RegExp(COLLEAGUE) })
    await expect(person).toBeVisible()
    await person.click()
    await expect(page).toHaveURL(/\/helixhr\/directory\?q=Directory(%20|\+)Colleague$/)
    // The palette (itself named "Search") has left before the page's own
    // Search field is read.
    await expect(palette(page)).toHaveCount(0)
    await expect(page.getByLabel('Search')).toHaveValue(COLLEAGUE)
    await expect(page.getByText(COLLEAGUE, { exact: true }).first()).toBeVisible()
  })

  test('one character matches pages only and asks the server nothing', async ({ page }) => {
    const searched: string[] = []
    page.on('request', (sent) => {
      if (sent.url().includes('helixhr.api.portal_search')) searched.push(sent.url())
    })
    await page.goto('/helixhr/')
    await openWithShortcut(page)
    await paletteInput(page).fill('p')
    await expect(group(page, 'pages').getByRole('option', { name: 'Payslips' })).toBeVisible()
    await page.waitForTimeout(600)
    expect(searched).toEqual([])
  })

  test('"/" typed in a text field stays in the field', async ({ page }) => {
    await page.goto('/helixhr/directory')
    const field = page.getByLabel('Search')
    await field.click()
    await page.keyboard.type('a/b')
    await expect(field).toHaveValue('a/b')
    await expect(palette(page)).toHaveCount(0)
    // Outside a field, "/" opens it.
    await page.getByRole('heading', { level: 1 }).click()
    await page.keyboard.press('/')
    await expect(palette(page)).toBeVisible()
  })

  test('Ctrl-K while a Leave sheet is open does nothing', async ({ page }) => {
    await page.goto('/helixhr/leave')
    await page.getByRole('button', { name: 'Ask for leave' }).first().click()
    await expect(page.getByRole('dialog', { name: 'Ask for leave' })).toBeVisible()
    await page.keyboard.press('ControlOrMeta+k')
    await page.waitForTimeout(300)
    await expect(palette(page)).toHaveCount(0)
    await expect(page.getByRole('dialog', { name: 'Ask for leave' })).toBeVisible()
  })

  test('opens with no recents and no error when storage throws', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.addInitScript(() => {
      const fail = () => {
        throw new DOMException('blocked', 'SecurityError')
      }
      Storage.prototype.getItem = fail
      Storage.prototype.setItem = fail
    })
    await page.goto('/helixhr/')
    await openWithShortcut(page)
    await expect(group(page, 'suggested')).toBeVisible()
    await expect(group(page, 'recent')).toHaveCount(0)
    await paletteInput(page).fill('pay')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/helixhr\/payslips$/)
    expect(errors).toEqual([])
  })

  test('a failed group is a quiet line beside the page matches, never "No results"', async ({ page }) => {
    await page.route(SEARCH_METHOD, (route) =>
      route.fulfill({ json: { message: { people: [], requests: [], inbox: [], failed: ['people'] } } }),
    )
    await page.goto('/helixhr/')
    await openWithShortcut(page)
    const status = page.getByTestId('palette-status')
    await paletteInput(page).fill('pay')
    await expect(status).toHaveText("Couldn't search people and requests")
    await expect(group(page, 'pages').getByRole('option', { name: 'Payslips' })).toBeVisible()
    await paletteInput(page).fill('zzqx-nothing')
    await expect(status).toHaveText("Couldn't search people and requests")
    await expect(palette(page)).not.toContainText('No results')
  })

  test('Escape closes it and gives focus back to the trigger', async ({ page }) => {
    await page.goto('/helixhr/')
    const trigger = page.getByTestId('palette-trigger')
    await trigger.click()
    await expect(paletteInput(page)).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(palette(page)).toHaveCount(0)
    await expect(trigger).toBeFocused()
  })

  test("Ask for leave opens the form once, and closing it drops ?ask from the URL", async ({ page }) => {
    await page.goto('/helixhr/')
    await openWithShortcut(page)
    await group(page, 'suggested').getByRole('option', { name: 'Ask for leave' }).click()
    await expect(page).toHaveURL(/\/helixhr\/leave\?ask=1$/)
    const form = page.getByRole('dialog', { name: 'Ask for leave' })
    await expect(form).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(form).toHaveCount(0)
    await expect(page).toHaveURL(/\/helixhr\/leave$/)
    // Back goes to Home, not to a reopened form.
    await page.goBack()
    await expect(page).toHaveURL(/\/helixhr\/?$/)
  })
})

test.describe('phone', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee phone scenarios')
    await page.setViewportSize({ width: 390, height: 844 })
  })

  test('the app-bar trigger is 44px, focuses the input, and results stay in view', async ({ page }) => {
    await page.goto('/helixhr/')
    const trigger = page.getByTestId('palette-trigger-phone')
    const box = await trigger.boundingBox()
    expect(box?.width).toBeGreaterThanOrEqual(44)
    expect(box?.height).toBeGreaterThanOrEqual(44)
    await trigger.click()
    await expect(paletteInput(page)).toBeFocused()
    await paletteInput(page).fill('pay')
    const option = group(page, 'pages').getByRole('option', { name: 'Payslips' })
    await expect(option).toBeInViewport()
    // KTD2: pinned to the top, not a bottom sheet behind the keyboard.
    const panel = await palette(page).boundingBox()
    expect(panel?.y).toBeLessThan(100)
  })

  test('the More row closes More, opens search, and Escape returns to More', async ({ page }) => {
    await page.goto('/helixhr/')
    const more = page.getByRole('navigation', { name: 'Main' }).getByRole('button', { name: 'More' })
    await more.click()
    await page.getByTestId('more-search').click()
    await expect(palette(page)).toBeVisible()
    await expect(page.getByRole('dialog', { name: 'More' })).toHaveCount(0)
    await expect(paletteInput(page)).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(palette(page)).toHaveCount(0)
    await expect(more).toBeFocused()
  })
})

test.describe('hr', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'HR scenarios')
    await page.setViewportSize({ width: 1440, height: 900 })
  })

  test('a person opens People, an inbox subject the handler view, an own request the employee view', async ({
    page,
  }) => {
    test.setTimeout(60000)
    const stamp = Date.now()
    const theirs = await fileRequest('employee@helixhr.test', `Palette inbox ${stamp}`)
    const mine = await fileRequest('hr-manager-employee@helixhr.test', `Palette own ${stamp}`)

    await page.goto('/helixhr/')
    await openWithShortcut(page)
    await paletteInput(page).fill(COLLEAGUE)
    await group(page, 'people').getByRole('option', { name: new RegExp(COLLEAGUE) }).click()
    await expect(page).toHaveURL(/\/helixhr\/people\/[^/?]+$/)
    await expect(page.getByRole('heading', { name: COLLEAGUE })).toBeVisible()

    await openWithShortcut(page)
    await paletteInput(page).fill(`Palette inbox ${stamp}`)
    await group(page, 'inbox').getByRole('option', { name: new RegExp(`Palette inbox ${stamp}`) }).click()
    await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${theirs}\\?tab=work$`))

    await openWithShortcut(page)
    await paletteInput(page).fill(`Palette own ${stamp}`)
    await expect(group(page, 'inbox')).toHaveCount(0)
    await group(page, 'requests').getByRole('option', { name: new RegExp(`Palette own ${stamp}`) }).click()
    await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${mine}$`))
  })

  test('a desk-only HR session is offered only its desk pages, People and Reports', async ({ browser }) => {
    test.setTimeout(60000)
    const { context, page } = await signedIn(browser, 'hr-manager@helixhr.test')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    const rail = await railLabels(page)
    await openWithShortcut(page)
    await expect(group(page, 'suggested')).toHaveCount(0)
    const offered = await palettePages(page)
    expect(offered).toEqual(ALL_PAGES.filter((label) => rail.includes(label)))
    expect(offered).toEqual(expect.arrayContaining(['People', 'Reports']))
    expect(offered).not.toContain('Leave')
    await paletteInput(page).fill('ask')
    await expect(page.getByRole('option', { name: 'Ask for leave' })).toHaveCount(0)
    await context.close()
  })
})

test.describe('gates', () => {
  test('each identity is offered exactly the pages its rail shows', async ({ page }, testInfo) => {
    test.skip(
      !['employee', 'manager', 'hr', 'it', 'portal-admin', 'hr-user'].includes(testInfo.project.name),
      'desktop identities',
    )
    test.setTimeout(60000)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    const rail = await railLabels(page)
    await openWithShortcut(page)
    expect(await palettePages(page)).toEqual(ALL_PAGES.filter((label) => rail.includes(label)))
    if (testInfo.project.name === 'employee') {
      for (const hidden of ['People', 'Settings', 'Organisation', 'Reports']) expect(rail).not.toContain(hidden)
    }
    // Every identity here has an Employee, so Ask for leave is offered.
    await paletteInput(page).fill('')
    await expect(group(page, 'suggested').getByRole('option', { name: 'Ask for leave' })).toBeVisible()
  })
})

test.describe('recents', () => {
  test("user B on the same browser sees none of user A's recents", async ({ browser }, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'run once')
    test.setTimeout(90000)
    const subject = `Palette recent ${Date.now()}`
    const filed = await fileRequest('employee@helixhr.test', subject)
    const { context, page } = await signedIn(browser, 'employee@helixhr.test')
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr/')

    await openWithShortcut(page)
    await paletteInput(page).fill(COLLEAGUE)
    await group(page, 'people').getByRole('option', { name: new RegExp(COLLEAGUE) }).click()
    await expect(page).toHaveURL(/\/helixhr\/directory\?q=/)
    await openWithShortcut(page)
    await paletteInput(page).fill(subject)
    await group(page, 'requests').getByRole('option', { name: new RegExp(subject) }).click()
    await expect(page).toHaveURL(new RegExp(`/helixhr/requests/${filed}$`))

    await openWithShortcut(page)
    await expect(group(page, 'recent').getByRole('option')).toHaveCount(2)
    await page.keyboard.press('Escape')

    await page.locator('aside').getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL(/\/login/)
    const left = await page.evaluate(() => Object.keys(localStorage).filter((key) => key.startsWith('helixhr.palette.recent:')))
    expect(left).toEqual([])

    const other = await login('manager@helixhr.test')
    await context.addCookies((await other.storageState()).cookies)
    await other.dispose()
    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await openWithShortcut(page)
    await expect(group(page, 'recent')).toHaveCount(0)
    await expect(palette(page)).not.toContainText(COLLEAGUE)
    await expect(palette(page)).not.toContainText(subject)
    await context.close()
  })
})
