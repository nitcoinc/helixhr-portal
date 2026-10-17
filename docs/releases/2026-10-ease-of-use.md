# What moved: the October 2026 ease-of-use release

Plan 2026-10-17-001. Nobody gains or loses access to anything in this release: every page, link
and bookmark you used before still works. Some things moved so the work you do most is closer,
and a few new shortcuts appear. This note is by role; read your own row and everything under
"Everyone".

## Everyone

- **Search from anywhere.** On a computer press **Ctrl-K** (**⌘K** on a Mac) or **/**, or click
  the search button beside your name at the top of the side menu. On a phone tap the **search
  icon** in the top bar, or **Search** at the top of **More**. Type a page ("payslips"), an action
  ("ask for leave"), a colleague's name or a request's subject. You only ever see pages and people
  you could already open. Your last few picks are remembered on that browser for you only, and
  are cleared when you sign out.
- **Notifications are grouped by request.** Several updates about the same request, leave or
  week show as one row with "+N earlier". Opening it marks the whole group read. There is an
  **Unread** filter at the top.
- **Notification wording is plainer.** Titles now read like "Your timesheet for Mar 8 is waiting
  for your manager", with ordinary dates instead of `2026-03-08` and "waiting for your manager"
  instead of "Pending Approval". Email subjects change the same way.
- **Ask HR** is now in the same place on every page: the foot of the side menu, and in **More** on
  a phone.
- **Skip to main content.** Keyboard users get a "Skip to main content" link as the first Tab stop.
- Your side menu's open and closed sections are remembered **per person** now, not per browser.
  On a shared computer the next person no longer inherits your layout. Because of the change,
  everyone's remembered sections reset once.

## Employees

- **Home shows one row per request.** A request with several HR replies reads "HR replied · 4 ·
  Address proof" instead of four rows. Tap anywhere on a row to open it.
- **A new "Decided" group on Home.** A final rejection, or a request HR has closed that you have
  not looked at yet, now sits under **Decided** instead of "Waiting on others". It stays there
  until you have seen it; it does not disappear after a few days.
- **Check in from Home.** Where mobile check-in is switched on, Home has a **Check in** chip. Tap
  it and confirm, and you are done.
- **Requests: "Needs you" means you owe something.** A request is under Needs you when HR is
  waiting for your reply or has sent you a message you have not read. A request that is simply
  done or declined shows a dot in its usual group instead.
- **Timesheet on a phone opens on today**, with the week above it and the month folded into one
  line. **Copy last week** now skips holidays, approved leave and projects that have closed, and
  tells you what it skipped.
- **Payslips:** every payslip row has its own **PDF** link, so you can download an older one
  without opening it.
- **Profile:** one **"Something wrong? Request a correction"** link per section, where there used
  to be one on every line.
- **Holidays** use the full page width and hide the ones already gone behind **Show past**.

## Managers (anyone with direct reports)

- **Approvals is a tab on your phone.** Your bottom bar is now **Home, Approvals, Timesheet,
  Requests, More**. It shows how many decisions are waiting.
- **Leave moved into More.** It is the first row under **My work** in More, and Home's **Ask for
  leave** and search still take you straight to the form.
- **Approve all.** At the top of each lane's "Looks normal" section, **Approve all N that look
  normal** approves every clean timesheet and leave request in that lane in two taps, after the
  usual confirm showing the count and total. Flagged items, attendance requests and change
  requests are never included; you still decide those one at a time. More than 60 waiting? It
  approves the oldest 60 and says so.
- On a computer you can also tick a whole lane or a whole person's group at once.
- **Keyboard shortcuts** (j/k to move, x to select, Enter to open, a to approve) are off unless
  you switch on **Keyboard shortcuts** beside the first lane. `a` only opens the confirm; it never
  approves on its own.
- **Team and Roster** list people who are off first, with an **Only people off** filter.
- If you approve leave but nobody reports to you, your bottom bar does not change; the waiting
  count shows on **More**.

## HR

- **Your HR section comes first.** In the side menu, **HR** sits directly under Home and
  Notifications and starts open. **Pay & policies** now starts folded, so the whole menu fits on
  a laptop screen without scrolling. Open or close any section and that choice is remembered.
- **Directory and People are one entry.** If you can open People, the side menu shows one
  **People** entry instead of both. The Directory page is still there at its usual address.
- **Approvals is a tab on your phone**, as for managers above.
- **Work the Team inbox from the list.** **Requests** opens straight on the Team inbox. Each row
  has **Assign to me** when you can pick it up, so you no longer open a request just to take it.
  Search by subject or by the requester's name, filter by category, and sort **past SLA first**.
  Overdue rows show how far over they are ("2 d over").
- HR Users who only oversee timesheets keep the employee bottom bar and see oversight first on
  Approvals.

## IT Team and other request handlers

- **Requests** opens on your Team inbox, with the same **Assign to me**, search and SLA sort as
  HR.

## Portal Admins

- **Your Admin section comes first** in the side menu and starts open (unless you are also HR, in
  which case HR does).
- **Report access** is shorter: rows that do not apply show a dash with one note under the
  column, each report family has a select-all, and on a phone families fold. The **export log**
  reads as two-line rows on a phone, with report and person filters.
- **Email templates** opens on the first customised email on a computer.

## For administrators deploying it

- Run `bench migrate`. A patch (`v1_0/apply_notification_copy`) rewrites the HelixHR notification
  titles and messages on existing sites; live bell and email subjects change from then on.
- Restart the web and worker processes after `bench migrate` (`bench restart`, or your supervisor
  equivalent). The new notification templates call Jinja methods registered in `hooks.py`
  (`helixhr_date`, `helixhr_state_words`), and a process started before the deploy cannot render
  them.
- The patch and the fixture sync overwrite the subject and message of the three HelixHR
  notifications. Any Desk edit to those three is lost; copy it out before migrating if you need it.
- **Leave** now reads leave balances as administrator, after its own check that the caller is the
  employee. An IT Team member without the Employee role can open Leave; no permission grant was
  added for it.
- Run `bench --site <site> execute helixhr.preflight.run`. It now also checks the rate limit for
  the new search (`portal_search`).
- Approvers' first page load does slightly more work, to show the pending count (a handful of
  extra database reads, measured). Nothing polls for it.
- Developers: the frontend's package manager is Yarn 1. AGENTS.md's dependency section still says
  pnpm; that line is wrong and is being corrected separately (`docs/runbook.md`).
