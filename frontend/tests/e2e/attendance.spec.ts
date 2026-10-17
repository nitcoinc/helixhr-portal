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

/** WCAG relative-luminance contrast of two computed `rgb()` colours. */
function contrast(a: string, b: string): number {
  const luminance = (colour: string) => {
    const [r, g, bl] = (colour.match(/[\d.]+/g) || []).slice(0, 3).map((part) => {
      const c = Number(part) / 255
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl
  }
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
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

  // U16 (ED-16, EP-12). Today is marked -- visibly and for assistive tech --
  // and every legend swatch measures at least 3:1 against the ground it sits
  // on, the non-text contrast minimum (WCAG 1.4.11).
  test('today is marked and the legend swatches are visible', async ({ page }) => {
    const response = await page.request.get('/api/method/helixhr.api.get_portal_bootstrap')
    const today = (await response.json()).message.today as string

    await page.goto('/helixhr/attendance')
    await page.waitForLoadState('networkidle')

    const current = page.locator('[aria-current="date"]')
    await expect(current).toHaveCount(1)
    await expect(current).toHaveAttribute('data-day', today)
    // The ring is drawn, not only announced.
    expect(await current.evaluate((cell) => getComputedStyle(cell).boxShadow)).not.toBe('none')

    const swatches = await page
      .getByRole('list', { name: 'Legend' })
      .locator('li > span[aria-hidden="true"]')
      .evaluateAll((spans) =>
        spans.map((span) => {
          const style = getComputedStyle(span)
          // A bordered swatch measures by its edge. The Late swatch is a
          // ring, which the browser paints as the widest of its box-shadows
          // (the zero-width one before it is the ring offset).
          const ring = style.boxShadow
            .split(/,(?![^(]*\))/)
            .map((shadow) => ({
              colour: (shadow.match(/rgba?\([^)]*\)/) || [''])[0],
              spread: parseFloat((shadow.match(/(-?[\d.]+)px\s*$/) || ['', '0'])[1]),
            }))
            .sort((x, y) => y.spread - x.spread)[0]
          const edge =
            parseFloat(style.borderTopWidth) > 0 ? style.borderTopColor : ring?.colour || ''
          let node: HTMLElement | null = span.parentElement
          let ground = 'rgba(0, 0, 0, 0)'
          while (node) {
            const bg = getComputedStyle(node).backgroundColor
            if (bg && !/rgba\(0, 0, 0, 0\)|transparent/.test(bg)) {
              ground = bg
              break
            }
            node = node.parentElement
          }
          return { label: span.parentElement!.textContent!.trim(), edge, ground }
        }),
      )
    expect(swatches.map((swatch) => swatch.label)).toEqual(
      expect.arrayContaining(['Present', 'Work from home', 'Absent', 'Late', 'No record']),
    )
    for (const { label, edge, ground } of swatches) {
      expect(contrast(edge, ground), `${label}: ${edge} on ${ground}`).toBeGreaterThanOrEqual(3)
    }

    // Another month has no today in it.
    await page.getByRole('button', { name: 'Previous month' }).click()
    await page.waitForLoadState('networkidle')
    await expect(page.locator('button[data-day]').first()).toBeVisible()
    await expect(page.locator('[aria-current="date"]')).toHaveCount(0)
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

  // P3-U6 step 1 / P3-KTD10: the day sheet now offers two things -- Fix a
  // day (an Attendance Request) and Report a problem (this) -- so the link is
  // "Report a problem" with its own sentence under it rather than one long
  // label.
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
    await dialog.getByRole('link', { name: 'Report a problem' }).click()

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
    expect(value).toMatch(
      /\((Present|Absent|Half day|On leave|Work from home|Holiday|No record|Nothing recorded)\)/,
    )
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
