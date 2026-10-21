import { test, expect, request, type APIRequestContext, type Page } from '@playwright/test'

// Plan 2026-10-19-001 U7: People & access, elevated first. Fixtures come
// from `helixhr.tests.utils.ensure_people_access_fixtures` (part of
// `setup_playwright_fixtures`, and re-run before this file so a rerun starts
// from the same state): a Frappe HR Manager with no assignment, a Manage
// access holder on All orgs, three targets with no elevated access, and an
// employee with no login. The Portal Admin fixture holds Portal Admin on
// `_Test Company` (its own) and `_Test Company B` (`ensure_company_access_fixtures`).
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'admin'
const COMPANY = '_Test Company'
const COMPANY_B = '_Test Company B'
const PORTAL_ADMIN = 'portal-admin@helixhr.test'
const DRIFT = 'access-drift@helixhr.test'
const ACCESS_ADMIN = 'access-admin@helixhr.test'
const LEADER = 'access-leader@helixhr.test'
const DM_TARGET = 'access-dm-target@helixhr.test'
const STALE_TARGET = 'access-stale-target@helixhr.test'
const HR_MANAGER = 'hr-manager-employee@helixhr.test'
const FOREIGN = 'grant-e2e-foreign@helixhr.test'
const LIST = '/helixhr/settings/people-access'

async function apiAs(baseURL: string | undefined, user: string, pwd = PASSWORD): Promise<APIRequestContext> {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Host: SITE_HOST } })
  const login = await api.post('/api/method/login', { form: { usr: user, pwd } })
  expect(login.ok(), `login as ${user}`).toBeTruthy()
  return api
}

async function signInAs(page: Page, baseURL: string | undefined, user: string, pwd = PASSWORD) {
  const api = await apiAs(baseURL, user, pwd)
  const state = await api.storageState()
  await api.dispose()
  await page.context().clearCookies()
  await page.context().addCookies(state.cookies)
}

/** The visible control that opens ``user`` (a table row, a card or an
 * All employees row). */
function opener(page: Page, user: string) {
  return page.locator(`[data-person-open="${user}"]:visible`)
}

async function findEmployee(page: Page, query: string, user: string) {
  await page.getByTestId('employees-search').fill(query)
  await page.getByTestId(`employee-open-${user}`).click()
  await expect(page.getByTestId('person-access-editor').getByRole('heading', { level: 2 })).toBeVisible()
}

test.describe('People & access', () => {
  test.describe.configure({ mode: 'serial' })

  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'portal-admin', 'one run: each test signs in as the hat it needs')
  })

  test.beforeAll(async ({ baseURL }, testInfo) => {
    if (testInfo.project.name !== 'portal-admin') return
    const admin = await apiAs(baseURL, 'Administrator', ADMIN_PASSWORD)
    const seeded = await admin.post('/api/method/helixhr.tests.utils.seed_people_access_fixtures')
    expect(seeded.ok(), 'seed the People & access fixtures').toBeTruthy()
    await admin.dispose()
  })

  test('a System Manager follows the banner and removes a role set outside HelixHR', async ({ page, baseURL }) => {
    await signInAs(page, baseURL, 'Administrator', ADMIN_PASSWORD)
    await page.goto(LIST)

    const banner = page.getByTestId('access-banner')
    await expect(banner).toHaveText(/\d+ (person needs|people need) attention/)
    await banner.click()
    await expect(page.getByTestId('elevated-filter-attention')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByTestId('elevated-region')).toBeFocused()

    const row = page.getByTestId(`elevated-row-${DRIFT}`)
    await expect(row).toContainText('Role set outside HelixHR')
    await expect(row).toContainText('HR Manager (no assignment)')
    await page.getByTestId(`elevated-open-${DRIFT}`).click()
    await expect(page).toHaveURL(/\/settings\/people-access\/access-drift/)

    const editor = page.getByTestId('person-access-editor')
    await expect(editor.getByTestId('access-role-HR Manager')).toContainText('Set outside HelixHR')
    await editor.getByRole('button', { name: 'Remove role: HR Manager' }).click()
    await editor.getByTestId('access-review').click()

    const dialog = page.getByTestId('access-review-dialog')
    await expect(dialog.getByTestId('access-review-losses')).toContainText('HR Manager, set outside HelixHR')
    await expect(dialog.getByTestId('access-high-risk')).toHaveCount(0)
    await dialog.getByTestId('access-confirm').click()
    await expect(page.getByTestId('toast').filter({ hasText: 'Access saved for' })).toBeVisible()
    // Nothing elevated is left, so they leave the Elevated table.
    await expect(
      page.locator(`[data-testid="elevated-card-${DRIFT}"], [data-testid="elevated-row-${DRIFT}"]`),
    ).toHaveCount(0)
  })

  test('the reported bug: a Portal Admin over two orgs sees the other org\'s employees', async ({ page }) => {
    await page.goto(LIST)
    await page.getByTestId('employees-org').selectOption(COMPANY_B)
    await expect(page.getByTestId(`employee-open-${FOREIGN}`)).toBeVisible()
    await page.getByTestId('employees-search').fill('grant-e2e-foreign')
    await expect(page.getByTestId('employees-list')).toContainText(COMPANY_B)
  })

  test('Manage access applies Leadership: high risk, a reason, then HR Manager on All orgs', async ({ page, baseURL }) => {
    await signInAs(page, baseURL, ACCESS_ADMIN)
    await page.goto(LIST)
    await findEmployee(page, 'access-leader', LEADER)

    const editor = page.getByTestId('person-access-editor')
    await editor.getByTestId('access-apply-leadership').click()
    await expect(editor.getByTestId('access-role-HR Manager')).toContainText('All orgs')
    await editor.getByTestId('access-review').click()

    const dialog = page.getByTestId('access-review-dialog')
    await expect(dialog.getByTestId('access-review-gains')).toContainText('HR Manager · All orgs')
    await expect(dialog.getByTestId('access-high-risk')).toHaveText('High risk')
    const confirm = dialog.getByTestId('access-confirm')
    await expect(confirm).toBeDisabled()
    await dialog.getByTestId('access-reason').fill('Leads HR for every org <b>from Monday</b>')
    await expect(confirm).toBeEnabled()
    await confirm.click()

    await expect(page.getByTestId('toast').filter({ hasText: 'Access saved for' })).toBeVisible()
    await expect(editor.getByTestId('person-access-summary')).toContainText(
      'Acts as HR Manager in all orgs, including new ones.',
    )
    // The reason is shown as text, never as markup.
    await editor.getByTestId('access-history').locator('summary').click()
    await expect(editor.getByTestId('access-history')).toContainText('<b>from Monday</b>')
    await expect(page.getByTestId(`elevated-card-${LEADER}`)).toContainText(/HR Manager · All orgs/)
  })

  test('a Portal Admin gives Delivery Manager in its own org without a reason; an HR Manager is read-only', async ({
    page,
  }) => {
    await page.goto(LIST)
    await findEmployee(page, 'access-dm-target', DM_TARGET)

    const editor = page.getByTestId('person-access-editor')
    const roles = editor.getByTestId('access-add-role-select')
    // Only the roles a Portal Admin may give (R12).
    await expect(roles.locator('option', { hasText: 'HR Manager' })).toHaveCount(0)
    await roles.selectOption({ label: 'Delivery Manager' })
    await expect(editor).toContainText(`Starts in ${COMPANY}, their own org.`)
    await editor.getByTestId('access-add-role-submit').click()
    await expect(editor.getByRole('button', { name: `Remove ${COMPANY} from Delivery Manager` })).toBeVisible()

    // The review traps focus and closes on Escape, keeping the draft.
    await editor.getByTestId('access-review').click()
    const dialog = page.getByTestId('access-review-dialog')
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(editor.getByTestId('access-role-HelixHR Delivery Manager')).toBeVisible()

    await editor.getByTestId('access-review').click()
    await expect(dialog.getByTestId('access-review-gains')).toContainText(`Delivery Manager · ${COMPANY}`)
    await expect(dialog.getByTestId('access-high-risk')).toHaveCount(0)
    await expect(dialog.getByTestId('access-confirm')).toBeEnabled()
    await dialog.getByTestId('access-confirm').click()
    await expect(page.getByTestId('toast').filter({ hasText: 'Access saved for' })).toBeVisible()
    await expect(editor.getByTestId('person-access-summary')).toContainText(`Delivery Manager in ${COMPANY}.`)

    await opener(page, HR_MANAGER).click()
    await expect(page).toHaveURL(/people-access\/hr-manager-employee/)
    await expect(page.getByTestId('person-access-lock')).toHaveText(
      "You can't change this person's access: they hold a role you can't assign.",
    )
    await expect(page.getByTestId('access-add-role')).toHaveCount(0)
    await expect(page.getByTestId('access-review-bar')).toHaveCount(0)
  })

  test('an employee without a login is listed, marked, and offers no editor', async ({ page }) => {
    await page.goto(LIST)
    await page.getByTestId('employees-search').fill('Nologin')
    const row = page.getByTestId('employees-list').locator('li', { hasText: 'Nologin' })
    await expect(row).toContainText('No login')
    await expect(row.getByRole('button')).toHaveCount(0)
    await expect(page.getByTestId('access-add-role')).toHaveCount(0)
  })

  test('your own access is read-only', async ({ page }) => {
    await page.goto(LIST)
    await findEmployee(page, 'portal-admin@', PORTAL_ADMIN)
    await expect(page.getByTestId('person-access-lock')).toHaveText("You can't change your own access.")
    await expect(page.getByTestId('access-add-role')).toHaveCount(0)
  })

  test('a stale save keeps the draft and offers Reload; leaving a dirty draft asks first', async ({ page, baseURL }) => {
    await page.goto(LIST)
    await findEmployee(page, 'access-stale-target', STALE_TARGET)
    const editor = page.getByTestId('person-access-editor')
    await editor.getByTestId('access-add-role-select').selectOption({ label: 'Delivery Manager' })
    await editor.getByTestId('access-add-role-submit').click()

    // Someone else changes this person meanwhile.
    const admin = await apiAs(baseURL, 'Administrator', ADMIN_PASSWORD)
    const current = await admin.post('/api/method/helixhr.api.get_person_access', { data: { user: STALE_TARGET } })
    const { token } = (await current.json()).message
    const saved = await admin.post('/api/method/helixhr.api.save_person_access', {
      data: { user: STALE_TARGET, token, rows: [{ role: 'IT Team', all_orgs: 0, company: COMPANY }] },
    })
    expect(saved.ok(), 'the other save lands').toBeTruthy()
    await admin.dispose()

    await editor.getByTestId('access-review').click()
    const dialog = page.getByTestId('access-review-dialog')
    await dialog.getByTestId('access-confirm').click()
    await expect(dialog.getByTestId('access-stale')).toContainText(
      "Someone changed this person's access. Reload and re-apply.",
    )
    await expect(dialog.getByTestId('access-reload')).toBeVisible()
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(editor.getByTestId('access-role-HelixHR Delivery Manager')).toBeVisible()

    // Back with a dirty draft asks before discarding it.
    await page.goBack()
    const discard = page.getByTestId('access-discard-dialog')
    await expect(discard).toBeVisible()
    await page.getByRole('button', { name: 'Keep editing' }).click()
    await expect(page).toHaveURL(/people-access\/access-stale-target/)
    await expect(editor.getByTestId('access-role-HelixHR Delivery Manager')).toBeVisible()

    await page.getByTestId('person-access-close').click()
    await expect(discard).toBeVisible()
    await page.getByTestId('access-discard-confirm').click()
    await expect(page).toHaveURL(/\/settings\/people-access$/)
    // Focus returns to the row that opened the person.
    await expect(page.getByTestId(`employee-open-${STALE_TARGET}`)).toBeFocused()
  })

  test('on a phone, elevated rows are cards, the editor is a pushed view, and nothing scrolls sideways', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(LIST)
    await expect(page.getByTestId('elevated-cards')).toBeVisible()
    await expect(page.getByTestId('elevated-table')).toBeHidden()
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(await overflow()).toBeLessThanOrEqual(0)

    await findEmployee(page, 'access-dm-target', DM_TARGET)
    await expect(page.getByTestId('people-access-lists')).toBeHidden()
    expect(await overflow()).toBeLessThanOrEqual(0)
    await page.getByRole('button', { name: 'Back to People & access' }).click()
    await expect(page.getByTestId('people-access-lists')).toBeVisible()
  })
})

test('an HR Manager without Portal Admin has no People & access tab', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'hr', 'the refused hat is HR')

  await page.goto('/helixhr/settings')
  await expect(page.getByTestId('settings-tab-categories')).toBeVisible()
  await expect(page.getByTestId('settings-tab-people-access')).toHaveCount(0)
})
