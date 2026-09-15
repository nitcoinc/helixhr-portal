import { test as setup, request } from '@playwright/test'

// See docs/runbook.md: the two test identities and their password ("password"
// is rejected as a top-10-common-password by any site with the password
// policy on, so this must match helixhr.tests.utils.TEST_PASSWORD).
const SITE_HOST = process.env.SITE_HOST || 'test_site'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'Helixhr-Test-Fixture-2026!'

const IDENTITIES = [
  { user: 'employee@helixhr.test', storageState: 'tests/.auth/employee.json' },
  { user: 'manager@helixhr.test', storageState: 'tests/.auth/manager.json' },
  // P4-KTD8 / P4-R10. The HR queue's own identity: an HR Manager who *has* an
  // Active Employee record, seeded by `make_test_hr_manager_employee`. Its
  // sibling `ensure_hr_manager_user` deliberately has no Employee, so
  // `portal_home_page` refuses it and every portal read throws for it -- it
  // proves the role alone reaches Desk and nothing else, and cannot sign in
  // here.
  { user: 'hr-manager-employee@helixhr.test', storageState: 'tests/.auth/hr.json' },
  // P5-U11. The fourth identity: `IT Team`, portal-only (desk_access: 0,
  // P5-KTD10), seeded by `make_test_it_user` with an Active Employee record
  // of its own -- routed-request scoping (P5-U5) needs a real employee to
  // resolve a company from, the same as every other identity here.
  { user: 'it-team@helixhr.test', storageState: 'tests/.auth/it.json' },
]

setup('authenticate as employee, manager, HR and IT', async ({ baseURL }) => {
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
