import { test, expect, Page } from '@playwright/test'

// P2-U5. Attendance: the Monday-first grid, and the day sheet as a real
// overlay rather than the `fixed bottom-0` div it used to be -- opening above
// the tab bar, closing three ways, and putting focus back on the day that
// opened it (scenario 6). Plus "Report a problem", which is the whole of what
// the portal does about a wrong day: it asks (scenario 7).

/** The first day cell that exists in the rendered month. Blanks before the
 * 1st are `<span>`s, not buttons, so this is always a real day. */
function firstDay(page: Page) {
  return page.locator('button[data-day]').first()
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  test('the grid runs Monday first, like every other week in the portal', async ({ page }) => {
    await page.goto('/helixhr/attendance')
    await expect(page.getByRole('heading', { name: 'Attendance' })).toBeVisible()
    await page.waitForLoadState('networkidle')

    const headers = page.locator('.grid-cols-7').first().locator('span')
    await expect(headers).toHaveText(['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'])
  })

  test('a legend explains the dots the grid draws', async ({ page }) => {
    await page.goto('/helixhr/attendance')
    await page.waitForLoadState('networkidle')

    for (const word of ['Present', 'Absent', 'Late', 'No record']) {
      await expect(page.getByRole('listitem').filter({ hasText: word }).first()).toBeVisible()
    }
  })

  test('the day sheet closes by button, Escape and backdrop, and restores focus', async ({
    browser,
  }) => {
    // A coarse-pointer phone context: this is the width and input mode the
    // sheet exists for, and the tab bar it has to cover only exists here.
    const context = await browser.newContext({
      storageState: 'tests/.auth/employee.json',
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    })
    const page = await context.newPage()
    await page.goto('/helixhr/attendance')
    await page.waitForLoadState('networkidle')

    const day = firstDay(page)
    const dayName = await day.getAttribute('data-day')
    const dialog = page.getByRole('dialog')

    // 1. It opens *above* the fixed tab bar. The old panel's z-index put it
    //    underneath, so its primary action was unreachable.
    await day.click()
    await expect(dialog).toBeVisible()
    const stacking = await page.evaluate(() => {
      const overlay = document.querySelector('.dialog-overlay') as HTMLElement | null
      const bar = document.querySelector('nav.fixed, [data-tab-bar]') as HTMLElement | null
      return {
        overlay: overlay ? Number(getComputedStyle(overlay).zIndex) : null,
        bar: bar ? Number(getComputedStyle(bar).zIndex) : 10,
      }
    })
    expect(stacking.overlay).not.toBeNull()
    expect(stacking.overlay!).toBeGreaterThan(stacking.bar)

    // 2. Closing by its own control returns focus to the day that opened it.
    //    frappe-ui's close control is an unnamed icon button in the sheet's
    //    header, so it is addressed by position rather than by name.
    await dialog.getByRole('button').first().click()
    await expect(dialog).toBeHidden()
    await expect(page.locator(`button[data-day="${dayName}"]`)).toBeFocused()

    // 3. Escape.
    await day.click()
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(page.locator(`button[data-day="${dayName}"]`)).toBeFocused()

    // 4. The backdrop.
    await day.click()
    await expect(dialog).toBeVisible()
    await page.locator('.dialog-overlay').click({ position: { x: 5, y: 5 } })
    await expect(dialog).toBeHidden()

    await context.close()
  })

  test('Report a problem opens one request with the date and status already in it', async ({
    page,
  }) => {
    await page.goto('/helixhr/attendance')
    await page.waitForLoadState('networkidle')

    const day = firstDay(page)
    const label = await day.getAttribute('aria-label')
    await day.click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('link', { name: 'Report a problem with this day' }).click()

    // One request, on the Requests route, with its subject already written --
    // attendance correction stays in Frappe HR (P2-R15).
    await expect(page).toHaveURL(/\/requests\?/)
    const subject = page.getByRole('dialog').getByLabel('Subject')
    await expect(subject).toBeVisible()

    const value = await subject.inputValue()
    expect(value).toContain('Attendance problem on')
    // The exact day, taken from the cell that was tapped.
    const dayNumber = (label || '').match(/\b(\d{1,2})\b/)?.[1]
    expect(value).toContain(String(Number(dayNumber)))
    // And its status, so HR does not have to ask what was wrong with it.
    expect(value).toMatch(/\((Present|Absent|Half day|On leave|Holiday|No record|Nothing recorded)\)/)
  })

  test('the month bounds are the API contract, not just the picker (P2-R22)', async ({
    page,
    baseURL,
  }) => {
    await page.goto('/helixhr/attendance')

    // A reversed span is refused by the server, whatever a caller sends.
    const response = await page.request.get(
      `${baseURL}/api/method/helixhr.api.get_my_attendance?from_date=2026-03-31&to_date=2026-03-01`,
    )
    expect(response.ok()).toBeFalsy()
  })
})
