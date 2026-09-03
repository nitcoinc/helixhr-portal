# Agent rules — HelixHR Employee Portal

Start with `README.md`, then `docs/architecture.md`. Every gotcha found so far is in
`docs/runbook.md`; check it before debugging anything that smells environmental.

Senior engineer. Ship deliberately. Verify before claiming done.

## Minimal engineering mode

- Prefer no code, then config, then an existing utility, then a small local change, then a new abstraction.
- Before writing code, ask: can existing code, the standard library, a framework feature, or a current dependency solve this?
- Implement the smallest safe change that satisfies the task.
- Do not add a dependency, abstraction, service, helper, class, hook, middleware, or file unless clearly justified.
- Never trade away validation, error handling, security, accessibility, observability, or tests to be smaller.
- If a shortcut is intentional, note the upgrade path in a comment or the final message.

Response style: cut filler, keep technical substance, no hedging unless the uncertainty matters, exact technical terms. Format as decision -> reason -> action.

## Stack

- Frontend: Vue 3 + frappe-ui + Tailwind, built by Vite into `helixhr/public/helixhr/` and `helixhr/www/helixhr.html` (both gitignored). 2-space indent; `yarn lint` formats. Never run prettier here.
- Backend: Frappe v16 app `helixhr` on top of ERPNext + HRMS `version-16`. Python 3.14, tabs (ruff). Whitelisted methods in `helixhr/api.py`, doc events in `events.py`, config as fixtures.
- Database: MariaDB via the bench. Never edit Frappe/ERPNext/HRMS core; never edit an applied migration or fixture in place without a patch.
- Package manager: Yarn 1 with `yarn.lock` in `frontend/`; Python deps come from the bench (`uv pip install -e`).
- Tests: Python `IntegrationTestCase` under `helixhr/tests/`, vitest for pure functions, Playwright in `frontend/tests/e2e/`.
- Dev bench: `frappe_docker` devcontainer, this repo bind-mounted at `apps/helixhr`, `bench start` serving `test_site` on :8000. Run bench commands inside that container.

## Verification commands

Run from the bench root unless noted. All must pass before "done"; CI runs the same set from a fresh site.

- Lint: `ruff check helixhr` and `cd frontend && yarn lint`
- Test (Python): `bench --site test_site run-tests --app helixhr` (site needs `allow_tests true`)
- Test (unit): `cd frontend && yarn test`
- Test (e2e): seed fixtures via `helixhr.tests.utils.setup_playwright_fixtures`, then `cd frontend && BASE_URL=http://localhost:8000 SITE_HOST=test_site yarn test:e2e -- --workers=1`
- Build: `cd frontend && yarn build && bench --site test_site clear-cache`
- Preflight (per site, after deploy): `bench --site <site> execute helixhr.preflight.run`

A long-lived local site accumulates fixture data that fails two tests by design (leave-balance baseline, `timesheet-approval.spec.ts`). Recreate the site for a final run; see `README.md` → Verify.

## Pick one planning track per feature — do not interleave them

Both tracks are installed and both are good. They produce different artifacts in different places, so mixing them mid-feature gives you two half-written plans.

**Track A — tracker-centric.** Work lives as issues/tickets. Best when work is shared across people or agents.

```
/grill-with-docs  ->  /to-spec  ->  /to-tickets  ->  /implement  ->  /code-review
```

**Track B — artifact-centric.** Work lives as plan files in the repo. Best for solo deep work and when you want the plan reviewable in a PR.

```
/ce-brainstorm  ->  /ce-plan  ->  /ce-work
```

Reach for `/prototype` from either track when a design question is genuinely unresolved — throwaway code answers "does this state model feel right?" faster than argument does.

## Skill routing

| Your intent | Use | Notes |
|---|---|---|
| Sharpen a vague idea, and get ADRs + a glossary out of it | `/grill-with-docs` | Relentless interview. Writes durable docs as it goes. |
| Answer a design question with throwaway code | `/prototype` | Terminal app for logic; switchable variants for UI. Never ships. |
| Turn this conversation into a spec/PRD | `/to-spec` | No interview — synthesises what you already discussed. |
| Break a spec into buildable slices | `/to-tickets` | Tracer-bullet verticals with explicit blocking edges. |
| Build from a spec or tickets | `/implement` | Uses TDD at pre-agreed seams. |
| Requirements Q&A before any tech decisions | `/ce-brainstorm` | Requirements only, no implementation detail. |
| Technical plan with units and test scenarios | `/ce-plan` | Writes a dated plan file with traceability. |
| Execute a plan with verification and commits | `/ce-work` | Host verification, commit tracking. |
| Review a branch or PR properly | `/code-review` | Two axes in parallel: Standards and Spec. |
| Check I didn't over-engineer what I just wrote | `/ponytail-review` | Over-engineering only. Cheap, run it often. |
| Find dead weight across the whole repo | `/ponytail-audit` | Repo-wide complexity scan. |
| Research what's actually true right now | `/last30days <topic>` | Searches people, not editors. Ranks by real engagement. |
| Design system for a new UI: palette, type, industry conventions | `/ui-ux-pro-max` | Do this before writing UI, not after. |
| Generate UI that doesn't look AI-generated | `/hallmark` | Theme + macrostructure + slop gates. |
| Audit, critique or polish UI that already exists | `/impeccable` | Live browser iteration, deterministic detector rules. |

## UI work, in order

```
/ui-ux-pro-max   pick the design system        (greenfield, before any markup)
/hallmark        generate the screens          (avoids the AI-slop fingerprint)
/impeccable      audit -> critique -> polish   (on real, rendered UI)
```

Skipping straight to `/impeccable` on a UI with no design system just polishes something inconsistent.

## Review, in order

```
/ponytail-review   while coding    - did I add code that didn't need to exist?
/code-review       before merge    - Standards axis + Spec axis, run in parallel
```

`/code-review` needs a fixed point. Give it one: `main`, a SHA, `HEAD~5`.

## Spend tokens deliberately

- `ccc search "<intent>"` before broad file reads. Semantic, local, no model calls — it replaces the read-twenty-files-to-find-one habit.
- `/prototype` before committing to a state model. Cheaper than implementing the wrong one and refactoring.
- `/to-tickets` sizes each slice to one fresh context window on purpose. Respect the sizing; don't chain three tickets in one session.
- `/code-review` runs its two axes as separate sub-agents so their contexts never pollute each other.
- Record durable decisions once (Mem0) instead of re-deriving them next session.

## Tools

Semantic code search — prefer over broad reads:

```
ccc index .                    # once per repo, and after large changes
ccc search "<intent>"          # natural language
ccc grep "<pattern>"           # structural, by example
ccc status                     # index freshness
```

`ccc` also runs as an MCP server (`ccc mcp`), so search is available as a tool call. See the harness notes at the bottom of this file.

Memory (only if `mem0` is installed and `MEM0_API_KEY` is set):

```
mem0 add "<durable decision>"
mem0 search "<query>"
```

Store durable decisions, rejected approaches, recurring bugs and repo preferences. Never store secrets, API keys, passwords, customer data or scratch notes.

## Code quality

- Smallest safe change. Follow existing patterns over inventing new ones.
- No shipped `console.log` / `print` debugging.
- No `any` without a stated reason.
- Validate external input at the boundary.
- Public functions need a clear name or a short docstring.

## Dependency safety

- Prefer the latest stable patch. No prereleases without a reason.
- Node: pnpm, commit the lockfile.
- Python: uv, commit `uv.lock`.
- A new package needs a reason, a freshness check (`/last30days`), and a security check.

## Done means

- Verification commands pass — not "should pass".
- `/ponytail-review` clean, and `/code-review` run for anything meaningful.
- Durable decisions recorded.
- If you skipped or couldn't finish part of the task, say which part and why.

---

## Claude Code specifics

- Global skills live in `~/.claude/skills/`. Installed by `install/setup.ps1` or `install/setup.sh`.
- Compound Engineering and Ponytail are plugins — check with `/plugin list`.
- Run `/setup-matt-pocock-skills` once per project. It writes `docs/agents/issue-tracker.md`, which `/to-spec`, `/to-tickets` and `/code-review` all read.
- `.mcp.json` registers `ccc mcp` and nothing else — no docs or browser MCP by default.
