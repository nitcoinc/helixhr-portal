import { test, expect, type Page } from '@playwright/test'

// P7-U5 / P7-R1-R3. Administer a project end to end -- HR-scoped in this
// suite (an HR Manager resolves to the "company" branch of
// `resolve_project_scope`), the same posture people.spec.ts and
// reports.spec.ts already take for `resolve_admin_scope`.
//
// `setup_playwright_fixtures` seeds a colleague ("Manager") in the HR
// fixture's own company -- the same fixture people.spec.ts and
// directory.spec.ts already search for -- reused here to assign a member.
const COLLEAGUE_NAME = 'Manager'
// Plan 2026-10-31-001: `ensure_projects_workspace_fixtures` seeds a project
// with 60 open tasks and three people with logins named "Projpick ...". The
// department add reuses the directory fixture's department, whose only
// person (Directory Colleague) has no login, so is always skipped.
const SIXTY_PROJECT = '_E2E Sixty Tasks'
const PICK_NAMES = ['Projpick Ada', 'Projpick Ben', 'Projpick Cy']
const DIRECTORY_DEPARTMENT = '_Test Directory Department'

async function openSixtyProject(page: Page) {
  // The search filter rides the URL, so the list opens already narrowed.
  await page.goto(`/helixhr/projects?q=${encodeURIComponent(SIXTY_PROJECT)}`)
  await page.getByRole('button', { name: new RegExp(SIXTY_PROJECT) }).first().click()
  await expect(page.getByRole('heading', { name: SIXTY_PROJECT })).toBeVisible()
}

test.describe('hr', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'hr', 'projects is scoped by resolve_project_scope; hr covers the "company" branch')
  })

  test('an HR identity sees the Projects nav entry and administers a project end to end', async ({
    page,
    browser,
  }) => {
    await page.goto('/helixhr/')
    // U15: the HR section starts collapsed on a first visit.
    const hrSection = page.getByRole('button', { name: 'HR', exact: true })
    if ((await hrSection.getAttribute('aria-expanded')) === 'false') await hrSection.click()
    await expect(page.getByRole('link', { name: 'Projects', exact: true })).toBeVisible()

    await page.goto('/helixhr/projects')
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()

    // Creating a project lands straight on its own detail route, built from
    // the create call's own response. U2: the form is a dialog now, so it
    // never pushes the table down.
    const projectName = `E2E Project ${Date.now()}`
    await page.getByRole('button', { name: 'New project' }).click()
    const createForm = page.getByTestId('project-create-form')
    // P8-U2: the server-derived company is shown, never asked for (KTD2).
    await expect(createForm.getByText('This project will be created in', { exact: false })).toBeVisible()
    await createForm.getByLabel('Project name').fill(projectName)
    await createForm.getByLabel('Priority').selectOption('High')
    await createForm.getByLabel('Billable').check()
    await createForm.getByRole('button', { name: 'Create', exact: true }).click()

    await expect(page).toHaveURL(/\/helixhr\/projects\/[^/]+$/)
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
    await expect(page.getByTestId('project-billable')).toHaveText('Yes')

    // Billable and priority persist across a reload, read back from
    // `get_project`, on the one-line facts strip (U3).
    await page.reload()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
    await expect(page.getByTestId('project-billable')).toHaveText('Yes')
    await expect(page.getByTestId('project-facts').getByText('High')).toBeVisible()

    const tasksPane = page.getByTestId('project-tasks-pane')
    const peoplePane = page.getByTestId('project-people-pane')

    // One line adds one task.
    const taskName = `Kickoff call ${Date.now()}`
    await page.getByPlaceholder('New task').fill(taskName)
    await tasksPane.getByRole('button', { name: 'Add task' }).click()
    await expect(tasksPane.getByText(taskName)).toBeVisible()

    // R4: three pasted lines are one `add_tasks` call that adds three, shown
    // in the Tasks pane beside -- not below -- the people.
    const stamp = Date.now()
    const pasted = [`Design ${stamp}`, `Build ${stamp}`, `Ship ${stamp}`]
    const addTaskCalls: string[] = []
    page.on('request', (request) => {
      if (request.url().includes('helixhr.api.add_tasks')) addTaskCalls.push(request.url())
    })
    await page.getByPlaceholder('New task').fill(pasted.join('\n'))
    await tasksPane.getByRole('button', { name: 'Add 3 tasks' }).click()
    for (const subject of pasted) await expect(tasksPane.getByText(subject)).toBeVisible()
    expect(addTaskCalls).toHaveLength(1)
    const tasksBox = await tasksPane.boundingBox()
    const peopleBox = await peoplePane.boundingBox()
    expect(tasksBox && peopleBox && tasksBox.y < peopleBox.y + peopleBox.height).toBeTruthy()

    // R5 (U6): pick three people and add them in one request. The creator
    // is already a member, so "Nobody is assigned" is never true here.
    await expect(page.getByText('Nobody is assigned to this project yet.')).toHaveCount(0)
    const members = peoplePane.getByTestId('project-member')
    const before = await members.count()
    const memberCalls: string[] = []
    page.on('request', (request) => {
      if (request.url().includes('helixhr.api.add_project_members')) memberCalls.push(request.url())
    })
    await peoplePane.getByRole('button', { name: 'Add people' }).click()
    const picker = page.getByRole('dialog')
    await picker.getByLabel('Find people').fill('Projpick')
    for (const name of PICK_NAMES) await picker.getByRole('checkbox', { name: new RegExp(name) }).check()
    await expect(picker.getByTestId('project-picker-count')).toHaveText('3 people selected.')
    await picker.getByRole('button', { name: 'Add 3 people' }).click()
    await expect(picker.getByTestId('project-picker-result')).toContainText('Added 3, skipped 0.')
    expect(memberCalls).toHaveLength(1)
    await picker.getByRole('button', { name: 'Done' }).click()
    await expect(members).toHaveCount(before + 3)
    for (const name of PICK_NAMES) await expect(peoplePane.getByText(name)).toBeVisible()

    // R6/R7: a whole department; its one person has no login and is named
    // as skipped, with the reason, until dismissed.
    await peoplePane.getByRole('button', { name: 'Add people' }).click()
    await picker.getByRole('button', { name: 'Department' }).click()
    const addDepartment = picker.getByRole('button', { name: /^Add everyone/ })
    await expect(addDepartment).toBeDisabled()
    await picker.getByLabel('Department').selectOption({ label: DIRECTORY_DEPARTMENT })
    await picker.getByRole('button', { name: `Add everyone in ${DIRECTORY_DEPARTMENT}` }).click()
    const result = picker.getByTestId('project-picker-result')
    await expect(result).toContainText(/Added \d+, skipped [1-9]/)
    await expect(result).toContainText('Directory Colleague · no portal login')
    await result.getByRole('button', { name: 'Dismiss' }).click()
    await expect(result).toHaveCount(0)
    await picker.getByRole('button', { name: 'Done' }).click()

    // The older single-person path still works through the same picker.
    await peoplePane.getByRole('button', { name: 'Add people' }).click()
    await picker.getByLabel('Find people').fill(COLLEAGUE_NAME)
    await picker.getByRole('checkbox', { name: new RegExp(COLLEAGUE_NAME) }).first().check()
    await picker.getByRole('button', { name: 'Add 1 person' }).click()
    await expect(picker.getByTestId('project-picker-result')).toContainText('Added 1')
    await picker.getByRole('button', { name: 'Done' }).click()
    await expect(peoplePane.getByText(COLLEAGUE_NAME).first()).toBeVisible()

    // R8: open to everyone, confirmed first with its effect named.
    const toggle = peoplePane.getByRole('switch', { name: /Open to everyone in/ })
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await toggle.click()
    await expect(page.getByTestId('project-open-confirm')).toContainText(
      'can book time on this project.',
    )
    await page.getByRole('button', { name: 'Open to everyone', exact: true }).click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    await expect(peoplePane.getByTestId('project-open-note')).toBeVisible()
    // Members stay listed (and removable) while the project is open.
    await expect(members.first()).toBeVisible()

    // The list says "Everyone" in the members column.
    await page.goto(`/helixhr/projects?q=${encodeURIComponent(projectName)}`)
    const row = page.getByTestId('project-row').filter({ hasText: projectName })
    await expect(row.getByTestId('project-members-cell')).toHaveText('Everyone')

    // A non-member employee of the same company can now book it: the
    // timesheet's task picker is built from `get_my_projects` (Timesheet.vue),
    // asked here directly so the run never depends on which week is open.
    const employee = await browser.newContext({ storageState: 'tests/.auth/employee.json' })
    try {
      const response = await employee.request.get('/api/method/helixhr.api.get_my_projects')
      expect(response.ok()).toBeTruthy()
      const bookable = (await response.json()).message as { project_name: string; tasks: { subject: string }[] }[]
      const project = bookable.find((entry) => entry.project_name === projectName)
      expect(project, 'the open project is bookable by a non-member').toBeTruthy()
      expect(project?.tasks.map((task) => task.subject)).toContain(pasted[0])
    } finally {
      await employee.close()
    }

    // And back off, so this run's project stops widening the employee's
    // picker. The confirm names what turning it off costs.
    await row.getByRole('button', { name: new RegExp(projectName) }).click()
    await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
    await page.getByRole('switch', { name: /Open to everyone in/ }).click()
    await expect(page.getByTestId('project-open-confirm')).toContainText(
      'Only listed members can book.',
    )
    await page.getByRole('button', { name: 'Members only', exact: true }).click()
    await expect(page.getByRole('switch', { name: /Open to everyone in/ })).toHaveAttribute('aria-checked', 'false')
    await expect(page.getByTestId('project-open-note')).toHaveCount(0)
  })

  // R2: the filters live in the URL, so a reload keeps them.
  test('filters the list by billable, and a reload keeps the filter', async ({ page }) => {
    await page.goto('/helixhr/projects')
    const count = page.getByTestId('projects-count')
    await expect(count).toHaveText(/^\d+ projects?\./)
    const all = Number((await count.textContent())?.match(/\d+/)?.[0])

    await page.getByLabel('Billable', { exact: true }).selectOption({ label: 'Non-billable' })
    await expect(page).toHaveURL(/[?&]billable=no/)
    await expect(count).toHaveText(new RegExp(`^\\d+ of ${all} projects?\\.`))
    const narrowed = await count.textContent()
    // The seeded sixty-task project is non-billable.
    await expect(page.getByRole('button', { name: new RegExp(SIXTY_PROJECT) })).toBeVisible()

    await page.reload()
    await expect(page.getByLabel('Billable', { exact: true })).toHaveValue(/no/)
    await expect(count).toHaveText((narrowed || '').trim())

    // Nothing matches: say so, and offer the way back.
    await page.getByLabel('Search', { exact: true }).fill(`no-such-project-${Date.now()}`)
    await expect(page.getByText('No projects match.')).toBeVisible()
    await page.getByRole('button', { name: 'Clear filters' }).click()
    await expect(count).toHaveText(`${all} ${all === 1 ? 'project' : 'projects'}.`)
    await expect(page).not.toHaveURL(/billable=/)
  })

  test('the screen renders at phone width as cards, with no horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto('/helixhr/projects')
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(page.getByTestId('project-card').first()).toBeVisible()
    await expect(page.getByTestId('projects-table')).toHaveCount(0)

    const hasHorizontalScroll = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(hasHorizontalScroll).toBe(false)

    // The detail's panes are tabs on a phone.
    await openSixtyProject(page)
    const tabs = page.getByRole('tablist', { name: 'Project sections' })
    await expect(tabs.getByRole('tab', { name: /Tasks/ })).toHaveAttribute('aria-selected', 'true')
    await tabs.getByRole('tab', { name: /People/ }).click()
    await expect(page.getByTestId('project-people-pane')).toBeVisible()
    await expect(page.getByTestId('project-tasks-pane')).toBeHidden()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth),
    ).toBe(false)
  })

  // R3: each pane shows 25 rows, then "Show N more"; its search narrows.
  test('a project with 60 tasks shows 25, searches, and shows more', async ({ page }) => {
    await openSixtyProject(page)
    const pane = page.getByTestId('project-tasks-pane')
    const rows = pane.getByTestId('project-task')
    await expect(rows).toHaveCount(25)
    await expect(pane.getByTestId('project-tasks-count')).toHaveText('Showing 25 of 60 tasks.')

    await pane.getByLabel('Search tasks').fill('Sixty task 42')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('Sixty task 42')
    await pane.getByLabel('Search tasks').fill(`nothing-${Date.now()}`)
    await expect(pane.getByText(/^No tasks match/)).toBeVisible()
    await pane.getByLabel('Search tasks').fill('')

    await pane.getByRole('button', { name: 'Show 25 more' }).click()
    await expect(rows).toHaveCount(50)
    await pane.getByRole('button', { name: 'Show 10 more' }).click()
    await expect(rows).toHaveCount(60)
    await expect(pane.getByRole('button', { name: /^Show \d+ more$/ })).toHaveCount(0)
  })

  // A company access grant reads a project but never changes it (plan
  // 2026-10-14-001 KTD4). The server's own `can_edit` is flipped in flight,
  // so this proves what the page hides, not who may write.
  test('a view-only caller sees no add controls in either pane', async ({ page }) => {
    await page.route('**/api/method/helixhr.api.get_project*', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.message = { ...body.message, can_edit: false, departments: [] }
      await route.fulfill({ response, json: body })
    })
    await openSixtyProject(page)
    await expect(page.getByTestId('project-view-only')).toBeVisible()
    await expect(page.getByTestId('project-task').first()).toBeVisible()
    await expect(page.getByPlaceholder('New task')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /^Add (task|\d+ tasks)$/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Add people' })).toHaveCount(0)
    await expect(page.getByRole('switch')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /^(Close|Remove) / })).toHaveCount(0)
  })

  // U6: the picker is a real dialog -- reachable by keyboard, focus kept
  // inside it, and handed back to the button that opened it.
  test('the member picker is keyboard reachable and returns focus', async ({ page }) => {
    await openSixtyProject(page)
    const opener = page.getByTestId('project-people-pane').getByRole('button', { name: 'Add people' })
    await opener.focus()
    await page.keyboard.press('Enter')
    const picker = page.getByRole('dialog')
    await expect(picker).toBeVisible()
    for (let step = 0; step < 8; step += 1) {
      await page.keyboard.press('Tab')
      expect(await picker.evaluate((dialog) => dialog.contains(document.activeElement))).toBe(true)
    }
    await picker.getByLabel('Find people').fill('Projpick')
    await expect(picker.getByRole('checkbox', { name: /Projpick Ada/ })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(picker).toHaveCount(0)
    await expect(opener).toBeFocused()
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
    await page.goto('/helixhr/projects')
    const field = page.getByLabel('Search', { exact: true })
    await expect(field).toBeVisible()
    const box = await field.boundingBox()
    await context.close()
    expect(box?.height).toBeGreaterThanOrEqual(44)
  })
})

test('an employee identity has no Projects nav item, and a direct hit is refused server-side', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'covered by the hr branch above')

  await page.goto('/helixhr/')
  // exact: "My projects" (plan 2026-10-05-001 U6) is every employee's.
  await expect(page.getByRole('link', { name: 'Projects', exact: true })).toHaveCount(0)

  await page.goto('/helixhr/projects')
  await expect(page.locator('[data-async-state="projects:forbidden"]')).toBeVisible()
})

// Plan 2026-10-05-001 U6: any linked employee reaches My projects from the nav.
test('an employee reaches My projects from the nav and sees cards or the empty state', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'My projects is the employee view')

  await page.goto('/helixhr/')
  await page.getByRole('link', { name: 'My projects', exact: true }).first().click()
  await expect(page).toHaveURL(/\/helixhr\/my-projects$/)
  await expect(page.getByRole('heading', { name: 'My projects' })).toBeVisible()
  // U16 (EP-23): a project with nothing of mine this month is a one-line
  // row under "Other projects", open by default when no card is active.
  const cards = page.getByTestId('my-project-card')
  const rows = page.getByTestId('my-project-row')
  const empty = page.locator('[data-async-state="my-projects:empty"]')
  await expect(cards.first().or(rows.first()).or(empty)).toBeVisible()
  // No money on this page, ever.
  await expect(page.getByText(/billable|billing|cost/i)).toHaveCount(0)
})

// U16 (EP-23). Projects with my hours or open tasks this month come first as
// cards; the rest fold under "Other projects (N)", a real disclosure. Stubbed:
// which projects carry hours this month is the site's data, not this spec's.
test('My projects shows active projects before the fold', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('employee'), 'My projects is the employee view')

  const project = (name: string, hours: number, tasks: object[] = []) => ({
    name,
    project_name: name,
    customer: null,
    status: 'Open',
    hours_this_month: hours,
    tasks,
  })
  await page.route('**/api/method/helixhr.api.get_my_project_overview*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        message: [
          project('_Spec Booked', 6),
          project('_Spec Quiet', 0),
          project('_Spec Tasked', 0, [{ name: 'T-1', subject: 'Write it', status: 'Open', due: null }]),
        ],
      }),
    }),
  )

  await page.goto('/helixhr/my-projects')
  await page.waitForLoadState('networkidle')

  // The two active projects are the cards, in the server's order.
  const cards = page.getByTestId('my-project-card')
  await expect(cards).toHaveCount(2)
  await expect(cards.nth(0)).toContainText('_Spec Booked')
  await expect(cards.nth(1)).toContainText('_Spec Tasked')
  // A card here for its hours has no empty tasks block.
  await expect(cards.nth(0).getByText('My open tasks')).toHaveCount(0)
  await expect(page.getByText('No open tasks')).toHaveCount(0)

  // The quiet one is folded, and the fold says so.
  const toggle = page.getByRole('button', { name: 'Other projects (1)' })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByText('_Spec Quiet')).toBeHidden()

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByTestId('my-project-row').filter({ hasText: '_Spec Quiet' })).toBeVisible()
})
