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
