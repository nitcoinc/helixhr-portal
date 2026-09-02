import { test as setup, request } from '@playwright/test'

// See docs/runbook.md: the two test identities and their password ("password"
// is rejected as a top-10-common-password by any site with the password
// policy on, so this must match helixhr.tests.utils.TEST_PASSWORD).
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

const IDENTITIES = [
  { user: 'employee@helixhr.test', storageState: 'tests/.auth/employee.json' },
  { user: 'manager@helixhr.test', storageState: 'tests/.auth/manager.json' },
]

setup('authenticate as employee and manager', async ({ baseURL }) => {
  const context = await request.newContext({
    baseURL,
    extraHTTPHeaders: { Host: SITE_HOST },
  })

  for (const { user, storageState } of IDENTITIES) {
    const response = await context.post('/api/method/login', {
      form: { usr: user, pwd: PASSWORD },
    })
    if (!response.ok()) {
      throw new Error(
        `Login failed for ${user}: ${response.status()} ${await response.text()}. ` +
          'Has helixhr.tests.utils.setup_playwright_fixtures been called on this site? See docs/runbook.md.',
      )
    }
    await context.storageState({ path: storageState })
  }

  await context.dispose()
})
