---
title: "fix: Celebration and holiday emails -- per-company settings, cross-company guard, moved to Email Templates"
type: fix
date: 2026-10-04
depth: standard
execution: code
---

# fix: Celebration and holiday emails -- per-company settings, cross-company guard, moved to Email Templates

## Summary

Birthday, work-anniversary and holiday emails get per-company settings: each company has its own on/off switch, template and audience. A send-time guard drops any recipient address that belongs to another company's active employee.

Configuration moves from Settings > Celebrations to a "Celebrations & holidays" group on the Email Templates page, which HR Manager can open. HelixHR takes over HRMS's holiday reminder, so its wording becomes editable the same way.

---

## Problem Frame

HR reports that employees of one company receive another company's celebration emails.

The send path already groups by the celebrant's company. `reminders._send_event` / `_send_company` and ERPNext's `get_all_employee_emails(company)` both filter on company. A read-only check of the 13 companies on the dev site found no cross-company address. The likely cause is data: two active Employee records in different companies that resolve to one mailbox. ERPNext only blocks a duplicate `user_id`; `company_email` and `personal_email` can repeat. The pool falls back user → company email → personal email, so a stale active duplicate in company B mails a person who now works in A.

Config adds to the confusion. `HelixHR Celebration Reminder` is one global row per event, and "All employees" reads as "everyone on the site". The holiday reminder is HRMS's own: hardcoded wording, sent weekly per employee, and live on dev.

---

## Requirements

**Company scope**
- R1. Every celebration and holiday setting belongs to one company; a company with no enabled setting sends nothing for that event.
- R2. Recipients are only active employees of the celebrating person's company. "All employees" is relabelled "Everyone in <company>".
- R3. At send time, any address that also belongs to an active employee of a different company is dropped and logged once per run, naming both Employee records.
- R4. Employees with no company are never mailed and never celebrated; skipped employees are counted in the log.
- R5. A celebrant never receives their own announcement, whichever email field resolves for them.
- R6. Preflight WARNs when an email address resolves for active employees in more than one company, listing the records.

**Holiday reminders**
- R7. HelixHR sends holiday reminders from its own template, per company, Weekly or Monthly, listing each recipient's upcoming non-weekly holidays from their own holiday list.
- R8. HRMS's `send_holiday_reminders` is turned off and kept off (read-only in Desk, refused on save, preflight FAIL if on).

**Configuration home**
- R9. The Email Templates page has a "Celebrations & holidays" group with three events (Birthday, Work anniversary, Holiday). Each event has a company selector, on/off, subject and body editor, audience (Everyone in company / Selected people), and, for Holiday, a frequency setting.
- R10. HR Manager and System Manager can open that group; HR Manager sees only companies in their admin scope. Notification Manager keeps the other template groups and does not see this one.
- R11. The editor offers preview and "send me a test" for the selected company, using that company's logo and name.
- R12. The selected-people picker searches employees of the selected company only, within the editor's scope.
- R13. `/settings/celebrations` redirects to the new group; the Settings nav entry is removed.

**Migration**
- R14. Existing global settings are copied to every company on migrate, so mail that sends today keeps sending after the change. Selected recipients are copied only to the row for the company they belong to.

---

## Key Technical Decisions

- KTD1. Per-company rows on the existing doctype. `HelixHR Celebration Reminder` gains a required `company`, a `frequency` field (holiday only), and `holiday` in the `event` Select, and `event` loses `unique: 1` so one event can have a row per company. Naming changes to `format:{event}-{company}`. A patch copies each existing global row (named by event) into one row per company and then deletes the global row. Keeping the doctype preserves its tests, preflight checks and save path; a parallel doctype would leave two sources of truth.
- KTD2. Templates stay Frappe `Email Template` records rendered by `reminders._render_restricted`, and companies may share one template. The editor stays HR Manager / System Manager (user decision), so the existing HR-only `restrict_globals` exception (P8-KTD7) still holds. Moving the bodies to `HelixHR Message Template` and its sandbox was rejected: existing HR-written bodies may use Frappe Jinja the sandbox lacks, and the Message Template editor is Notification Manager's.
- KTD3. The Email Templates page becomes role-sectioned instead of Notification-Manager-only. The route gate moves to a bootstrap flag, `can_edit_email_templates` (Notification Manager, HR Manager or System Manager). Each group's endpoints keep their own server gate: the celebration group uses `_is_hr()` plus a company check through `utils.resolve_admin_scope`. The e2e assertion "HR Manager sees no Email templates entry" is inverted.
- KTD4. The cross-company guard is a set subtraction in `_send_company`. Before sending, build the set of addresses belonging to active employees of other companies, then drop the overlap from the pool. It runs as one query per run, not per company. This fixes the reported symptom whatever the data cause and makes it visible in the log, rather than trusting data hygiene.
- KTD5. Celebrant self-exclusion computes both HRMS address orders (`user_id → personal → company` and `user_id → company → personal`) and subtracts both. This fixes the small existing bug where a celebrant with no User can receive their own announcement.
- KTD6. The holiday sender lives in `reminders.py`, separate from the celebration loop. It runs on a daily scheduler entry and checks each company's frequency: Weekly sends on Monday, Monthly on the 1st. It imports HRMS `get_holidays_for_employee(only_non_weekly=True)` rather than re-implementing it (P4-KTD12). Recipients are grouped by resolved holiday list and upcoming-holiday set, giving one render and one send per group.
- KTD7. Holiday takeover mirrors the celebration takeover:
  - a `turn_off_hrms_holiday_reminders` patch, also called from `install.after_install`;
  - a Property Setter making `send_holiday_reminders` and `frequency` read-only;
  - `events.hr_settings_validate` refusing the save;
  - a `preflight.check_celebration_reminders` FAIL when the HRMS flag is on;
  - a seeded default Email Template through `seed_celebration_templates`, insert-if-absent.
- KTD8. A once-per-run marker is added now. The job writes a dated cache key per (event, company), as the overdue digest does, so a manual `bench execute` after the scheduler run does not mail every company twice. Per-company settings multiply the blast radius of that known hazard.

---

## Implementation Units

### U1. Per-company reminder model and migration

- **Goal:** Reminder settings keyed by event and company, existing behaviour preserved.
- **Requirements:** R1, R14
- **Dependencies:** none
- **Files:**
  - Modify: `helixhr/helixhr/doctype/helixhr_celebration_reminder/helixhr_celebration_reminder.json` (+ `.py` validation)
  - Create: `helixhr/patches/v1_0/split_celebration_reminders_by_company.py`; Modify: `helixhr/patches.txt`, `helixhr/install.py`
  - Modify: `helixhr/patches/v1_0/migrate_celebration_reminders.py` (fresh installs create per-company disabled rows, or none; decide in implementation per `after_install` order)
  - Test: `helixhr/tests/test_celebration_reminder_doctype.py`, `helixhr/tests/test_install.py`
- **Approach:** Patch is idempotent: skip when no global rows remain. Copy `is_enabled`, `email_template`, `recipient_mode`; filter recipients per company. Validation: `recipient_mode = Selected` requires at least one recipient, and all recipients must belong to `company`; `frequency` is required only for holiday.
- **Execution note:** Characterize current single-row behaviour in a test before changing the schema.
- **Test scenarios:**
  - Two companies, global birthday row enabled with template T → after patch, `birthday-A` and `birthday-B` both enabled with T; global row gone.
  - Global row in Selected mode with recipients from A and B → A row has A's recipients, B row has B's.
  - Patch run twice → no duplicates, no error.
  - Saving a row with a recipient from another company → refused.
  - Holiday row without frequency → refused.
- **Verification:** Migrate on a copy of a site with existing settings sends the same mail as before (U2 tests).

### U2. Company-scoped sender with guard

- **Goal:** The celebration send path reads per-company rows and drops cross-company addresses.
- **Requirements:** R2–R5
- **Dependencies:** U1
- **Files:**
  - Modify: `helixhr/reminders.py` (`send_celebration_reminders`, `_send_event`, `_send_company`, run marker)
  - Test: `helixhr/tests/test_reminders.py`
- **Approach:** Loop over the companies HRMS returns in `get_employees_having_an_event_today`, skip the `None` group, and load that company's row for the event. Build the foreign-address set once per run. Log dropped addresses with both Employee names, without the email body.
- **Test scenarios:**
  - Company A enabled, B disabled, celebrants in both → only A's employees mailed, about A's celebrant.
  - Duplicate: Employee X1 in A and stale X2 in B, both Active with the same `company_email`; celebrant in B → X's address dropped from B's pool and the log names X1 and X2. Covers the reported bug.
  - Employee with no company celebrating → not announced; company-less employees not mailed.
  - Celebrant with no User, both personal and company email → receives no announcement of themselves.
  - Selected mode in A → only selected A people mailed.
  - Second run same day → no mail (marker); next day → sends.
  - Existing `test_recipients_never_cross_companies` still passes.
- **Verification:** All reminder tests pass; the guard test fails on the old code.

### U3. Holiday reminder takeover

- **Goal:** HelixHR sends editable, per-company holiday reminders; HRMS stays off.
- **Requirements:** R7, R8
- **Dependencies:** U1
- **Files:**
  - Modify: `helixhr/reminders.py` (`send_holiday_reminders`, `EVENTS` gains the holiday HRMS field), `helixhr/hooks.py` (daily scheduler entry)
  - Create: `helixhr/patches/v1_0/turn_off_hrms_holiday_reminders.py`; Modify: `helixhr/patches/v1_0/seed_celebration_templates.py` (holiday template), `helixhr/patches.txt`, `helixhr/install.py`
  - Modify: `helixhr/fixtures/property_setter.json`, `helixhr/events.py` (`hr_settings_validate`), `helixhr/preflight.py`
  - Test: `helixhr/tests/test_reminders.py`, `helixhr/tests/test_preflight.py`, `helixhr/tests/test_fixtures.py`, `helixhr/tests/test_install.py`
- **Approach:** Window: Weekly sends on Monday for Monday–Sunday; Monthly sends on the 1st for that month. Use exclusive bounds, avoiding HRMS's double-listing of boundary days. Template context: `holidays` (date, description), `company`, `logo_url`, `portal_url`, `employee_name`. Skip employees whose list has no upcoming holiday or who have no holiday list.
- **Test scenarios:**
  - Weekly, Monday, A's holiday list has a Wednesday holiday → A's employees on that list get one email listing it; employees on a list without holidays get none.
  - Two holiday lists in one company → two sends, each listing its own holidays.
  - Monthly company on a Monday that is not the 1st → no send.
  - Weekly-off rows excluded.
  - HRMS `send_holiday_reminders` on → HR Settings save refused, preflight FAIL; patch turns it off.
  - Company B disabled → nothing for B.
- **Verification:** HRMS flag off on a fresh install; HelixHR holiday mail renders with the seeded template.

### U4. Celebration endpoints for the Email Templates page

- **Goal:** HR edits per-company settings through scoped endpoints.
- **Requirements:** R9–R12
- **Dependencies:** U1, U3
- **Files:**
  - Modify: `helixhr/api.py` (`get_celebration_setup(company)`, `save_celebration_reminder` gains `company` and `frequency`, `preview_celebration`, `send_test_celebration`, `search_celebration_recipients(company, query)`; bootstrap `can_edit_email_templates`; remove `celebrations` from `get_portal_config` once the UI moves)
  - Modify: `helixhr/utils.py` (`RATE_LIMIT_POLICY`), `helixhr/tests/test_upload_security.py`
  - Test: `helixhr/tests/test_api_config.py`, create `helixhr/tests/test_api_celebrations.py`
- **Approach:** Every endpoint checks `_is_hr()` and that `company` is in `resolve_admin_scope(session.user)`. The template write stays `ignore_permissions` on Email Template as today. A test send goes only to the caller's own address.
- **Test scenarios:**
  - HR Manager of A reads and saves A's birthday row; saving B's row → refused.
  - System Manager can edit any company.
  - Notification Manager without HR → refused on every celebration endpoint.
  - Recipient search for A returns only A's active employees matching the query.
  - Preview for A renders A's company name and logo.
  - Test send mails only the caller.
- **Verification:** Endpoint tests pass with a two-company fixture.

### U5. Email Templates page group and Settings removal

- **Goal:** One place to configure celebration and holiday mail.
- **Requirements:** R9–R13
- **Dependencies:** U4
- **Files:**
  - Modify: `frontend/src/pages/EmailTemplates.vue` (group nav, celebration editor), create `frontend/src/components/templates/CelebrationEditor.vue` (moved from `frontend/src/components/settings/CelebrationsSection.vue`)
  - Modify: `frontend/src/pages/Settings.vue`, `frontend/src/router.js` (drop `celebrations` section, add redirect), `frontend/src/components/AppShell.vue` (nav gate), `frontend/src/lib/session.js`
  - Delete: `frontend/src/components/settings/CelebrationsSection.vue`
  - Test: `frontend/tests/e2e/email-templates.spec.ts`, `frontend/tests/e2e/settings.spec.ts`
- **Approach:** The group's URL is `/email-templates?group=celebrations&event=birthday&company=…`. The company selector defaults to the session company and is hidden when scope has one company. The editor shows token help from `CELEBRATION_TEMPLATE_TOKENS`, plus holiday tokens. The Notification Manager view is unchanged apart from the group nav.
- **Test scenarios:**
  - HR Manager sees Email Templates nav with only "Celebrations & holidays"; edits A's birthday subject; reload keeps it.
  - Notification Manager sees message templates, not the celebration group.
  - `/settings/celebrations` lands on the new group.
  - Selected-people picker lists only the chosen company's employees.
  - Holiday event shows a frequency selector; birthday does not.
- **Verification:** e2e passes for HR Manager, Notification Manager and System Manager.

### U6. Duplicate-mailbox preflight and docs

- **Goal:** Surface the data cause and document the model.
- **Requirements:** R6
- **Dependencies:** U2
- **Files:**
  - Modify: `helixhr/preflight.py` (new `check_cross_company_mailboxes`), `helixhr/tests/test_preflight.py`
  - Modify: `docs/architecture.md` (rewrite the stale "Reminders" section ~814-860), `docs/deployment.md`, `docs/runbook.md` (diagnostic SQL, what to do with duplicates)
- **Approach:** Group active employees by resolved address (user → company email → personal email). WARN when a group spans companies, listing at most 20 pairs.
- **Test scenarios:**
  - Two active employees in different companies sharing `company_email` → WARN naming both.
  - Same address within one company → PASS.
  - One of the pair set to Left → PASS.
- **Verification:** Preflight on a fresh site passes; on a seeded duplicate it WARNs.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Changing autoname of an existing doctype | Patch creates new rows and deletes old ones explicitly; test on a site with data; idempotent. |
| Fresh-install patch ordering (`new-site` marks patches done) | Call every new patch from `install.after_install`, as existing celebration patches do. |
| Guard hides a real person who legitimately has two employments | Logged every run and surfaced by preflight; HR fixes the data. |
| HR template bodies reading other records (`restrict_globals` not a sandbox) | Unchanged boundary, HR-only editors (KTD2); documented. |
| Holiday mail volume on Monday for large companies | One send per holiday-list group, not per employee. |

---

## Operational Notes

- After deploy, run the duplicate-mailbox query on the affected site (in `docs/runbook.md`), and set the stale duplicates to Left.
- Check each company's three settings on the Email Templates page. Migration copies the old global setting to every company, so a company that should not get mail must be switched off.

---

## Sources & Research

- `helixhr/reminders.py:93-245` (send path); `hrms/controllers/employee_reminders.py:18-83` (holiday sender), `:152-195` (celebrants grouped by company); ERPNext `setup/doctype/employee/employee.py:311-326` (duplicate check is `user_id` only), `:367-370` and `:482-497` (two address orders)
- `helixhr/api.py:6898` `save_celebration_reminder`, `:6598-6740` Email Templates endpoints (`_assert_can_manage_notifications`)
- Prior decisions: P4-KTD12 (import HRMS helpers), P8-KTD7 (HR-only restricted render), P8-KTD8 (read-only HRMS checkboxes); memory `restrict-globals-not-a-sandbox`
