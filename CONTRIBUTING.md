# Contributing to HelixHR

Thank you for taking the time. This is a small, deliberately scoped product,
and the fastest way to a merged change is to fit how it is already built.

## Before you start

- Read [README.md](README.md), then [docs/architecture.md](docs/architecture.md).
  Every gotcha found so far is in [docs/runbook.md](docs/runbook.md).
- Check [PRODUCT.md](PRODUCT.md). Some things are out of scope on purpose
  (payroll, onboarding, recruitment, anything that re-implements an HRMS
  rule). A change that pulls in one of those needs a conversation first —
  open an issue.
- Larger work starts as a plan in `docs/plans/`, with requirement IDs the code
  and tests then cite (`P6-R4`, `P6-U3`). A bug fix does not need one; a new
  screen or a new server method usually does.

## Ground rules

- Never modify Frappe, ERPNext or HRMS core. Extend through fixtures, hooks and
  whitelisted methods.
- Every server call runs as the signed-in user. Do not use `ignore_permissions`
  in a request path.
- Every new read is bounded and added to `RATE_LIMIT_POLICY`. Every new screen
  renders through `AsyncState`. Every structural change gets a preflight guard.
- No Frappe vocabulary on screen. `docs/design-system.md` has the copy mapping.
- Tests assert the payload, not the chrome.
- Python is tabs (ruff). Vue is 2-space, formatted by `yarn lint`. Never run
  `prettier` in `frontend/`.

## Verification

Run the full set before opening a pull request; CI runs the same from a fresh
site and is the authoritative signal.

```bash
bench --site test_site run-tests --app helixhr
ruff check helixhr
cd frontend && yarn lint && yarn test && yarn build
BASE_URL=http://localhost:8000 SITE_HOST=test_site yarn test:e2e -- --workers=1
```

## Pull requests

- One concern per PR. Keep the diff to what the change needs.
- Conventional commit messages (`feat(people): ...`, `fix(e2e): ...`).
- Say in the description what you verified and how. If you skipped a part of
  the verification set, say which and why.
- A change to a user-visible screen should include or update a Playwright
  spec, and a change to a server method a Python test that asserts real data.

## Reporting a vulnerability

Please do not open a public issue. See [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contribution is licensed under the
[GNU Affero General Public License v3](LICENSE), the same as the project.
