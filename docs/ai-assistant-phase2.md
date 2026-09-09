# HelixHR AI Assistant — Phase 2 plan

> **Not yet.** Do not build any of this until the HRMS self-service bundle
> (payslips, check-in, holidays, attendance requests, team calendar) is live.
> Reviewed against the shipped portal on 2026-09-09; see §7 for what changed.

## 1. What it is

A chat box inside the portal. An employee types a request in plain words. The assistant answers from Frappe data or proposes a small, known HR action, always as that employee.

Examples: "How much casual leave do I have?" · "Apply leave this Friday." · "What is missing from my timesheet?" · "Update my phone number." · "Who is my manager?"

## 2. Rules

1. **The assistant is just another caller of the same API.** It calls the same `helixhr.api` methods the screens call. No new HR logic. No raw DocType access. No SQL.
2. **It runs as the logged-in user.** The chat endpoint lives in the `helixhr` app, so `frappe.session.user` is already the employee. Frappe permissions apply on every tool call with zero extra code.
3. **Small tool list.** Start with about 6 tools. Add one at a time when real usage asks for it.
4. **The assistant never writes.** For an action, the model returns a *proposal* (leave type, dates, reason). The server hands that back to the browser as a card ("Casual Leave · Fri 4 Sep · 1 day"). On Confirm the **browser** calls the normal method (`apply_for_leave`) exactly as the Leave page does. The model loop has no write tool at all, so confirmation is enforced by structure, not by prompt. (Changed from "writes always confirm" — see §7.)
5. **No memory across sessions, no training on company data.** Each chat starts fresh with the employee's context.
6. **Every tool call is logged** (user, tool, arguments, result size, latency) in one small DocType for audit.
7. **Model provider is a later decision**, but the constraint is fixed now: data residency for India and USA employees. See §6.

## 3. Phase 2 tool list (v1)

Every tool already has a backing method in `helixhr/api.py`. Nothing new is needed on the read side.

| Tool | Read / Propose | Backed by |
|---|---|---|
| `get_my_profile` | Read | `helixhr.api.get_portal_bootstrap` (employee, manager, company) |
| `get_leave_balances` + `list_my_leave` | Read | `helixhr.api.get_my_leave` |
| `get_leave_day_count(from, to)` | Read | `helixhr.api.get_leave_day_count` (working days per the employee's holiday list) |
| `get_attendance(month)` | Read | `helixhr.api.get_my_attendance` |
| `get_my_timesheet_week(week)` | Read | `helixhr.api.get_my_week` |
| `propose_leave(leave_type, from, to, reason)` | **Propose** | returns a card; browser calls `helixhr.api.apply_for_leave` on Confirm |
| `propose_profile_update(fields)` | **Propose** | returns a card; browser calls `helixhr.api.update_my_profile` (allow-list already enforced server-side) |

Deliberately **not** in v1: timesheet submit, leave withdrawal, approvals, anything for managers or HR, HR policy Q&A over documents (needs retrieval, a different project). Add after the reads and `propose_leave` prove reliable.

## 4. Shape of the build

- One whitelisted method `helixhr.ai.chat(messages)` in Python, in a new `helixhr/ai.py`. It runs the tool-calling loop server-side. The browser never holds a model API key.
- **Request/response, no streaming.** Frappe whitelisted methods return once. Streaming would need socketio or a polled background job. A small model with 5 or fewer tool calls answers in a few seconds; ship that first.
- Tools are thin wrappers around the existing methods, returning small JSON. Cap tool calls per chat at 5, cap messages per chat at 10.
- Date words ("this Friday") are resolved server-side in the **site** time zone (Asia/Kolkata), not the browser's, then shown back on the card. The e2e suite already had one day-boundary bug from mixing the two clocks.
- Rate limit through the existing `rate_limit_per_user` table in `helixhr/utils.py`, like every other write.
- Frontend: one chat panel component in the existing Vue app. Proposal cards reuse the Leave and Profile form components' summary shape. Same Signal tokens, no new colour or surface.
- Kill switch: a `helixhr_assistant` key in site config (`frappe.conf`), the same mechanism as `helixhr_rate_limits` and the HR contact setting. Absent means off. No settings DocType.
- Preflight: `helixhr.preflight.run` reports whether the assistant is on and whether the provider key resolves.
- Tests: the provider is behind one small interface with a **fake** implementation used by Python tests and e2e, so CI never calls a real model. One test per tool proves it returns exactly what the screen method returns for the same employee.

## 5. What phase 1 did so phase 2 is easy (verified 2026-09-09)

- `helixhr.api` methods are small, intent-named, and resolve the employee from the session. ✅ All 27 whitelisted methods.
- Plain JSON with human-readable labels. ✅
- Allow-listed profile writes with a server-side field list. ✅ `update_my_profile`.
- Per-user rate limits already exist. ✅
- Reusable confirmation card. ⚠️ Partial: leave withdrawal is confirm-gated, but there is no standalone card component yet. Extract one when the chat panel is built.

No AI code, no vector database, no agent framework was added in phase 1. Still true.

## 6. Decision points for later

| Question | Default |
|---|---|
| Provider and model | A current mid-tier model with tool use. Prefer a **regional** endpoint: Claude on AWS Bedrock Mumbai (ap-south-1), Azure OpenAI India Central, or Vertex Mumbai. Direct US-hosted APIs need a data-processing agreement and zero-retention terms first. |
| Python dependency | One: the provider SDK, or plain `httpx` against the provider's HTTP API. The bench has no LLM library today and Frappe v16 has no built-in one. Needs an explicit go-ahead per repo rules before it is added. |
| Where does the chat run | Inside the Frappe app (Python), same server, synchronous. |
| Cost | A chat is a few thousand tokens. Cap turns; log token counts in the audit DocType so the bill is visible before it matters. |
| Should the assistant ever act for managers | No, until employee use is boring and reliable. |
| Voice, Teams bot | No. |

## 7. Feasibility review, 2026-09-09

Reviewed after the phase 2 hardening shipped. Verdict: **feasible, medium effort, about four units** — chat loop and tools with the audit DocType; chat panel and proposal cards; config switch, preflight and rate limit; fake-provider tests and docs. Comparable in size to one HRMS self-service bundle.

Changes from the original draft, and why:

1. **Tools now point at `helixhr.api`, not `hrms.api`.** The HRMS methods take `employee` as a parameter; ours resolve it from the session and return labels, which is the convention the whole portal enforces (P2-R26 strict permissions). Every tool in §3 has a backing method already.
2. **The assistant never writes** (rule 4). Removes the whole class of "model was talked into a write" risks, including prompt injection through data the model reads (leave reasons, HR replies). The browser does the write through the same method the page uses, so the audit trail and permission checks are identical.
3. **No streaming in v1.** Not worth socketio plumbing for sub-10-second replies.
4. **Kill switch is site config, not a DocType.** There is no `HelixHR Settings` DocType and the repo already uses `frappe.conf` for operator settings.
5. **Residency is the first provider question, not the last.** Site runs in Asia/Kolkata with US employees too; regional endpoints exist for every major provider.

Honest value check: single-fact reads ("how much casual leave") are one tap away on Home already. The assistant earns its place on multi-step questions ("what is missing from my timesheet this week"), on date words ("next Friday"), and later on HR policy Q&A over documents, which is deferred because it needs retrieval. Building it after the HRMS bundle means more data surfaces (payslips, holidays, attendance requests) are available as tools on day one.
