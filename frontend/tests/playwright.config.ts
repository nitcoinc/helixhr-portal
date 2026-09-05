import { defineConfig, devices } from '@playwright/test'

// BASE_URL points at whichever site is under test (test_site for CI, a
// dev_site for a local run). See docs/runbook.md for how the site is
// created and how the two test identities get their password logins.
const baseURL = process.env.BASE_URL || 'http://localhost:8080'

// P2-U0: the quality baseline is opt-in. Registering its project
// unconditionally would put a hard-throttled multi-minute run inside every
// functional and release pass, so BASELINE_MODE both selects the protocol
// (full vs lightweight) and decides whether the project exists at all.
const baselineMode = process.env.BASELINE_MODE

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: 'line',
  use: {
    baseURL,
    // No Host header override here: setting one via extraHTTPHeaders on a
    // real *browser* page breaks Chromium page.goto() (CDP rejects the
    // navigation with net::ERR_INVALID_ARGUMENT -- confirmed by testing a
    // bare Playwright script with and without it). auth.setup.ts still
    // sets Host explicitly on its own request.newContext() for login,
    // which is an API-only context and unaffected by this.
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'employee',
      use: { ...devices['Desktop Chrome'], storageState: 'tests/.auth/employee.json' },
      dependencies: ['setup'],
      testIgnore: /performance\.spec\.ts/,
    },
    {
      name: 'manager',
      use: { ...devices['Desktop Chrome'], storageState: 'tests/.auth/manager.json' },
      dependencies: ['setup'],
      testIgnore: /performance\.spec\.ts/,
    },
    // P2-U9 step 9. Mobile WebKit is the second mandatory browser: it is the
    // only engine on iOS, it is where a coarse pointer, a real safe-area
    // inset and Safari's own overlay behaviour actually live, and every
    // mobile-shaped defect this plan fixed was reasoned about on it. Scoped
    // to the critical employee flows rather than the whole suite -- the
    // desktop Chromium projects already cover the rest, and a second full
    // pass would double the release run for no new information.
    //
    // It needs the WebKit build and its system libraries:
    //   npx playwright install --with-deps webkit
    // A host that cannot install those (this repo's dev VM is one -- the
    // browser downloads but libevent and friends need root) cannot run this
    // project; CI installs them, and docs/runbook.md records it.
    {
      // The `employee-` prefix is load-bearing: every employee-scoped spec
      // gates on `testInfo.project.name.startsWith('employee')`, so this
      // project runs them and the manager-only and run-once ones stay out.
      name: 'employee-mobile-webkit',
      use: { ...devices['iPhone 14'], storageState: 'tests/.auth/employee.json' },
      dependencies: ['setup'],
      // Read-shaped critical flows only. The data-mutating specs (leave,
      // timesheet entry and approval, requests) are single-run-per-site by
      // design -- one of them signs a session out and another consumes a
      // leave allocation -- so a second pass in the same run would fight the
      // first rather than test a second engine.
      testMatch: /(login-dashboard|navigation|visual-foundation|hardening)\.spec\.ts/,
    },
    // P2-U0: the pinned quality baseline, present only when BASELINE_MODE
    // is set. The spec owns the viewport, CPU and network profile itself (a
    // browser context created inside a test does not inherit `use`), so
    // nothing device-shaped is set here.
    ...(baselineMode
      ? [
          {
            name: 'baseline',
            testMatch: /performance\.spec\.ts/,
            use: { ...devices['Desktop Chrome'], storageState: 'tests/.auth/employee.json' },
            dependencies: ['setup'],
            retries: 0,
            fullyParallel: false,
          },
        ]
      : []),
  ],
})
