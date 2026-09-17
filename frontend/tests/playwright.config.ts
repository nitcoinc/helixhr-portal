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
    // P4-U4. The HR queue's identity (P4-KTD8): an HR Manager with an Active
    // Employee record, who lands in the portal and works the HR half of the
    // Approvals queue. Scoped to `approvals.spec.ts` and (P5-U14)
    // `settings.spec.ts` on purpose -- this project exists for the HR
    // capability shape, and every other spec is written for the employee or
    // manager one, so a wider `testMatch` would run somebody else's flow
    // under the wrong hat and fight it for fixtures.
    {
      name: 'hr',
      use: { ...devices['Desktop Chrome'], storageState: 'tests/.auth/hr.json' },
      dependencies: ['setup'],
      // P5-U15: organisation.spec.ts joins this list -- HR is the actor
      // `get_organisation_view` is written for. P6-U5/U6: people.spec.ts
      // and reports.spec.ts join it the same way, for `resolve_admin_scope`.
      testMatch: /(approvals|settings|organisation|people|reports)\.spec\.ts/,
    },
    // P5-U11. The routed-worker identity: `IT Team`, portal-only
    // (desk_access: 0), which never reaches Desk and works only the requests
    // stamped to its role (P5-U5). Scoped to `approvals.spec.ts` for the same
    // reason `hr` is -- this project exists for the IT capability shape, and
    // every other spec is written for a different hat.
    //
    // P5-U15: also `organisation.spec.ts`, for one negative assertion --
    // IT Team works requests but is not HR, so the organisation view must
    // refuse it exactly as it refuses a plain employee. P6-U5/U6: same for
    // people.spec.ts and reports.spec.ts -- IT Team is a routed worker,
    // never an admin scope, and sees no Desk link anywhere.
    {
      name: 'it',
      use: { ...devices['Desktop Chrome'], storageState: 'tests/.auth/it.json' },
      dependencies: ['setup'],
      testMatch: /(approvals|organisation|people|reports)\.spec\.ts/,
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
