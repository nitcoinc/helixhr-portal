# HelixHR AI Assistant — Phase 2 plan

> **Not phase 1.** Do not build any of this until the portal is live and stable.
> Phase 1 only has to keep the API small and typed. That is the whole "foundation".

## 1. What it is

A chat box inside the portal. An employee types a request in plain words. The assistant answers from Frappe data or performs a small, known HR action, always as that employee.

Examples: "How much casual leave do I have?" · "Apply leave this Friday." · "What is missing from my timesheet?" · "Update my phone number." · "Who is my manager?"

## 2. Rules (fixed now, so phase 1 does not paint us into a corner)

1. **The assistant is just another caller of the same API.** It calls the same `hrms.api` / `helixhr.api` methods the screens call. No new HR logic. No raw DocType access. No SQL.
2. **It runs as the logged-in user.** The chat endpoint lives in the `helixhr` app, so `frappe.session.user` is already the employee. Frappe permissions apply on every tool call with zero extra code.
3. **Small tool list.** Start with about 6 tools. Add one at a time when real usage asks for it.
4. **Reads need no confirmation. Writes always do.** The assistant shows a short card ("Casual Leave · Fri 4 Sep · 1 day") and the employee taps Confirm. The write happens only after the tap, through the normal method.
5. **No memory across sessions, no training on company data.** Each chat starts fresh with the employee's context.
6. **Every tool call is logged** (user, tool, arguments, result, time) in one small DocType for audit.
7. **Model provider is a later decision.** Any LLM API with tool calling ("function calling") works. Pick when phase 2 starts; check data-residency for India and USA employees then.

## 3. Phase 2 tool list (v1)

| Tool | Read / Write | Backed by |
|---|---|---|
| `get_my_profile` | Read | `hrms.api.get_current_employee_info` |
| `get_leave_balances` | Read | `hrms.api.get_leave_balance_map` |
| `list_my_leave` | Read | `hrms.api.get_leave_applications` |
| `apply_leave(leave_type, from, to, reason)` | **Write, confirm** | Leave Application create + submit (same as portal form) |
| `get_attendance(month)` | Read | `hrms.api.get_attendance_calendar_events` |
| `get_my_timesheet_week(week)` | Read | `helixhr.api.get_my_week` |
| `update_my_profile(fields)` | **Write, confirm** | `helixhr.api.update_my_profile` (allow-list) |

Deliberately **not** in v1: submit timesheet, cancel leave, approvals, anything for managers or HR. Add after the reads and `apply_leave` prove reliable.

## 4. Shape of the build (when the time comes)

- One whitelisted method `helixhr.ai.chat(messages)` in Python. It runs the tool-calling loop server-side. The browser never holds a model API key.
- Tools are thin wrappers that call the existing methods and return small JSON.
- Date words ("this Friday") are resolved server-side using the employee's holiday list and time zone, then shown back for confirmation.
- Frontend: one chat panel component in the existing Vue app. Reuse the portal's cards for the confirmation step.
- Cost control: short system prompt, small tool schemas, cap on turns per chat.
- Kill switch: a checkbox in `HelixHR Settings` turns the assistant off for everyone.

## 5. What phase 1 must do so phase 2 is easy

- Keep `helixhr.api` methods **small, typed, and named by intent** (`get_my_week`, not `get_data`). Each one already checks permissions and takes plain arguments.
- Resolve the employee **from the session** inside every method, never from a parameter.
- Return plain JSON with human-readable labels (leave type names, status words), not internal codes.
- Keep the confirmation card UI as a reusable component (it is used by the normal Apply Leave flow too).

That is all. No AI code, no vector database, no agent framework in phase 1.

## 6. Decision points for later

| Question | Default |
|---|---|
| Which LLM provider and model? | Decide at phase 2 start; pick the current mid-tier model with tool use; check data residency |
| Where does the chat run? | Inside the Frappe app (Python), same server |
| Should the assistant ever act for managers? | No, until employee use is boring and reliable |
| Voice, Teams bot? | No |
