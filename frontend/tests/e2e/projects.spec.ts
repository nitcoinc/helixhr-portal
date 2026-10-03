import { test, expect } from '@playwright/test'

// P7-U5 / P7-R1-R3. Administer a project end to end -- HR-scoped in this
// suite (an HR Manager resolves to the "company" branch of
// `resolve_project_scope`), the same posture people.spec.ts and
// reports.spec.ts already take for `resolve_admin_scope`.
//
// `setup_playwright_fixtures` seeds a colleague ("Manager") in the HR
// fixture's own company -- the same fixture people.spec.ts and
// directory.spec.ts already search for -- reused here to assign a member.
const COLLEAGUE_NAME = 'Manager'

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'projects is scoped by resolve_project_scope; hr covers the "company" branch')
  })

  test('an HR identity sees the Projects nav entry and administers a project end to end', async ({ page }) => {
    await page.goto('/helixhr/')
    // U15: the HR section starts collapsed on a first visit.
    const hrSection = page.getByRole('button', { name: 'HR', exact: true })
    if ((await hrSection.getAttribute('aria-expanded')) === 'false') await hrSection.click()
    await expect(page.getByRole('link', { name: 'Projects' })).toBeVisible()

    await page.goto('/helixhr/projects')
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()

    // Creating a project shows it in the list without a reload -- this test
    // proves it by landing straight on the new project's own detail route,
    // built from the create call's own response.
    const projectName = `E2E Project ${Date.now()}`
    await page.getByRole('button', { name: 'New project' }).click()
    // P8-U2: name/priority/type/billable, the same fields Desk asks for
    // minus the server-derived company (shown as static text, never asked
    // for -- KTD2 -- so this only confirms it renders, not its wording).
    // `exact: true` on Create below -- a stray fixture project from
    // another spec file whose name happens to contain "creates" would
    // otherwise collide with this button under Playwright's default
    // case-insensitive substring match.
    await expect(page.getByTestId('project-create-form').getByText('Company', { exact: false })).toBeVisible()
    await page.getByLabel('Project name').fill(projectName)
    await page.getByLabel('Priority').selectOption('High')
    await page.getByLabel('Billable').check()
    await page.getByRole('button', { name: 'Create', exact: true }).click()

    await expect(page).toHaveURL(/\/helixhr\/projects\/[^/]+$/)
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
    await expect(page.getByTestId('project-billable')).toHaveText('Yes')

    // Marking a project billable persists across a reload -- not just in the
    // create response held in memory, but read back from `get_project`.
    // Priority rides along the same way.
    await page.reload()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
    await expect(page.getByTestId('project-billable')).toHaveText('Yes')
    await expect(page.getByText('High')).toBeVisible()

    // Adding a task shows it under the project.
    const taskName = `Kickoff call ${Date.now()}`
    await page.getByPlaceholder('New task').fill(taskName)
    await page.getByRole('button', { name: 'Add task' }).click()
    await expect(page.getByText(taskName)).toBeVisible()

    // Assigning a person shows them in the member list by name. The
    // creator (this HR identity) is already a member the moment the
    // project exists -- create_project adds them in the same write, or
    // they would fall straight back out of their own scope the instant
    // they created it (see the endpoint's own docstring) -- so "Nobody is
    // assigned" is never true here; the count going from one to two is
    // what proves the add.
    await expect(page.getByText('Nobody is assigned to this project yet.')).toHaveCount(0)
    // Scoped to the section housing the picker, so this never counts the
    // picker's own dropdown <li> rows once one opens.
    const membersSection = page.locator('section', { has: page.getByLabel('Add a person') })
    const before = await membersSection.locator('li').count()
    await page.getByLabel('Add a person').fill(COLLEAGUE_NAME)
    const match = page.getByRole('button', { name: new RegExp(COLLEAGUE_NAME) }).first()
    await expect(match).toBeVisible()
    await match.click()
    await expect(page.locator('ul li').filter({ hasText: COLLEAGUE_NAME }).first()).toBeVisible()
    // The picker dropdown closes itself on a successful add (memberQuery is
    // cleared), so this count is the members list alone again, not the
    // dropdown's own <li> rows.
    await expect(membersSection.locator('li')).toHaveCount(before + 1)

    // Back to the list: the new project narrows into view by name, without a
    // full reload of the page.
    await page.goto('/helixhr/projects')
    await page.getByLabel('Search').fill(projectName)
    await expect(page.getByRole('button', { name: new RegExp(projectName) })).toBeVisible()
  })

  test('the screen renders at phone width with no horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto('/helixhr/projects')
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()

    const hasHorizontalScroll = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(hasHorizontalScroll).toBe(false)
  })
})

test('an employee identity has no Projects nav item, and a direct hit is refused server-side', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  await expect(page.getByRole('link', { name: 'Projects' })).toHaveCount(0)

  await page.goto('/helixhr/projects')
  await expect(page.locator('[data-async-state="projects:forbidden"]')).toBeVisible()
})
