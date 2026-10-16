import { test, expect, Page } from '@playwright/test'

// P2-U9: the gates that are cheap enough to run on every pass.
//
// The pinned performance protocol lives in `performance.spec.ts` and is
// throttled to minutes; everything here is a yes/no property of the shipped
// build or of the running page, so it belongs in the ordinary suite where a
// regression is caught the day it lands rather than at the next baseline.
//
// Covers P2-U9 scenarios 2, 3, 4 and 5, the two accessibility items U3..U8
// left open, and the response headers step 8 asks for.

const ASSET_PREFIX = '/assets/helixhr/helixhr/assets/'

/** Every route the phone tab bar and More sheet can reach, with the chunk
 * name Vite gives its page component. */
const LAZY_ROUTES = [
  { path: '/helixhr/leave', chunk: 'Leave' },
  { path: '/helixhr/timesheet', chunk: 'Timesheet' },
  { path: '/helixhr/requests', chunk: 'Requests' },
  { path: '/helixhr/attendance', chunk: 'Attendance' },
  { path: '/helixhr/documents', chunk: 'Documents' },
  { path: '/helixhr/notifications', chunk: 'Notifications' },
  { path: '/helixhr/profile', chunk: 'Profile' },
  { path: '/helixhr/payslips', chunk: 'Payslips' },
  { path: '/helixhr/holidays', chunk: 'Holidays' },
  { path: '/helixhr/directory', chunk: 'Directory' },
  // Plan 2026-10-17-001 U1 (EP-24): /team now sends an employee with no
  // reports Home before its chunk loads, so My projects stands in as the
  // route this employee reaches by URL. navigation.spec.ts covers the
  // redirect itself.
  { path: '/helixhr/my-projects', chunk: 'MyProjects' },
]

function scriptUrls(page: Page) {
  const urls: string[] = []
  page.on('request', (request) => {
    if (request.resourceType() === 'script' || request.resourceType() === 'stylesheet') {
      urls.push(new URL(request.url()).pathname)
    }
  })
  return urls
}

test.describe('employee', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenarios')
  })

  // ── Scenario 2 ────────────────────────────────────────────────────────
  test('every primary route arrives as its own lazy chunk (P2-R21)', async ({ page }) => {
    const requested = scriptUrls(page)

    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.waitForLoadState('networkidle')

    const onLoad = [...requested]
    // No page component other than the Dashboard's may be in the initial
    // payload: that is the whole point of the router's dynamic imports.
    for (const route of LAZY_ROUTES) {
      expect(
        onLoad.some((url) => url.startsWith(`${ASSET_PREFIX}${route.chunk}-`)),
        `${route.chunk} was in the initial payload`,
      ).toBe(false)
    }

    for (const route of LAZY_ROUTES) {
      const before = requested.length
      await page.goto(route.path)
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await page.waitForLoadState('networkidle')
      const fetched = requested.slice(before)
      expect(
        fetched.some((url) => url.startsWith(`${ASSET_PREFIX}${route.chunk}-`)),
        `${route.path} did not fetch a ${route.chunk} chunk: ${fetched.join(', ')}`,
      ).toBe(true)
    }
  })

  // ── Scenario 3 ────────────────────────────────────────────────────────
  test('a hidden tab stops polling, and returning refreshes the count once', async ({ browser }) => {
    const context = await browser.newContext({ storageState: 'tests/.auth/employee.json' })
    // The page starts hidden, so the poll never starts. Overriding the
    // property is the only way to move `visibilityState` from a test:
    // Playwright can dispatch the event but cannot background a real tab.
    await context.addInitScript(() => {
      let state = 'hidden'
      Object.defineProperty(document, 'visibilityState', { get: () => state, configurable: true })
      Object.defineProperty(document, 'hidden', { get: () => state === 'hidden', configurable: true })
      ;(window as unknown as { __setVisibility: (next: string) => void }).__setVisibility = (next) => {
        state = next
        document.dispatchEvent(new Event('visibilitychange'))
        // Both events, on purpose: a phone returning from the app switcher
        // fires them together and must still cost one request.
        window.dispatchEvent(new Event('focus'))
      }
    })
    const page = await context.newPage()

    const counts: string[] = []
    page.on('request', (request) => {
      if (request.url().includes('frappe.client.get_count')) counts.push(request.url())
    })

    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.waitForLoadState('networkidle')

    // The bootstrap already carried the unread number, so a hidden page owes
    // the server nothing at all.
    expect(counts, counts.join('\n')).toEqual([])

    await page.evaluate(() =>
      (window as unknown as { __setVisibility: (next: string) => void }).__setVisibility('visible'),
    )
    await expect.poll(() => counts.length).toBe(1)

    // Going away and coming back is one more, not two, and no timer was
    // left behind by the first return.
    await page.evaluate(() =>
      (window as unknown as { __setVisibility: (next: string) => void }).__setVisibility('hidden'),
    )
    await page.evaluate(() =>
      (window as unknown as { __setVisibility: (next: string) => void }).__setVisibility('visible'),
    )
    await expect.poll(() => counts.length).toBe(2)
    await page.waitForTimeout(2_000)
    expect(counts.length).toBe(2)

    await context.close()
  })

  // ── Scenario 4 ────────────────────────────────────────────────────────
  test('the built assets expose no source map, no unhashed name and no mixed content', async ({
    page,
    request,
  }) => {
    const requested = scriptUrls(page)
    const insecure: string[] = []
    page.on('request', (r) => {
      if (r.url().startsWith('http://') && !r.url().includes('localhost') && !r.url().includes('127.0.0.1')) {
        insecure.push(r.url())
      }
    })

    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.waitForLoadState('networkidle')

    expect(insecure, insecure.join('\n')).toEqual([])
    expect(requested.filter((url) => url.endsWith('.map'))).toEqual([])

    const assets = requested.filter((url) => url.startsWith(ASSET_PREFIX))
    expect(assets.length, 'no built assets were served').toBeGreaterThan(0)
    for (const url of assets) {
      // Vite's content hash: `Name-8charhash.js`. An unhashed asset cannot
      // be cached immutably, which is the other half of P2-U9 step 3.
      expect(url, `${url} is not content-hashed`).toMatch(/-[A-Za-z0-9_-]{8}\.(js|css)$/)
    }

    // Chromium only fetches a source map with devtools open, so "the browser
    // did not ask for one" is not the same as "the host will not serve one".
    const entry = assets.find((url) => /\/index-[A-Za-z0-9_-]{8}\.js$/.test(url))
    expect(entry, `no entry chunk among ${assets.join(', ')}`).toBeTruthy()
    const map = await request.get(`${entry}.map`)
    expect(map.status(), 'a public source map is still being served').not.toBe(200)
  })

  // ── Scenario 5 ────────────────────────────────────────────────────────
  test('nothing personal is stored by a service worker or a shared cache', async ({ page }) => {
    const responses: { url: string; cacheControl: string | undefined }[] = []
    page.on('response', (response) => {
      const url = response.url()
      if (url.includes('/helixhr') || url.includes('/api/method/')) {
        responses.push({ url, cacheControl: response.headers()['cache-control'] })
      }
    })

    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.waitForLoadState('networkidle')

    const workers = await page.evaluate(async () =>
      'serviceWorker' in navigator ? (await navigator.serviceWorker.getRegistrations()).length : 0,
    )
    expect(workers, 'the portal registers no service worker by design').toBe(0)
    expect(await page.evaluate(async () => (await caches.keys()).length)).toBe(0)

    for (const { url, cacheControl } of responses) {
      if (url.includes('/assets/')) continue
      expect(cacheControl, `${url} has no Cache-Control`).toBeTruthy()
      expect(cacheControl?.toLowerCase(), `${url} is publicly cacheable`).not.toContain('public')
    }
  })

  // ── Step 8, as far as a test on this host can see it ───────────────────
  test('every response carries the security headers the app sets', async ({ page }) => {
    const response = await page.goto('/helixhr/')
    const headers = response!.headers()
    expect(headers['x-content-type-options']).toBe('nosniff')
    expect(headers['content-security-policy']).toContain("frame-ancestors 'none'")
    expect(headers['referrer-policy']).toBeTruthy()
    expect(headers['permissions-policy']).toBeTruthy()
    // HSTS is deliberately absent over plain HTTP -- see
    // helixhr/utils.py::set_security_headers. The HTTPS half is a host-only
    // sign-off (docs/runbook.md) and preflight's `check_public_endpoint`.
    expect(headers['strict-transport-security']).toBeUndefined()
  })

  // ── Accessibility items carried over from U3..U8 ───────────────────────
  test("a dialog's close control has an accessible name", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 })
    await page.goto('/helixhr/')
    await page.getByRole('button', { name: 'More' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'Close' })).toBeVisible()
    // And nothing inside it is left nameless.
    const nameless = await dialog.evaluate((node) =>
      [...node.querySelectorAll('button')].filter(
        (button) => !button.textContent?.trim() && !button.getAttribute('aria-label'),
      ).length,
    )
    expect(nameless).toBe(0)
  })

  test('a leave row link is a 44px target in its own right', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 })
    await page.goto('/helixhr/leave')
    await expect(page.getByRole('heading', { level: 1, name: 'Leave' })).toBeVisible()
    const links = page.locator('li a[href^="/helixhr/leave/"]')
    const count = await links.count()
    test.skip(count === 0, 'this fixture has no leave rows')
    for (let index = 0; index < count; index += 1) {
      const box = await links.nth(index).boundingBox()
      expect(box!.height, `leave row link ${index} is ${box!.height}px tall`).toBeGreaterThanOrEqual(44)
    }
  })
})

// Plan 2026-10-17-001 U14 (R4, KTD4). The toast UI is reka-ui's Toast, kept
// out of the eager chunk: AppShell fetches `Toaster-<hash>.js` on the first
// idle callback, or sooner if a message is queued first. The init script
// holds every idle callback until the test releases it, so "before idle" is
// a state the test can stand in rather than a race it hopes to win.
function holdIdleCallbacks() {
  const held: Array<() => void> = []
  const w = window as unknown as {
    requestIdleCallback: (callback: IdleRequestCallback) => number
    __runIdle: () => void
  }
  w.requestIdleCallback = (callback) => {
    held.push(() => callback({ didTimeout: false, timeRemaining: () => 50 } as IdleDeadline))
    return held.length
  }
  w.__runIdle = () => held.splice(0).forEach((run) => run())
}

const isToasterChunk = (url: string) => url.startsWith(`${ASSET_PREFIX}Toaster-`) && url.endsWith('.js')

test.describe('toaster loading', () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(!testInfo.project.name.startsWith('employee'), 'employee-only scenarios')
  })

  test('the cold load fetches no Toaster chunk before idle, and fetches it once after', async ({
    page,
  }) => {
    await page.addInitScript(holdIdleCallbacks)
    const requested = scriptUrls(page)

    await page.goto('/helixhr/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.waitForLoadState('networkidle')
    expect(requested.filter(isToasterChunk), 'the Toaster rode in with the cold load').toEqual([])
    await expect(page.locator('[data-toast-live]')).toHaveCount(0)

    await page.evaluate(() => (window as unknown as { __runIdle: () => void }).__runIdle())
    await expect.poll(() => requested.filter(isToasterChunk).length).toBe(1)
    // Mounted: both live regions now exist, empty, before any message.
    await expect(page.locator('[data-toast-live]')).toHaveCount(2)
    await page.waitForLoadState('networkidle')
    expect(requested.filter(isToasterChunk).length, 'fetched once').toBe(1)
  })
})

test.describe('toaster queue', () => {
  test.beforeEach(async ({}, testInfo) => {
    // Signs in as HR itself (the export menu is the first real caller); once
    // per run is enough.
    test.skip(testInfo.project.name !== 'employee', 'run once, as HR, from the employee project')
  })

  test('a message sent before the Toaster mounts is shown and announced once it does', async ({
    browser,
  }) => {
    const context = await browser.newContext({ storageState: 'tests/.auth/hr.json' })
    const page = await context.newPage()
    await page.addInitScript(holdIdleCallbacks)

    // Hold the chunk too, so the message provably lands in the queue first.
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const requested: string[] = []
    await page.route(/\/Toaster-[\w-]{8}\.js$/, async (route) => {
      requested.push(route.request().url())
      await held
      return route.continue()
    })
    // A real flow: Export answered "Queued" (stubbed; a large export is not a
    // fixture), which the export menu reports through `notify`.
    await page.route('**/api/method/helixhr.api.request_export', (route) =>
      route.fulfill({ json: { message: { status: 'Queued' } } }),
    )
    await page.route('**/api/method/helixhr.api.list_my_exports*', (route) =>
      route.fulfill({ json: { message: [] } }),
    )

    await page.goto('/helixhr/reports/leave_ledger?from_date=2000-01-01&to_date=2100-01-01')
    await expect(page.getByRole('heading', { name: 'Leave ledger', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Export', exact: true }).click()
    await page.getByRole('menuitem', { name: /CSV/ }).click()

    // The queued message asked for the chunk itself, idle still held.
    await expect.poll(() => requested.length).toBe(1)
    await expect(page.getByTestId('toast')).toHaveCount(0)

    release()
    const message = "This export is large, so it's being prepared."
    await expect(page.getByTestId('toast')).toContainText(message)
    await expect(page.locator('[data-toast-live="polite"]')).toContainText(message)
    await context.close()
  })
})
