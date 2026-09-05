# HelixHR portal frontend

Vue 3 + Vue Router + Tailwind + [frappe-ui](https://github.com/frappe/frappe-ui), built by Vite into
the Frappe app next to it. `yarn build` writes `../helixhr/public/helixhr/` and
`../helixhr/www/helixhr.html`; both are gitignored, so every deploy rebuilds them.

Start with the repository `README.md` and `docs/architecture.md`. This file covers only what is
specific to the frontend.

## Run it

```bash
yarn install
yarn build                 # production build into the app
yarn dev                   # Vite dev server on :8080, proxying the bench on :8000
yarn lint                  # ESLint. Never run prettier here -- see docs/runbook.md
yarn test                  # vitest, pure functions only
yarn test:e2e              # Playwright, against a running bench
```

`yarn dev` proxies API calls to the bench, so a dev session is a real session against a real site.

## Security in development

**Do not set `ignore_csrf`.** Older frappe-ui starter instructions told you to put
`"ignore_csrf": 1` in `site_config.json` to stop `CSRFToken` errors behind the Vite dev server.
That setting disables CSRF validation for *every* mutation on the site, which is exactly what
`helixhr.api`'s POST methods rely on, and `helixhr.preflight.check_csrf` now FAILs a site that has
it — a habit picked up in development is the way it reaches production.

It is not needed. The shell (`helixhr/www/helixhr.py`) renders a real `window.csrf_token` into the
page and `src/lib/api.js` sends it on every request, in dev and in production alike. If you do see
a CSRF error, the session is stale, not misconfigured: `lib/api.js` already reloads once on that
specific failure, and signing in again fixes the rest.

Two more rules the app depends on and a dev session must not break:

- Uploads go through `helixhr.api.attach_to_my_request`, never `frappe.client.insert` on File. The
  policy (private, at most 10MB, PDF/PNG/JPEG/DOCX/XLSX, checked by content signature) lives in
  `helixhr/utils.py` and is enforced again in the `File.before_insert` hook.
- Nothing in `src/` performs authorization. `lib/session.js`'s `canApprove` decides whether a nav
  item is drawn and nothing else; every domain method re-resolves the session user on the server.

## Layout

```
src/
  main.js            app + router + frappe-ui resource plugin
  router.js          routes, including the exact-detail convention (P2-R12)
  index.css          design tokens; the Tailwind entry
  lib/               session bootstrap, api, dates, error copy, unread badge, small guards
  components/        AppShell and the shared primitives (AsyncState, StatusBadge, ...)
  pages/             one component per route, each a lazy chunk
tests/
  playwright.config.ts   projects: setup, employee, manager, employee-mobile-webkit, baseline
  e2e/                   flow specs, plus hardening.spec.ts and performance.spec.ts
```

## Build-level decisions worth knowing about

These are in `vite.config.js` and `tailwind.config.cjs`, and each is there for a measured reason
(P2-U9; the numbers are in `docs/runbook.md`).

- **No production source maps.** frappe-ui's build plugin defaults them on, which published a
  readable copy of every source file next to each chunk. Rebuild locally with
  `npx vite build --sourcemap` when you actually need one.
- **`feather-icons` is aliased to `src/lib/featherIcons.js`.** frappe-ui's `Button` and `Dialog`
  import `FeatherIcon`, which pulls the whole 96KB Feather set into the eagerly loaded chunk. This
  app draws every icon from `src/components/Icon.vue`. `src/lib/featherIcons.test.js` fails if any
  component starts passing `icon`/`iconLeft`/`iconRight` to a frappe-ui component.
- **Tailwind scans a named list of frappe-ui components, not all of them.** Scanning all of them
  generated the utilities for Calendar, Charts, TextEditor and the pickers, and blew the CSS
  budget. `src/lib/frappeUiComponents.test.js` fails if you import a frappe-ui component that is
  not in the list.
- **A web manifest, and deliberately no service worker.** The portal is installable; nothing about
  an employee is stored offline. Adding a service worker is a Product Contract change, not a build
  change.

## Tests

`yarn test` is vitest over pure functions and the build guards above — no component mounting, no
DOM. Everything that needs a browser is Playwright, against a real bench:

```bash
BASE_URL=http://localhost:8000 SITE_HOST=test_site yarn test:e2e -- --workers=1
```

`--workers=1` matters: the specs mutate shared fixture data. See `docs/runbook.md` for seeding, for
the employee-mobile-webkit project's system dependencies, and for the pinned performance protocol.
