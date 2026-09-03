# HelixHR Employee Portal

A Frappe v16 app that gives employees a plain, mobile-first portal for leave,
attendance, timesheets, HR requests, documents, notifications and profile, plus
one Approvals page for managers. Frappe HR stays the only source of truth; HR
keeps working in Frappe Desk. The portal is served at `/helixhr` on the same
site as ERPNext and HRMS.

- What it is and is not: [PRODUCT.md](PRODUCT.md)
- How it is put together: [docs/architecture.md](docs/architecture.md)
- Operating it, and every hard-won gotcha: [docs/runbook.md](docs/runbook.md)
- Visual system, copy rules and measured contrast: [docs/design-system.md](docs/design-system.md)
- The phase 1 plan the code was built from: [docs/plans/](docs/plans/)

## Repository layout

```
helixhr/                 Frappe app (Python)
  api.py                 every whitelisted method the frontend calls
  events.py              Timesheet and File document-event hooks
  preflight.py           go-live checks: bench --site <site> execute helixhr.preflight.run
  utils.py               week bounds, manager lookup, per-user rate limit
  hooks.py               fixtures, doc_events, the /helixhr/* route rule
  fixtures/              Property Setters, Custom DocPerms, Workflow, Notifications
  helixhr/doctype/       HR Request, HelixHR Document Link
  www/helixhr.py         serves the built SPA, injects CSRF token and site config
  tests/                 Python integration tests (bench run-tests)
frontend/                Vue 3 + frappe-ui + Tailwind, built by Vite
  src/pages/             one file per screen
  src/components/        AppShell, WeekSpine, NeedsYou, forms
  src/lib/               api client, session, dates, error mapping
  tests/e2e/             Playwright specs (real browser, real site)
docs/                    runbook, architecture, design system, plans
```

The frontend build writes into `helixhr/public/helixhr/` and
`helixhr/www/helixhr.html`. Both are gitignored: build them on the bench, never
commit them.

## Requirements

- A Frappe bench on `version-16` with `erpnext` and `hrms` installed.
- Python 3.14 (what `pyproject.toml` and CI pin), Node 24, Yarn 1.
- MariaDB 11.x and Redis, as any bench.

## Install on a bench

```bash
cd frappe-bench
bench get-app https://github.com/nitcoinc/helixhr-portal --branch main
cd apps/helixhr/frontend && yarn install --frozen-lockfile && yarn build && cd -
bench --site <site> install-app helixhr
bench build --app helixhr          # links sites/assets/helixhr; needed once per bench
bench --site <site> clear-cache
```

`bench get-app` runs the pip install. If you copy the folder in by hand instead,
also run `uv pip install -e apps/helixhr --python env/bin/python`, otherwise
`install-app` fails with `No module named 'helixhr'`. Details and the container
caveats are in the runbook.

## Configure a site

All configuration is per-site data, not code. Set it in Desk or with
`bench set-config`, then run the preflight to confirm.

| Setting | Where | Value |
|---|---|---|
| Apply Strict User Permissions | System Settings | on. Without it a User Permission on Employee does not restrict linked doctypes. |
| Disable Signup | Website Settings | on. An unknown sign-in must see "contact HR", not self-register. |
| Disable Username/Password Login | System Settings | **off** while sign-in is local. Turn on only once an Entra ID Social Login Key is enabled. |
| Enable Password Policy | System Settings | on, for local login. |
| Allowed File Extensions, Max File Size | System Settings | set; the app does not constrain uploads itself. |
| `rate_limit` | `bench --site <site> set-config rate_limit '{"limit": 600, "window": 60}'` | site-wide request limit, in addition to the app's per-user write limits. |
| `helixhr_hr_contact` | `bench --site <site> set-config helixhr_hr_contact hr@example.com` | the address shown to a signed-in user with no Employee record. Unset shows "Contact HR" with no link. |
| Documents page content | Desk: HelixHR Document Link | one record per link; no code change to add one. |

Site config is cached for 60 seconds per web process, so a `set-config` change
reaches the page within a minute with no restart.

Every portal user needs an active Employee record whose `user_id` is their User,
the **Employee Self Service** role, and a User Permission on their own Employee.
Creating the Employee with "Create User Permission" checked does the last part.

### Preflight

```bash
bench --site <site> execute helixhr.preflight.run
```

Prints one PASS/WARN/FAIL line per check above, plus fixtures installed,
frontend built, and every linked employee having their User Permission. Exits
non-zero on any FAIL, so a deploy script can gate on it. Run it on staging, then
again on production, after every deploy. The checks assume the local-login
phase; `helixhr/preflight.py` marks the two lines that flip when Entra ID goes
live.

## Develop

The team's dev bench is a `frappe_docker` devcontainer with this repo
bind-mounted at `apps/helixhr`; `bench start` serves `test_site` on port 8000.
Any bench works the same way.

```bash
# backend: edit Python, the dev server reloads
# frontend: rebuild after each change, then clear the page cache
cd apps/helixhr/frontend && yarn build && bench --site <site> clear-cache
```

`yarn dev` (Vite dev server with proxy) also works but the built page is what
ships, so verify against `yarn build` before committing.

Do not run `prettier` in `frontend/`. The repo root `.editorconfig` is for
Frappe's Python and would retab every Vue file; `yarn lint` is the formatter.

## Verify

Run all of these before claiming a change is done. CI runs the same set.

```bash
# Python (from the bench root; needs allow_tests on the site)
bench --site test_site set-config allow_tests true
bench --site test_site run-tests --app helixhr
ruff check helixhr

# frontend
cd frontend
yarn lint
yarn test                       # vitest, unit
yarn build

# end to end, against a running bench that has the Playwright fixtures seeded
curl -c cookies.txt -X POST http://localhost:8000/api/method/login -d "usr=Administrator&pwd=<admin>"
curl -b cookies.txt -X POST http://localhost:8000/api/method/helixhr.tests.utils.setup_playwright_fixtures
BASE_URL=http://localhost:8000 SITE_HOST=test_site yarn test:e2e -- --workers=1
```

Two things bite on a long-lived local site and are not bugs: the Python suite's
leave-balance test fails if an earlier run left a Leave Allocation behind, and
`timesheet-approval.spec.ts` is single-run-per-site by design. Recreate the test
site (or reset the fixture data as the runbook shows) before a final run. CI
always starts from a fresh site and is the authoritative signal.

## Release

1. Merge to `main`; CI must be green.
2. On the server: `bench get-app`/`git pull` in `apps/helixhr`, then
   `cd apps/helixhr/frontend && yarn install --frozen-lockfile && yarn build`.
3. `bench --site <site> migrate` (installs fixtures) and `bench --site <site> clear-cache`.
4. `bench --site <site> execute helixhr.preflight.run` and fix every FAIL.
5. Restart the web workers if the Python changed (`bench restart`).

## Sign-in

Phase 1 ships with **local username/password login**. Microsoft Entra ID via
Frappe's Office 365 Social Login Key is the planned next step; the Azure and
Frappe steps are written up in the runbook and have not yet been verified on a
real host. Do not disable password login before an Entra key is enabled and
tested, or nobody can sign in. Preflight fails on exactly that combination.

## Test users

`helixhr/tests/utils.py` creates `employee@helixhr.test`,
`manager@helixhr.test` and `no-employee@helixhr.test` on demand, all with the
password in that file's `TEST_PASSWORD`. They exist only on sites where
`allow_tests` is on; never enable that on production.

## Rules

- Never modify Frappe, ERPNext or HRMS core code. Extend through fixtures, hooks and whitelisted methods only.
- Every server call runs as the logged-in user. Frappe permissions are the security model; do not bypass them with `ignore_permissions` in request paths.
- No Frappe vocabulary on screen. The copy table in the design system is the mapping.
- Assert the payload, not the chrome: tests must check that a feature works, not that its label rendered.

## License

MIT
