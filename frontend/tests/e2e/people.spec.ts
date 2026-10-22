import { test, expect } from '@playwright/test'

// P6-U5 / P6-R1, P6-R2, P6-R13. Find a person, then read them -- HR-only,
// the server's own gate (`resolve_admin_scope`), the same posture
// organisation.spec.ts and settings.spec.ts already take.
//
// `setup_playwright_fixtures` seeds a colleague and a manager in the HR
// fixture's own company (`make_test_employee_and_manager`, reused by
// `ensure_directory_fixtures`) -- "Manager" is the name this suite searches
// for, the same fixture directory.spec.ts already opens.
const COLLEAGUE_NAME = 'Manager'

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'people is an HR-only screen')
  })

  test('an HR identity searches, opens a colleague, and the URL survives a reload', async ({ page }) => {
    await page.goto('/helixhr/')
    // U15: the HR section starts collapsed on a first visit.
    const hrSection = page.getByRole('button', { name: 'HR', exact: true })
    if ((await hrSection.getAttribute('aria-expanded')) === 'false') await hrSection.click()
    await expect(page.getByRole('link', { name: 'People' })).toBeVisible()

    await page.goto('/helixhr/people')
    await expect(page.getByRole('heading', { name: 'People', exact: true })).toBeVisible()

    const search = page.getByLabel('Search')
    await search.fill(COLLEAGUE_NAME)
    const row = page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first()
    await expect(row).toBeVisible()
    await row.click()

    await expect(page).toHaveURL(/\/helixhr\/people\/[^/]+$/)
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()
    await expect(page.getByText('Leave balance')).toBeVisible()
    await expect(page.getByText('Attendance this month')).toBeVisible()
    await expect(page.getByText('Open and recent requests')).toBeVisible()

    // A reload lands on the same person (P2-R12) -- not a client-side-only
    // route, and not thrown back to the search.
    await page.reload()
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()
  })

  test('P8-U8: editing overview persists and reflects without a full reload', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill(COLLEAGUE_NAME)
    await page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()

    const email = `manager-e2e-${Date.now()}@helixhr.test`
    await page.getByTestId('person-edit-overview').click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('heading', { name: 'Edit overview' })).toBeVisible()
    const original = await page.getByLabel('Work email').inputValue()
    await page.getByLabel('Work email').fill(email)
    await page.getByRole('button', { name: 'Save' }).click()

    await expect(dialog).toBeHidden()
    await expect(page.getByText(email)).toBeVisible()

    // Read back from the server, not just the in-memory response.
    await page.reload()
    await expect(page.getByText(email)).toBeVisible()

    // Put the seeded address back: directory.spec.ts asserts the manager's
    // published work email, and fails whenever this spec runs before it.
    await page.getByTestId('person-edit-overview').click()
    await page.getByLabel('Work email').fill(original)
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toBeHidden()
  })

  test('P8-U9: approvers follow the manager, with no approver pickers', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill(COLLEAGUE_NAME)
    await page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first().click()
    await expect(page.getByRole('heading', { name: COLLEAGUE_NAME })).toBeVisible()

    await page.getByTestId('person-edit-approvers').click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('heading', { name: 'Edit approvers and shift' })).toBeVisible()
    // Plan 2026-10-07-001: only the manager is editable; the approvers follow it.
    await expect(dialog.getByLabel('Leave approver')).toHaveCount(0)
    await expect(dialog.getByText('Leave, expense and shift approvals follow the manager.')).toBeVisible()

    // The manager picker offers themself among the options (any active
    // employee in their own company, per get_person_form_options) --
    // picking the second option (index 1) avoids "None" at index 0
    // without depending on a specific fixture name. get_person_form_options
    // loads lazily on first open, so wait for it past the "None"-only state
    // before reading the list.
    const managerSelect = page.getByLabel('Manager')
    await expect(async () => {
      expect(await managerSelect.locator('option').count()).toBeGreaterThan(1)
    }).toPass({ timeout: 10000 })
    const options = await managerSelect.locator('option').allTextContents()
    await managerSelect.selectOption({ index: 1 })
    const chosenName = options[1]

    await page.getByRole('button', { name: 'Save' }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByText(chosenName).first()).toBeVisible()
    // Either the approvers follow the new manager, or the card says why not.
    await expect(
      page.getByText(/approvers follow the manager|^No approver:/).first(),
    ).toBeVisible()
  })

  test('a search nobody matches names it plainly', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill('zzz-nobody-by-that-name')
    await expect(page.getByText('Nobody matches that search')).toBeVisible()
  })

  // Plan 2026-10-17-001 U17 (HR-14): at desktop width a compact table, its
  // sortable headers carrying aria-sort the way ReportTable's do.
  test('U17: the desktop table sorts by name and by joining date, with aria-sort', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/helixhr/people')
    const table = page.getByTestId('people-table')
    await expect(table.getByTestId('people-row').first()).toBeVisible()

    const name = table.getByRole('columnheader', { name: 'Name', exact: true })
    const joined = table.getByRole('columnheader', { name: 'Joined', exact: true })
    // The server sends rows by name, so that is the starting sort.
    await expect(name).toHaveAttribute('aria-sort', 'ascending')
    await expect(joined).toHaveAttribute('aria-sort', 'none')

    const names = () => table.getByTestId('people-name').allTextContents()
    const ascending = await names()
    await name.getByRole('button').click()
    await expect(name).toHaveAttribute('aria-sort', 'descending')
    // Equal names read the same, so the reversed text is exact even with ties.
    expect(await names()).toEqual([...ascending].reverse())

    // Joining date starts newest first; blanks sit last either way.
    const dates = async () =>
      (await table.getByTestId('people-joined').evaluateAll((cells) =>
        cells.map((cell) => cell.getAttribute('data-date') || ''),
      )).filter(Boolean)
    await joined.getByRole('button').click()
    await expect(joined).toHaveAttribute('aria-sort', 'descending')
    await expect(name).toHaveAttribute('aria-sort', 'none')
    const newestFirst = await dates()
    expect(newestFirst.length).toBeGreaterThan(1)
    expect(newestFirst).toEqual([...newestFirst].sort().reverse())

    await joined.getByRole('button').click()
    await expect(joined).toHaveAttribute('aria-sort', 'ascending')
    const oldestFirst = await dates()
    expect(oldestFirst).toEqual([...oldestFirst].sort())
  })

  // U17 (HR-14): the department chips and the count, at both widths -- the
  // phone keeps its cards, the desktop its table, and both narrow the same.
  for (const width of [390, 1440]) {
    test(`U17: the department filter narrows the count at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/helixhr/people')
      const count = page.getByTestId('people-count')
      await expect(count).toContainText(/\d+ (person|people)/)
      const everyone = Number((await count.textContent())!.match(/\d+/)![0])

      // The seeded directory colleague has a department and most fixtures
      // have none, so there are at least two chips besides All.
      const chips = page.getByRole('group', { name: 'Filter people by department' })
      const chip = chips.getByRole('button').nth(1)
      const chipCount = Number((await chip.getAttribute('aria-label'))!.match(/\((\d+)\)$/)![1])
      expect(chipCount).toBeLessThan(everyone)

      await chip.click()
      await expect(chip).toHaveAttribute('aria-pressed', 'true')
      await expect(count).toContainText(`${chipCount} of ${everyone}`)
      await expect(page.getByTestId('people-row')).toHaveCount(chipCount)
      await expect(page.getByTestId('people-table')).toHaveCount(width >= 1024 ? 1 : 0)

      await chips.getByRole('button').first().click()
      await expect(page.getByTestId('people-row')).toHaveCount(everyone)
    })
  }

  // Plan 2026-10-22-001 U4 (R1, R4, R7). The dev site has no Microsoft
  // sign-in, so the welcome email is held. A per-run name keeps a re-run
  // clear of the duplicate refusal.
  test('U4: HR adds an employee with a login and lands on their page', async ({ page }) => {
    const stamp = Date.now()
    const firstName = `Newstart ${stamp}`
    await page.goto('/helixhr/people')
    await page.getByRole('button', { name: 'Add employee' }).click()
    await expect(page).toHaveURL(/\/helixhr\/people\/new$/)
    await expect(page.getByRole('heading', { name: 'Add employee' })).toBeVisible()

    await page.getByLabel('First name').fill(firstName)
    await page.getByLabel('Last name').fill('Playwright')
    const gender = page.getByLabel('Gender')
    await expect(gender.locator('option')).not.toHaveCount(1)
    await gender.selectOption({ index: 1 })
    await page.getByLabel('Date of birth').fill('1995-05-05')
    // Preselected when HR has one company; otherwise take the first.
    const company = page.getByLabel('Company', { exact: true })
    if (!(await company.inputValue())) await company.selectOption({ index: 1 })
    await page.getByLabel('Joining date').fill('2025-01-06')
    await page.getByLabel('Company email').fill(`newstart-${stamp}@helixhr.test`)
    await expect(page.getByLabel('Create login now')).toBeChecked()

    await page.getByRole('button', { name: 'Add employee' }).click()

    await expect(page).toHaveURL(/\/helixhr\/people\/(?!new$)[^/]+$/)
    await expect(page.getByRole('heading', { name: `${firstName} Playwright` })).toBeVisible()
    await expect(
      page.getByText(`Added ${firstName} Playwright. Login created; welcome email held until Microsoft sign-in is on.`).first(),
    ).toBeVisible()
  })

  test('U4: a missing required field is marked inline and nothing is added', async ({ page }) => {
    let posted = false
    page.on('request', (request) => {
      if (request.url().includes('helixhr.api.create_employee')) posted = true
    })
    await page.goto('/helixhr/people/new')
    await expect(page.getByLabel('First name')).toBeVisible()
    await page.getByLabel('Last name').fill('Nofirstname')
    await page.getByRole('button', { name: 'Add employee' }).click()

    const firstName = page.getByLabel('First name')
    await expect(page.getByText('Enter their first name.')).toBeVisible()
    await expect(firstName).toHaveAttribute('aria-invalid', 'true')
    await expect(firstName).toBeFocused()
    await expect(page.getByText(/fields need an answer/)).toBeVisible()
    await expect(page).toHaveURL(/\/helixhr\/people\/new$/)
    expect(posted).toBe(false)
  })

  // R16: the seeded "Directory Left" fixture (`ensure_directory_fixtures`).
  test('U4: "Include people who have left" finds a relieved person', async ({ page }) => {
    await page.goto('/helixhr/people')
    await page.getByLabel('Search').fill('Directory Left')
    await expect(page.getByText('Nobody matches that search')).toBeVisible()

    await page.getByLabel('Include people who have left').check()
    const row = page.getByTestId('people-row').filter({ hasText: 'Directory Left' })
    await expect(row).toHaveCount(1)
    await expect(row.getByTestId('people-status')).toHaveText('Left')
  })

  // HR-10 / U15: the shared SearchField, 44px under a coarse pointer.
  test('the search field is a 44px target on a phone', async ({ browser }) => {
    const context = await browser.newContext({
      storageState: 'tests/.auth/hr.json',
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    })
    const page = await context.newPage()
    await page.goto('/helixhr/people')
    const field = page.getByLabel('Search', { exact: true })
    await expect(field).toBeVisible()
    const box = await field.boundingBox()
    await context.close()
    expect(box?.height).toBeGreaterThanOrEqual(44)
  })
})

test('an employee identity has no People nav item, and a direct hit is refused server-side', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'People' })).toHaveCount(0)

  await page.goto('/helixhr/people')
  await expect(page.locator('[data-async-state="people:forbidden"]')).toBeVisible()

  await page.goto('/helixhr/people/HR-EMP-00001')
  await expect(page.locator('[data-async-state="person:forbidden"]')).toBeVisible()

  // Plan 2026-10-22-001 U4: no Add employee, and the form itself is refused.
  await page.goto('/helixhr/people')
  await expect(page.getByRole('button', { name: 'Add employee' })).toHaveCount(0)
  await page.goto('/helixhr/people/new')
  await expect(page.locator('[data-async-state="new-person:forbidden"]')).toBeVisible()
})

test('an IT Team identity -- a routed worker, not HR -- is refused the same way', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'it', 'this is the one negative assertion this project exists for')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'People' })).toHaveCount(0)

  await page.goto('/helixhr/people')
  await expect(page.locator('[data-async-state="people:forbidden"]')).toBeVisible()
})
