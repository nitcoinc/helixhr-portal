import { test, expect, request } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// P2-U0: the frozen quality baseline. This file is the measurement
// protocol, not a functional test -- it exists so P2-R21..P2-R24 can be
// argued from one reproducible number instead of an estimate, and so every
// later unit can re-run the same protocol cheaply.
//
// Run it (see docs/runbook.md for the whole procedure):
//   BASELINE_MODE=full BASE_URL=http://localhost:8000 SITE_HOST=test_site \
//     yarn test:e2e -- --project=baseline
//
// It refuses to produce a number it cannot trust: a failed resource, a
// console error, a missing metric, a stale build, a fixture-count mismatch
// or an environment that differs from the pinned one marks the run INVALID
// and fails the test (P2-U0 scenario 3). A quieter measurement is never
// silently accepted as an improvement.

const LIGHTWEIGHT = process.env.BASELINE_MODE === 'lightweight'
const COLD_LOADS = LIGHTWEIGHT ? 3 : 10
const INTERACTIONS = LIGHTWEIGHT ? 6 : 20

// The pinned device profile. Changing any of these numbers invalidates
// every comparison against an earlier result, so they are constants here
// and are copied verbatim into each result file's manifest.
const PROFILE = {
  viewport: { width: 360, height: 800 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  cpuThrottlingRate: 4,
  network: {
    // 1.6 Mbps down / 750 Kbps up / 150 ms RTT, in the bytes-per-second
    // units Network.emulateNetworkConditions expects.
    latencyMs: 150,
    downloadThroughput: (1.6 * 1000 * 1000) / 8,
    uploadThroughput: (750 * 1000) / 8,
  },
  percentile: 75,
  percentileMethod: 'nearest-rank on the sorted cold-run samples',
}

// import.meta.dirname, not __dirname: package.json sets "type": "module",
// so this file is loaded as ESM and __dirname does not exist.
const HERE = import.meta.dirname
const REPO_ROOT = path.resolve(HERE, '..', '..', '..')
const OUT_DIR = path.join(REPO_ROOT, '.impeccable', 'review', 'baseline')
const PIN_FILE = path.join(OUT_DIR, 'environment-pin.json')
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const STORAGE_STATE = path.join(HERE, '..', '.auth', 'employee.json')

type Sample = {
  index: number
  cache: 'cold' | 'warm'
  lcpMs: number | null
  clsScore: number | null
  requests: number
  apiRequests: number
  transferredBytes: number
  jsBytes: number
  cssBytes: number
  fontRequests: string[]
  sourceMapRequests: string[]
  // What the host actually served the code as. The bench dev server does
  // not compress, a real proxy does, so a transfer number is only
  // comparable against another run with the same encoding (P2-R24).
  assetEncodings: Record<string, number>
  apiDurationsMs: Record<string, number[]>
}

const problems: string[] = []
const invalidate = (why: string) => {
  if (!problems.includes(why)) problems.push(why)
}

function percentile(values: number[], p: number): number | null {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b)
  if (!sorted.length) return null
  // Nearest-rank: the smallest value at or above the p-th percentile
  // position. Stated in the manifest so a later run cannot use a different
  // definition and call it the same metric.
  const rank = Math.max(1, Math.ceil((p / 100) * sorted.length))
  return sorted[rank - 1]
}

/** Response headers arrive with whatever casing the server used. */
function headerValue(headers: Record<string, string>, name: string) {
  const match = Object.keys(headers || {}).find((key) => key.toLowerCase() === name)
  return match ? headers[match] : null
}

function sum(values: number[]) {
  return values.reduce((total, value) => total + value, 0)
}

function git(...args: string[]) {
  try {
    return execFileSync('git', args, { cwd: REPO_ROOT, encoding: 'utf8' }).trim()
  } catch {
    return 'unknown'
  }
}

// The observer has to be installed before any application code runs, so it
// goes in via addInitScript. Nothing here reads page content: only timings,
// so the artifact cannot pick up a personal value (P2-U0 scenario 4).
const METRICS_INIT = `
  window.__p2u0 = { lcp: null, cls: 0, interactions: [], observers: [] }
  const track = (type, handle, options) => {
    try {
      const observer = new PerformanceObserver((list) => list.getEntries().forEach(handle))
      observer.observe(Object.assign({ type, buffered: true }, options || {}))
      window.__p2u0.observers.push(type)
    } catch (error) {
      /* unsupported entry type: reported as a missing metric, never as a zero */
    }
  }
  track('largest-contentful-paint', (entry) => {
    window.__p2u0.lcp = entry.startTime
  })
  track('layout-shift', (entry) => {
    if (!entry.hadRecentInput) window.__p2u0.cls += entry.value
  })
  track('event', (entry) => {
    window.__p2u0.interactions.push({
      name: entry.name,
      startTime: entry.startTime,
      duration: entry.duration,
    })
  }, { durationThreshold: 16 })
`

/** Everything the harness needs from one page: metrics plus the guarantee
 * that the observers it depends on actually attached. */
async function readMetrics(page) {
  return page.evaluate(() => {
    const state = window.__p2u0 || {}
    return {
      lcp: state.lcp ?? null,
      cls: state.cls ?? null,
      interactions: state.interactions || [],
      observers: state.observers || [],
    }
  })
}

function builtBundle() {
  const shell = path.join(REPO_ROOT, 'helixhr', 'www', 'helixhr.html')
  const assetDir = path.join(REPO_ROOT, 'helixhr', 'public', 'helixhr', 'assets')
  if (!fs.existsSync(shell) || !fs.existsSync(assetDir)) {
    invalidate('no production build found: run `yarn build` before measuring')
    return null
  }
  const html = fs.readFileSync(shell, 'utf8')
  const entry = html.match(/assets\/(index-[^"']+\.js)/)?.[1] || null
  if (!entry) {
    invalidate('could not find the entry chunk in helixhr/www/helixhr.html')
    return null
  }
  const files = fs.readdirSync(assetDir)
  // A source newer than the built entry chunk means the served build is not
  // the code in the working tree -- the one environment mismatch that would
  // otherwise be invisible in the numbers.
  const builtAt = fs.statSync(path.join(assetDir, entry)).mtimeMs
  const newest = newestSourceMtime(path.join(REPO_ROOT, 'frontend', 'src'))
  if (newest > builtAt) invalidate('stale production build: frontend/src is newer than the built entry chunk')
  return {
    entry,
    builtAt: new Date(builtAt).toISOString(),
    sourceMapsOnDisk: files.filter((file) => file.endsWith('.map')).length,
    onDiskBytes: Object.fromEntries(
      ['.js', '.css'].map((extension) => [
        extension,
        sum(
          files
            .filter((file) => file.endsWith(extension))
            .map((file) => fs.statSync(path.join(assetDir, file)).size),
        ),
      ]),
    ),
  }
}

function newestSourceMtime(dir: string): number {
  let newest = 0
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    newest = Math.max(newest, entry.isDirectory() ? newestSourceMtime(full) : fs.statSync(full).mtimeMs)
  }
  return newest
}

/** URL reduced to origin + pathname. Query strings are dropped before
 * anything is written to disk: the artifact records what was fetched and
 * how big it was, never a record name or a filter value (P2-U0 scenario 4). */
function scrub(url: string) {
  try {
    const parsed = new URL(url)
    return `${parsed.origin}${parsed.pathname}`
  } catch {
    return url.split('?')[0]
  }
}

async function measureLoad(browser, baseURL: string, index: number, cache: 'cold' | 'warm', screenshot?: string) {
  // A brand-new context per iteration is what makes a load cold: contexts
  // do not share the HTTP cache. The declared warm run reuses one.
  const context = await browser.newContext({
    baseURL,
    storageState: STORAGE_STATE,
    viewport: PROFILE.viewport,
    deviceScaleFactor: PROFILE.deviceScaleFactor,
    isMobile: PROFILE.isMobile,
    hasTouch: PROFILE.hasTouch,
  })
  await context.addInitScript(METRICS_INIT)
  if (cache === 'warm') {
    // Warm means the browser cache is populated, which is a property of the
    // context, not of the URL -- so prime it with a throwaway page here and
    // measure the second load below. A fresh context per iteration is what
    // makes every other run genuinely cold.
    const primer = await context.newPage()
    await primer.goto('/helixhr', { waitUntil: 'load' })
    await primer.waitForLoadState('networkidle')
    await primer.close()
  }
  const page = await context.newPage()

  const seen = new Map<
    string,
    { url: string; type: string; requestTime: number | null; encoding: string }
  >()
  const sample: Sample = {
    index,
    cache,
    lcpMs: null,
    clsScore: null,
    requests: 0,
    apiRequests: 0,
    transferredBytes: 0,
    jsBytes: 0,
    cssBytes: 0,
    fontRequests: [],
    sourceMapRequests: [],
    assetEncodings: {},
    apiDurationsMs: {},
  }

  page.on('console', (message) => {
    if (message.type() === 'error') invalidate(`console error during ${cache} load ${index}: ${message.text()}`)
  })
  page.on('pageerror', (error) => invalidate(`page error during ${cache} load ${index}: ${error.message}`))
  page.on('requestfailed', (failed) =>
    invalidate(`request failed during ${cache} load ${index}: ${scrub(failed.url())}`),
  )
  page.on('response', (response) => {
    if (response.status() >= 400) {
      invalidate(`HTTP ${response.status()} during ${cache} load ${index}: ${scrub(response.url())}`)
    }
  })

  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.emulateNetworkConditions', {
    offline: false,
    latency: PROFILE.network.latencyMs,
    downloadThroughput: PROFILE.network.downloadThroughput,
    uploadThroughput: PROFILE.network.uploadThroughput,
  })
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: PROFILE.cpuThrottlingRate })

  cdp.on('Network.responseReceived', (event) => {
    seen.set(event.requestId, {
      url: scrub(event.response.url),
      type: event.type,
      // requestTime is a monotonic clock in seconds, shared with
      // loadingFinished's timestamp -- the pair is the whole server+transfer
      // duration of the call, which is the "custom API duration" P2-R21 and
      // P2-R22 are argued from.
      requestTime: event.response.timing?.requestTime ?? null,
      encoding: headerValue(event.response.headers, 'content-encoding') || 'identity',
    })
  })
  // encodedDataLength is bytes on the wire -- gzip included, which is the
  // number P2-R24's budget is written against.
  cdp.on('Network.loadingFinished', (event) => {
    const info = seen.get(event.requestId)
    if (!info) return
    const bytes = event.encodedDataLength || 0
    sample.requests += 1
    sample.transferredBytes += bytes
    if (info.url.endsWith('.js') || info.url.endsWith('.css')) {
      sample.assetEncodings[info.encoding] = (sample.assetEncodings[info.encoding] || 0) + 1
    }
    if (info.url.endsWith('.js')) sample.jsBytes += bytes
    if (info.url.endsWith('.css')) sample.cssBytes += bytes
    if (info.url.endsWith('.map')) sample.sourceMapRequests.push(info.url)
    if (/fonts\.(googleapis|gstatic)\.com/.test(info.url)) sample.fontRequests.push(info.url)
    if (info.url.includes('/api/method/')) {
      sample.apiRequests += 1
      if (info.requestTime) {
        const method = info.url.split('/api/method/')[1]
        const duration = (event.timestamp - info.requestTime) * 1000
        if (duration >= 0) (sample.apiDurationsMs[method] ||= []).push(Math.round(duration))
      }
    }
  })

  await page.goto('/helixhr', { waitUntil: 'load' })
  // The dashboard's own data, not just the shell: the week spine is the
  // last thing get_dashboard fills in.
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 60_000 })
  await page.waitForLoadState('networkidle')
  // LCP is only final once nothing more paints; a short settle is cheaper
  // and more stable than racing the observer.
  await page.waitForTimeout(1_000)

  const metrics = await readMetrics(page)
  for (const required of ['largest-contentful-paint', 'layout-shift', 'event']) {
    if (!metrics.observers.includes(required)) invalidate(`browser did not support the ${required} metric`)
  }
  sample.lcpMs = metrics.lcp === null ? null : Math.round(metrics.lcp)
  sample.clsScore = metrics.cls === null ? null : Number(metrics.cls.toFixed(4))
  if (sample.lcpMs === null) invalidate(`no LCP recorded on ${cache} load ${index}`)

  if (screenshot) {
    fs.mkdirSync(OUT_DIR, { recursive: true })
    await page.screenshot({ path: path.join(OUT_DIR, screenshot), fullPage: true })
  }

  await context.close()
  return sample
}

async function measureInteractions(browser, baseURL: string, resultId: string) {
  const context = await browser.newContext({
    baseURL,
    storageState: STORAGE_STATE,
    viewport: PROFILE.viewport,
    deviceScaleFactor: PROFILE.deviceScaleFactor,
    isMobile: PROFILE.isMobile,
    hasTouch: PROFILE.hasTouch,
  })
  await context.addInitScript(METRICS_INIT)
  const page = await context.newPage()
  page.on('console', (message) => {
    if (message.type() === 'error') invalidate(`console error during interactions: ${message.text()}`)
  })
  page.on('pageerror', (error) => invalidate(`page error during interactions: ${error.message}`))
  page.on('response', (response) => {
    if (response.status() >= 400) {
      invalidate(`HTTP ${response.status()} during interactions: ${scrub(response.url())}`)
    }
  })

  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.emulateNetworkConditions', {
    offline: false,
    latency: PROFILE.network.latencyMs,
    downloadThroughput: PROFILE.network.downloadThroughput,
    uploadThroughput: PROFILE.network.uploadThroughput,
  })
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: PROFILE.cpuThrottlingRate })

  await page.goto('/helixhr', { waitUntil: 'load' })
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 60_000 })

  const tabBar = page.getByRole('navigation', { name: 'Main' })
  // The pinned interaction script: the four phone tab destinations, the
  // More sheet, one destination behind it, and back Home. Repeated until
  // INTERACTIONS interactions have been measured.
  const script = [
    { label: 'tab:Leave', open: () => tabBar.getByRole('link', { name: 'Leave' }).click(), settled: 'Leave' },
    {
      label: 'tab:Timesheet',
      open: () => tabBar.getByRole('link', { name: 'Timesheet' }).click(),
      settled: 'Timesheet',
    },
    {
      label: 'tab:Requests',
      open: () => tabBar.getByRole('link', { name: 'Requests' }).click(),
      settled: 'Requests',
    },
    {
      label: 'sheet:More',
      open: () => page.getByRole('button', { name: 'More' }).click(),
      settledSheet: 'More',
    },
    {
      label: 'sheet:Attendance',
      open: () => page.getByRole('dialog').getByRole('link', { name: 'Attendance' }).click(),
      settled: 'Attendance',
    },
    {
      // The Dashboard's h1 is the employee's own name, so this step settles
      // on the route plus "some h1" rather than on a fixture's name.
      label: 'tab:Home',
      open: () => tabBar.getByRole('link', { name: 'Home' }).click(),
      settledUrl: /\/helixhr\/?$/,
    },
  ]

  const measured: { index: number; step: string; latencyMs: number | null }[] = []
  for (let index = 0; index < INTERACTIONS; index += 1) {
    const step = script[index % script.length]
    const marker = await page.evaluate(() => performance.now())
    await step.open()
    if (step.settledSheet) {
      await expect(page.getByRole('dialog')).toBeVisible({ timeout: 30_000 })
    } else if (step.settledUrl) {
      await expect(page).toHaveURL(step.settledUrl, { timeout: 30_000 })
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 30_000 })
    } else {
      await expect(page.getByRole('heading', { level: 1, name: step.settled })).toBeVisible({
        timeout: 30_000,
      })
    }
    // Two frames after the transition, so the event-timing entry for this
    // interaction has been delivered before it is read.
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    )
    const metrics = await readMetrics(page)
    const durations = metrics.interactions
      .filter((entry) => entry.startTime >= marker)
      .map((entry) => entry.duration)
    if (!durations.length) {
      // No event-timing entry at all is a missing metric, not a fast
      // interaction -- record it as null and invalidate the run.
      invalidate(`no event-timing entry for interaction ${index} (${step.label})`)
      measured.push({ index, step: step.label, latencyMs: null })
    } else {
      measured.push({ index, step: step.label, latencyMs: Math.round(Math.max(...durations)) })
    }
  }

  fs.mkdirSync(OUT_DIR, { recursive: true })
  await page.screenshot({ path: path.join(OUT_DIR, `${resultId}-interactions-end-360.png`), fullPage: true })
  const cls = (await readMetrics(page)).cls
  await context.close()
  return { measured, clsAfterInteractions: cls === null ? null : Number(cls.toFixed(4)) }
}

test.describe('P2-U0 quality baseline', () => {
  test.describe.configure({ mode: 'serial', retries: 0 })

  test('capture the pinned production-build mobile profile', async ({ browser, baseURL }) => {
    test.setTimeout(LIGHTWEIGHT ? 15 * 60_000 : 60 * 60_000)
    const url = baseURL || 'http://localhost:8000'

    // 1. Environment and fixtures, before anything is measured.
    const bundle = builtBundle()
    const api = await request.newContext({ baseURL: url, extraHTTPHeaders: { Host: SITE_HOST } })
    const countsResponse = await api.get(
      '/api/method/helixhr.tests.utils.baseline_fixture_counts',
      { headers: { Cookie: cookieHeader() } },
    )
    let counts: any = null
    if (!countsResponse.ok()) {
      invalidate(`could not read baseline fixture counts: HTTP ${countsResponse.status()}`)
    } else {
      counts = (await countsResponse.json()).message
      for (const [key, expectedValue] of Object.entries(counts.expected)) {
        if (counts.actual[key] !== expectedValue) {
          invalidate(`fixture count mismatch for ${key}: ${counts.actual[key]} != ${expectedValue}`)
        }
      }
    }
    // Whether a source map is actually *served* -- Chromium only fetches
    // one with devtools open, so a browser capture cannot answer P2-R24's
    // "no public source map" clause on its own.
    let sourceMapsPublic: boolean | null = null
    if (bundle) {
      const mapResponse = await api.get(`/assets/helixhr/helixhr/assets/${bundle.entry}.map`)
      sourceMapsPublic = mapResponse.status() === 200
    }
    await api.dispose()

    const commit = git('rev-parse', 'HEAD')
    // Minute-stamped, so an invalidated run can never overwrite the result
    // file a valid one wrote -- this identifier is what P2-R21..P2-R24 cite.
    const resultId = [
      'P2-U0',
      LIGHTWEIGHT ? 'light' : 'full',
      new Date().toISOString().slice(0, 16).replace(/[-:]/g, ''),
      commit.slice(0, 7),
    ].join('-')

    const manifest = {
      result_id: resultId,
      captured_at: new Date().toISOString(),
      mode: LIGHTWEIGHT ? 'lightweight' : 'full',
      cold_loads: COLD_LOADS,
      interactions: INTERACTIONS,
      git: { commit, branch: git('rev-parse', '--abbrev-ref', 'HEAD'), dirty: git('status', '--porcelain') !== '' },
      build: bundle,
      browser: { name: browser.browserType().name(), version: browser.version() },
      runner: { node: process.version, platform: `${os.platform()} ${os.release()}`, arch: os.arch() },
      site: { base_url: url, host: SITE_HOST },
      profile: PROFILE,
      fixtures: counts ? { anchor_date: counts.anchor_date, counts: counts.actual } : null,
    }
    checkPin(manifest)

    // 2. Ten cold Dashboard loads, then one explicitly labelled warm load.
    const cold: Sample[] = []
    for (let index = 0; index < COLD_LOADS; index += 1) {
      cold.push(
        await measureLoad(browser, url, index, 'cold', index === 0 ? `${resultId}-dashboard-cold-360.png` : undefined),
      )
    }
    // Labelled separately and deliberately excluded from every acceptance
    // number below (P2-U0 scenario 2).
    const warm = await measureLoad(browser, url, 0, 'warm')

    // 3. The scripted interactions.
    const { measured, clsAfterInteractions } = await measureInteractions(browser, url, resultId)

    // Per-method p75 over every cold-run call to that method.
    const apiDurations: Record<string, number | null> = {}
    for (const method of new Set(cold.flatMap((sample) => Object.keys(sample.apiDurationsMs)))) {
      apiDurations[method] = percentile(
        cold.flatMap((sample) => sample.apiDurationsMs[method] || []),
        PROFILE.percentile,
      )
    }
    if (!Object.keys(apiDurations).length) invalidate('no API call durations recorded on any cold load')

    const results = {
      ...manifest,
      status: problems.length ? 'invalid' : 'valid',
      invalidated_by: problems,
      cold_run_p75: {
        lcp_ms: percentile(cold.map((sample) => sample.lcpMs).filter((value): value is number => value !== null), PROFILE.percentile),
        cls: percentile(cold.map((sample) => sample.clsScore).filter((value): value is number => value !== null), PROFILE.percentile),
        requests: percentile(cold.map((sample) => sample.requests), PROFILE.percentile),
        api_requests: percentile(cold.map((sample) => sample.apiRequests), PROFILE.percentile),
        transferred_bytes: percentile(cold.map((sample) => sample.transferredBytes), PROFILE.percentile),
        js_bytes: percentile(cold.map((sample) => sample.jsBytes), PROFILE.percentile),
        css_bytes: percentile(cold.map((sample) => sample.cssBytes), PROFILE.percentile),
      },
      interaction_p75_ms: percentile(
        measured.map((entry) => entry.latencyMs).filter((value): value is number => value !== null),
        PROFILE.percentile,
      ),
      // Cumulative for the whole interaction session (load included), so it
      // is reported next to -- never instead of -- the cold-load CLS.
      cls_after_interactions: clsAfterInteractions,
      api_durations_p75_ms: apiDurations,
      source_maps_public: sourceMapsPublic,
      asset_content_encoding: Object.fromEntries(
        cold.flatMap((sample) => Object.entries(sample.assetEncodings)),
      ),
      remote_font_requests: [...new Set(cold.flatMap((sample) => sample.fontRequests))],
      source_map_requests: [...new Set(cold.flatMap((sample) => sample.sourceMapRequests))],
      cold_samples: cold,
      warm_sample: warm,
      interaction_samples: measured,
    }

    fs.mkdirSync(OUT_DIR, { recursive: true })
    const file = path.join(OUT_DIR, `${resultId}.json`)
    fs.writeFileSync(file, `${JSON.stringify(results, null, 2)}\n`)
    // eslint-disable-next-line no-console
    console.log(`baseline ${results.status}: ${file}`)

    expect(problems, `run invalidated:\n- ${problems.join('\n- ')}`).toEqual([])
  })
})

/** The employee session, as a Cookie header, for the fixture-count read.
 * Taken from the same storageState the browser contexts use so the harness
 * never carries a second credential -- and nothing from it is written to
 * the result file. */
function cookieHeader() {
  if (!fs.existsSync(STORAGE_STATE)) {
    invalidate('no employee storageState: run the setup project (auth.setup.ts) first')
    return ''
  }
  const state = JSON.parse(fs.readFileSync(STORAGE_STATE, 'utf8'))
  return (state.cookies || []).map((cookie) => `${cookie.name}=${cookie.value}`).join('; ')
}

/** Freeze the environment on the first run in this directory and compare
 * every later run against it: a different browser version, viewport,
 * throttling profile or fixture anchor invalidates the result instead of
 * quietly producing a number that is not comparable (P2-U0 scenario 3). */
function checkPin(manifest) {
  const pinned = {
    browser: manifest.browser,
    profile: manifest.profile,
    site: manifest.site,
    fixture_anchor: manifest.fixtures?.anchor_date ?? null,
    fixture_counts: manifest.fixtures?.counts ?? null,
  }
  fs.mkdirSync(OUT_DIR, { recursive: true })
  if (!fs.existsSync(PIN_FILE)) {
    fs.writeFileSync(PIN_FILE, `${JSON.stringify(pinned, null, 2)}\n`)
    return
  }
  const existing = JSON.parse(fs.readFileSync(PIN_FILE, 'utf8'))
  for (const key of Object.keys(pinned)) {
    if (JSON.stringify(existing[key]) !== JSON.stringify(pinned[key])) {
      invalidate(
        `environment differs from ${path.relative(REPO_ROOT, PIN_FILE)} (${key}): ` +
          'measure on the pinned environment, or delete the pin deliberately and re-baseline',
      )
    }
  }
}
