import { defineConfig, devices } from '@playwright/test'

// BASE_URL points at whichever site is under test (test_site for CI, a
// dev_site for a local run). See docs/runbook.md for how the site is
// created and how the two test identities get their password logins.
const baseURL = process.env.BASE_URL || 'http://localhost:8080'

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
    },
    {
      name: 'manager',
      use: { ...devices['Desktop Chrome'], storageState: 'tests/.auth/manager.json' },
      dependencies: ['setup'],
    },
  ],
})
