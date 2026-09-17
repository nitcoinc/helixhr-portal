// Captures the README screenshots against a running bench.
//
//   cd frontend
//   BASE_URL=http://localhost:8000 SITE_HOST=test_site node tests/screenshots.mjs
//
// It reuses the Playwright storage states in `tests/.auth/`, so run the e2e
// suite's `setup` project (or the whole suite) first -- or just call
// `helixhr.tests.utils.setup_playwright_fixtures` and log the two identities
// in as `auth.setup.ts` does. Output goes to `docs/images/`, which is the only
// place the README reads images from.
//
// Deliberately not a Playwright spec: it mutates nothing, asserts nothing, and
// registering it as a project would put it inside every functional run.
import { chromium, devices, request } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const baseURL = process.env.BASE_URL || 'http://localhost:8000'
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../../docs/images')

const DESKTOP = { width: 1280, height: 860 }
const PHONE = devices['iPhone 14']

// [file, identity, path, viewport]
//
// `PERSON` is the Employee id the People shot opens. It is a fixture id, so
// it differs per site: pass it as PERSON_ID=HR-EMP-00001, or the People shot
// falls back to the search screen.
const PERSON = process.env.PERSON_ID
const SHOTS = [
  ['portal-dashboard.png', 'employee', '/helixhr/', DESKTOP],
  ['portal-timesheet.png', 'employee', '/helixhr/timesheet/2026-08-31', DESKTOP],
  ['portal-payslips.png', 'employee', '/helixhr/payslips', DESKTOP],
  ['portal-approvals.png', 'manager', '/helixhr/approvals', DESKTOP],
  ['portal-people.png', 'showcase-hr', PERSON ? `/helixhr/people/${PERSON}` : '/helixhr/people', DESKTOP],
  ['portal-reports.png', 'showcase-hr', '/helixhr/reports', DESKTOP],
  ['portal-attendance-mobile.png', 'employee', '/helixhr/attendance', PHONE],
  ['portal-leave-mobile.png', 'employee', '/helixhr/leave', PHONE],
]

// Regenerate a subset: `node tests/screenshots.mjs portal-people.png portal-reports.png`.
// The committed shots were taken on a hand-curated site (README, Screenshots);
// being able to refresh two without disturbing six is what keeps that true.
const only = process.argv.slice(2)
const selected = only.length ? SHOTS.filter(([file]) => only.includes(file)) : SHOTS

async function login() {
  const context = await request.newContext({
    baseURL,
    extraHTTPHeaders: { Host: SITE_HOST },
  })
  const states = {}
  for (const [key, user] of [
    ['employee', 'employee@helixhr.test'],
    ['manager', 'manager@helixhr.test'],
    // The HR shots sign in as the plainly named identity
    // `helixhr.tests.utils.ensure_showcase_fixtures` creates, not the test
    // suite's `hr-manager-employee@...` -- its name is what the sidebar shows.
    ['showcase-hr', 'ananya.rao@helixhr.test'],
  ]) {
    const response = await context.post('/api/method/login', {
      form: { usr: user, pwd: PASSWORD },
    })
    if (!response.ok()) {
      throw new Error(`Login failed for ${user}: ${response.status()} ${await response.text()}`)
    }
    states[key] = await context.storageState()
  }
  await context.dispose()
  return states
}

const states = await login()
await mkdir(outDir, { recursive: true })
const browser = await chromium.launch()

for (const [file, identity, path, viewport] of selected) {
  const context = await browser.newContext({
    ...(viewport === DESKTOP ? { viewport } : viewport),
    storageState: states[identity],
    baseURL,
    deviceScaleFactor: 2,
    colorScheme: 'light',
  })
  const page = await context.newPage()
  await page.goto(path, { waitUntil: 'networkidle' })
  // Every resource region renders an AsyncState first; waiting for the
  // loading text to go is what separates a real screen from a skeleton.
  await page.waitForFunction(() => !document.body.innerText.includes('Loading'), null, {
    timeout: 15000,
  })
  await page.waitForTimeout(600)
  // Crop the dead space under a short screen. A README image is judged on
  // how much of it is content, and every page here is shorter than the
  // viewport the nav rail wants.
  // Phone shots keep their whole frame: the bottom tab bar is the point.
  const clip = viewport !== DESKTOP ? null : await page.evaluate((minHeight) => {
    const main = document.querySelector('main')
    if (!main) return null
    let bottom = 0
    for (const el of main.querySelectorAll('*')) {
      const box = el.getBoundingClientRect()
      if (box.width > 0 && box.height > 0) bottom = Math.max(bottom, box.bottom)
    }
    let height = Math.min(
      window.innerHeight,
      Math.max(minHeight, Math.ceil(bottom) + 32),
    )
    // Land the cut between two nav items rather than through one: a rail
    // sliced across an icon is the thing that makes a screenshot look
    // careless.
    if (height < window.innerHeight) {
      for (const link of document.querySelectorAll('aside a')) {
        const box = link.getBoundingClientRect()
        if (box.top < height && box.bottom > height - 16) {
          height = Math.ceil(box.bottom) + 12
          break
        }
      }
      height = Math.min(window.innerHeight, height)
    }
    return { x: 0, y: 0, width: window.innerWidth, height }
  }, 380)
  await page.screenshot({ path: resolve(outDir, file), clip: clip || undefined })
  console.log(`${file}  ${identity}  ${path}`)
  await context.close()
}

await browser.close()
