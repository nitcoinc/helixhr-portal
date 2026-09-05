import { test, expect, Page } from '@playwright/test'

// P2-U3. The visual and asynchronous foundation, checked rather than asserted
// in a document: one surface language, five distinguishable async states, and
// overlays that behave at the widths and input modes the plan names
// (P2-R1..P2-R9, P2-R24).
//
// Everything here is deterministic. There is no screenshot comparison and no
// third-party accessibility package -- the plan defers both -- so each check
// reads a computed value out of the real rendered page.

const ROUTES = [
  '/helixhr/',
  '/helixhr/leave',
  '/helixhr/timesheet',
  '/helixhr/timesheet/history',
  '/helixhr/requests',
  '/helixhr/attendance',
  '/helixhr/documents',
  '/helixhr/notifications',
  '/helixhr/profile',
]

/** Horizontal overflow of the document, in CSS pixels. Zero is the only
 * acceptable value at every supported width (P2-R3). */
async function horizontalOverflow(page: Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== 'employee', 'employee-only scenarios')
  })

  // ── Scenario 1 ────────────────────────────────────────────────────────
  test('a failed request is an unavailable panel with Retry, never an empty list (P2-AE8)', async ({
    page,
  }) => {
    // Only the list call fails. The bootstrap still succeeds, so this is a
    // section failure inside a healthy portal -- exactly the case that used
    // to render as "You have no requests yet". P2-U8 moved Requests off
    // `frappe.client.get_list` onto its own session-scoped endpoint, so that
    // is what the stub names now.
    let failures = 0
    await page.route('**/api/method/helixhr.api.get_my_requests*', (route) => {
      failures += 1
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ exception: 'Exception: seeded failure' }),
      })
    })

    await page.goto('/helixhr/requests')

    const region = page.locator('[data-async-state^="requests-list"]')
    await expect(region).toHaveAttribute('data-async-state', 'requests-list:unavailable')
    await expect(page.getByText("We couldn't load this")).toBeVisible()
    await expect(page.getByText('No requests yet')).toHaveCount(0)

    // One bounded retry, and it re-issues the request rather than reloading
    // the app (P2-R25).
    const before = failures
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect.poll(() => failures).toBeGreaterThan(before)
  })

  test('a successful empty response is a task-specific empty state with an action', async ({
    page,
  }) => {
    await page.route('**/api/method/helixhr.api.get_my_requests*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: { requests: [], total: 0, limit: 20 } }),
      }),
    )

    await page.goto('/helixhr/requests')

    await expect(page.locator('[data-async-state="requests-list:empty"]')).toBeVisible()
    await expect(page.getByText('No requests yet')).toBeVisible()
    // Every empty state names the next action (docs/design-system.md).
    await expect(
      page.locator('[data-async-state="requests-list:empty"]').getByRole('button', {
        name: 'Send your first request',
      }),
    ).toBeVisible()
  })

  test('a pending request shows a sized skeleton, not a blank region', async ({ page }) => {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    await page.route('**/api/method/helixhr.api.get_my_requests*', async (route) => {
      await held
      return route.continue()
    })

    await page.goto('/helixhr/requests', { waitUntil: 'commit' })

    const region = page.locator('[data-async-state="requests-list:pending"]')
    await expect(region).toBeVisible()
    await expect(region.getByRole('status')).toHaveAttribute('aria-busy', 'true')
    // "Sized" is the load-bearing word: an unsized skeleton is what produced
    // the U0 baseline's 0.8431 CLS.
    const height = await region.evaluate((node) => node.getBoundingClientRect().height)
    expect(height).toBeGreaterThan(100)

    release()
    await expect(page.locator('[data-async-state^="requests-list"]')).not.toHaveAttribute(
      'data-async-state',
      'requests-list:pending',
    )
  })

  // ── Scenario 2 ────────────────────────────────────────────────────────
  test('no page scrolls in two dimensions at 320px reflow or 360px mobile', async ({ page }) => {
    for (const width of [320, 360]) {
      await page.setViewportSize({ width, height: 720 })
      for (const route of ROUTES) {
        await page.goto(route)
        await page.waitForLoadState('networkidle')
        expect(await horizontalOverflow(page), `${route} at ${width}px`).toBe(0)
      }
    }
  })

  test("a page's sticky action bar clears the fixed tab bar at 360px", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 720 })
    // Profile rather than Timesheet: every employee has an editable field, so
    // the bar is reachable without depending on which workflow state this
    // site's current week happens to be in.
    await page.goto('/helixhr/profile')
    await page.waitForLoadState('networkidle')
    // A value this run has not used, so the bar is genuinely dirty. Nothing
    // is saved -- the check is about where the bar sits, not what it writes.
    await page
      .getByTestId('profile-editable-cell_number')
      .getByLabel('Mobile')
      .fill(`+1-555-${String(Date.now() % 10000).padStart(4, '0')}`)

    const bar = page.locator('.action-bar')
    await expect(bar).toBeVisible()
    const save = page.getByTestId('profile-save-bar').getByRole('button', { name: 'Save' })
    const action = await save.boundingBox()
    const tabBar = await page.locator('nav[aria-label="Main"]').last().boundingBox()
    expect(action).not.toBeNull()
    expect(tabBar).not.toBeNull()
    // The whole control sits above the bar's top edge, not merely somewhere
    // on the page: `sticky bottom-0` put Timesheet's Submit underneath it.
    expect(action!.y + action!.height).toBeLessThanOrEqual(tabBar!.y + 1)
  })

  // ── Scenario 3 ────────────────────────────────────────────────────────
  test('navigation switches once, at 1024px, and content keeps its intended width', async ({
    page,
  }) => {
    const sideNav = page.locator('aside nav[aria-label="Main"]')
    const tabBar = page.locator('div > nav[aria-label="Main"]')

    for (const width of [768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/helixhr/leave')
      await page.waitForLoadState('networkidle')

      if (width >= 1024) {
        await expect(sideNav).toBeVisible()
        await expect(tabBar).toBeHidden()
      } else {
        await expect(sideNav).toBeHidden()
        await expect(tabBar).toBeVisible()
      }
      expect(await horizontalOverflow(page), `overflow at ${width}px`).toBe(0)

      // Capped rather than stretched: a form that runs the full 1440px is
      // unreadable, which is what `max-w-5xl` on <main> prevents.
      const main = await page.locator('main').boundingBox()
      expect(main!.width).toBeLessThanOrEqual(1024)
    }
  })

  test('200% text zoom does not create horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 720 })
    // Doubling the root font size is the deterministic stand-in for a browser
    // text-zoom setting; every size in this app is relative to it.
    await page.addInitScript(() => {
      document.addEventListener('DOMContentLoaded', () => {
        document.documentElement.style.fontSize = '32px'
      })
    })
    for (const route of ['/helixhr/', '/helixhr/leave', '/helixhr/profile']) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      expect(await horizontalOverflow(page), `${route} at 200% text`).toBe(0)
    }
  })

  // ── Scenario 4 ────────────────────────────────────────────────────────
  test('an overlay traps focus, closes with Escape, and gives focus back', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/helixhr/')
    await page.waitForLoadState('networkidle')

    const more = page.getByRole('button', { name: 'More' })
    await more.focus()
    await page.keyboard.press('Enter')

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    // Focus is inside the overlay, not left behind on the page underneath.
    await expect
      .poll(() => dialog.evaluate((node) => node.contains(document.activeElement)))
      .toBe(true)

    // The sheet covers the tab bar rather than sliding behind it: the tab bar
    // is `z-10` and its own stacking context, so the overlay needs a value of
    // its own (index.css).
    const overlayZ = await page
      .locator('.dialog-overlay')
      .evaluate((node) => Number(getComputedStyle(node).zIndex))
    expect(overlayZ).toBeGreaterThan(10)

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(more).toBeFocused()
  })

  test('the More sheet marks the route you are standing on', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/helixhr/documents')
    await page.waitForLoadState('networkidle')

    const more = page.getByRole('button', { name: 'More' })
    // The tab itself is lit, so five unlit tabs never claim you are nowhere.
    await expect(more).toHaveAttribute('aria-current', 'page')

    await more.click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('link', { name: 'Documents' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    await expect(dialog.getByRole('link', { name: 'Profile' })).not.toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  // ── Scenario 5 ────────────────────────────────────────────────────────
  test('every control is at least 44px tall under a coarse pointer', async ({ browser }) => {
    const context = await browser.newContext({
      storageState: 'tests/.auth/employee.json',
      viewport: { width: 390, height: 844 },
      hasTouch: true,
      isMobile: true,
    })
    const page = await context.newPage()
    const findings: string[] = []

    for (const route of ROUTES) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      findings.push(
        ...(await page.evaluate(() => {
          const small: string[] = []
          const nodes = document.querySelectorAll('button, a[href], [role="button"], select, input')
          for (const node of nodes) {
            const element = node as HTMLElement
            if (element.hasAttribute('disabled')) continue
            const style = getComputedStyle(element)
            if (style.display === 'none' || style.visibility === 'hidden') continue
            // Inline links inside a sentence are text, not targets; `min-height`
            // does not apply to them and WCAG 2.5.8 exempts them.
            if (style.display === 'inline') continue
            const box = element.getBoundingClientRect()
            if (box.height === 0 && box.width === 0) continue
            if (box.height < 44) {
              small.push(
                `${location.pathname} ${element.tagName}.${element.className
                  .toString()
                  .split(/\s+/)
                  .slice(0, 3)
                  .join('.')} = ${box.height.toFixed(1)}px`,
              )
            }
          }
          return small
        })),
      )
    }
    await context.close()
    expect(findings, findings.join('\n')).toEqual([])
  })

  test('status is carried by words, not only by colour', async ({ page }) => {
    await page.goto('/helixhr/leave')
    await page.waitForLoadState('networkidle')

    const badges = page.locator('[data-status]')
    const count = await badges.count()
    test.skip(count === 0, 'no leave on this site to read a status from')
    for (let index = 0; index < Math.min(count, 10); index += 1) {
      // The plain sentence, never the Frappe value, and never an empty pill
      // whose only content is a hue.
      const text = (await badges.nth(index).innerText()).trim()
      expect(text.length).toBeGreaterThan(0)
      expect(text).not.toBe('Rejected')
    }
  })

  // ── Scenario 6 ────────────────────────────────────────────────────────
  test('reduced motion stops the pulse but keeps the loading region legible', async ({
    browser,
  }) => {
    const context = await browser.newContext({
      storageState: 'tests/.auth/employee.json',
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    await page.route('**/api/method/helixhr.api.get_my_requests*', async (route) => {
      await held
      return route.continue()
    })

    await page.goto('/helixhr/requests', { waitUntil: 'commit' })
    const skeleton = page.locator('[data-async-state="requests-list:pending"] .animate-pulse').first()
    await expect(skeleton).toBeVisible()

    const style = await skeleton.evaluate((node) => {
      const computed = getComputedStyle(node)
      return { duration: computed.animationDuration, opacity: computed.opacity }
    })
    // The blanket reduced-motion rule would otherwise freeze the pulse at
    // whatever opacity one iteration lands on; index.css pins it to a legible
    // resting tint instead.
    expect(parseFloat(style.duration)).toBeLessThan(0.05)
    expect(parseFloat(style.opacity)).toBe(1)
    // And the caption still says what is happening, which is the part a
    // stopped animation cannot carry.
    await expect(page.getByRole('status')).toHaveAttribute('aria-busy', 'true')

    release()
    await context.close()
  })

  // ── Scenario 7 ────────────────────────────────────────────────────────
  test('the production build makes no Google Fonts request (P2-R24)', async ({ browser }) => {
    const context = await browser.newContext({ storageState: 'tests/.auth/employee.json' })
    const page = await context.newPage()
    const remote: string[] = []
    page.on('request', (request) => {
      const url = request.url()
      if (/fonts\.googleapis\.com|fonts\.gstatic\.com/.test(url)) remote.push(url)
    })

    await page.goto('/helixhr/')
    await page.waitForLoadState('networkidle')

    expect(remote, remote.join('\n')).toEqual([])
    // And the family is genuinely in use, so "no request" cannot be passing
    // because the font silently stopped loading.
    const family = await page
      .locator('h1')
      .first()
      .evaluate((node) => getComputedStyle(node).fontFamily)
    expect(family).toContain('Archivo')
    await context.close()
  })
})
