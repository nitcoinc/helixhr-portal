// Role x route x viewport capture for human UI review (plan 2026-10-17-001 U18).
//
//   cd frontend
//   BASE_URL=http://localhost:8000 node tests/audit-shots.mjs before
//   BASE_URL=http://localhost:8000 node tests/audit-shots.mjs after
//
// Signs in each Playwright identity over the API, asks the bootstrap what
// that identity may open, and captures every allowed route full-page at a
// desktop and a phone viewport. Output lands in .audit-shots/<label>/ at the repo
// root (gitignored; not test-results/, which every Playwright run wipes) with a
// log.tsv per run: final URL, page height, horizontal
// scroll and console errors per shot, so a before/after pair can be compared
// side by side.
//
// Like tests/screenshots.mjs this is deliberately not a Playwright spec: it
// asserts nothing (console-clean.spec.ts is the asserting counterpart) and
// must not run inside functional suites. The browser goes to the bench's
// default site; SITE_HOST only sets the Host header for the API login, the
// same as auth.setup.ts.
import { chromium, request } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const baseURL = process.env.BASE_URL || 'http://localhost:8000'
const SITE_HOST = process.env.SITE_HOST
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'
const label = process.argv[2] || 'latest'
const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../../.audit-shots', label)

const IDENTITIES = [
  'employee@helixhr.test',
  'manager@helixhr.test',
  'hr-manager-employee@helixhr.test',
  'hr-user@helixhr.test',
  'it-team@helixhr.test',
  'portal-admin@helixhr.test',
]

// [path, gate(bootstrap) -> bool]. The gates mirror the rail's own filter in
// AppShell.vue: a route the rail would not offer is not captured.
const linked = (b) => Boolean(b.employee)
const ROUTES = [
  ['/', () => true],
  ['/notifications', linked],
  ['/leave', linked],
  ['/timesheet', linked],
  ['/timesheet/history', linked],
  ['/my-projects', linked],
  ['/attendance', linked],
  ['/requests', linked],
  ['/requests?tab=work', (b) => b.can_handle_requests || b.can_work_requests],
  ['/payslips', linked],
  ['/holidays', linked],
  ['/documents', linked],
  ['/directory', linked],
  ['/profile', linked],
  ['/team', (b) => b.has_reports],
  ['/roster', (b) => linked(b) || b.can_roster_everyone],
  ['/approvals', (b) => b.can_approve || b.can_oversee_timesheets],
  ['/people', (b) => b.can_see_people],
  ['/reports', (b) => b.can_run_reports || b.can_admin_portal],
  ['/organisation', (b) => b.can_see_organisation],
  ['/projects', (b) => b.can_see_projects],
  ['/settings', (b) => b.can_configure || b.can_admin_portal],
  ['/email-templates', (b) => b.can_edit_email_templates],
]

const VIEWPORTS = {
  desk: { viewport: { width: 1440, height: 900 } },
  phone: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
}

const slug = (path) =>
  path === '/' ? 'home' : path.replace(/^\//, '').replace(/[/?=&]/g, '_')

async function signIn(user) {
  const context = await request.newContext({
    baseURL,
    ...(SITE_HOST ? { extraHTTPHeaders: { Host: SITE_HOST } } : {}),
  })
  const login = await context.post('/api/method/login', { form: { usr: user, pwd: PASSWORD } })
  if (!login.ok()) {
    await context.dispose()
    throw new Error(`Login failed for ${user}: ${login.status()}`)
  }
  const boot = await context.get('/api/method/helixhr.api.get_portal_bootstrap')
  const bootstrap = boot.ok() ? (await boot.json()).message || {} : {}
  const state = await context.storageState()
  await context.dispose()
  return { state, bootstrap }
}

await mkdir(outDir, { recursive: true })
const browser = await chromium.launch()
const log = ['identity\tviewport\tpath\tfinal\theight\thscroll\tconsole_errors']

for (const user of IDENTITIES) {
  const name = user.split('@')[0]
  let session
  try {
    session = await signIn(user)
  } catch (error) {
    log.push(`${name}\t-\t-\tLOGIN FAILED: ${error.message}\t\t\t`)
    continue
  }
  const paths = ROUTES.filter(([, gate]) => gate(session.bootstrap)).map(([path]) => path)
  for (const [viewportName, options] of Object.entries(VIEWPORTS)) {
    const context = await browser.newContext({
      ...options,
      deviceScaleFactor: 1,
      storageState: session.state,
    })
    const page = await context.newPage()
    const errors = []
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text().split('\n')[0].slice(0, 160))
    })
    page.on('pageerror', (error) => errors.push(String(error.message).split('\n')[0].slice(0, 160)))
    for (const path of paths) {
      errors.length = 0
      try {
        await page.goto(`${baseURL}/helixhr${path}`, { waitUntil: 'networkidle', timeout: 30000 })
        await page.waitForTimeout(600)
        await page.screenshot({
          path: resolve(outDir, `${name}__${viewportName}__${slug(path)}.png`),
          fullPage: true,
        })
        const metrics = await page.evaluate(() => ({
          height: document.documentElement.scrollHeight,
          hscroll: document.documentElement.scrollWidth > window.innerWidth,
        }))
        const final = page.url().replace(`${baseURL}/helixhr`, '') || '/'
        log.push(
          [name, viewportName, path, final, metrics.height, metrics.hscroll, errors.join(' | ')].join('\t'),
        )
      } catch (error) {
        log.push([name, viewportName, path, `ERROR: ${error.message.split('\n')[0]}`, '', '', ''].join('\t'))
      }
    }
    await context.close()
  }
}

await browser.close()
await writeFile(resolve(outDir, 'log.tsv'), `${log.join('\n')}\n`)
console.log(`${log.length - 1} rows -> ${outDir}`)
