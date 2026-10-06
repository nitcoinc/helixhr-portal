---
title: "feat: Leave rules tab in portal Settings; Report access and Portal roles become Portal Admin only"
type: feat
date: 2026-10-06
depth: standard
execution: code
---

# feat: Leave rules tab in portal Settings; Report access and Portal roles become Portal Admin only

## Summary

HR can edit the backdated leave rule (grace days and exempt role) from a new "Leave rules" tab in portal Settings, instead of through site config. The Report access and Portal roles tabs, and every endpoint behind them, move to HelixHR Portal Admin and System Manager only. HR Manager and HR User lose that access.

---

## Problem Frame

- The backdated leave rule is not in HR Settings on purpose. HRMS's `restrict_backdated_leave_application` checks the session user, so it also blocks approvers (`docs/runbook.md`, "Backdated leave"). The HelixHR rule lives only in site config (`helixhr_backdated_leave_grace_days`, default 1, and `helixhr_backdated_leave_exempt_role`), read in `helixhr/events.py` (`BACKDATED_EXEMPT_ROLE_KEY`, `backdated_grace_days`, `_backdated_exempt`). HR has no screen for it and can't find it.
- Settings shows Report access and Portal roles to HR as well as to Portal Admin (`frontend/src/pages/Settings.vue`: `ALL_SECTIONS`, `PORTAL_SECTIONS`, `portalOnly`). The user wants these administered by Portal Admin only, the same move already made for Email templates (merge `9cc51e4`).

---

## Requirements

**Leave rules**
- R1. A "Leave rules" tab in portal Settings, with the same audience as Leave types (`session.canConfigure`: HR Manager and System Manager). It edits grace days (0 to 365) and an optional exempt role, which must exist.
- R2. The tab explains in plain words what the rule does. It says HR Manager always has no limit, and that HR Settings' `restrict_backdated_leave_application` must stay off.
- R3. The rule enforcement in `events.py` reads the new stored values. Behaviour is unchanged when they equal the current site config.
- R4. Existing site-config values carry over on migrate.

**Admin-only settings**
- R5. The Report access and Portal roles tabs show only to HelixHR Portal Admin and System Manager.
- R6. Every endpoint behind those tabs refuses HR Manager, HR User and Employee: the access-matrix get and save, portal-role grant and revoke, and the export log if it belongs to the tab. Portal Admin and System Manager are allowed.
- R7. Portal Admin still cannot grant privileged roles such as System Manager. The existing guards stay.

---

## Key Technical Decisions

- KTD1. Storage. Add fields to an existing HelixHR Single settings DocType if one fits. Otherwise create a Single, `HelixHR Leave Rules`, with `backdated_grace_days` (Int, default 1, minimum 0) and `backdated_exempt_role` (Link Role). Rationale: HR needs to edit it without bench access.
- KTD2. Migration. A new idempotent patch copies the site-config values into the Single when the Single is unset. After that, `events.py` reads the Single only, which gives one source of truth. Register the patch in `patches.txt`, and call it from `install.py` if its sibling patches are called there.
- KTD3. Gate. The admin-only endpoints keep the explicit role check (`HelixHR Portal Admin` or `System Manager`) and then use `ignore_permissions`. Portal Admin keeps no DocPerm, which preflight enforces (memory: report execution model, Portal Admin is portal-only).
- KTD4. The Settings tab list derives from flags. HR sees the HR sections, and Portal Admin sees the admin sections. Someone holding both roles sees both.

---

## Implementation Units

### U1. Leave rules storage, migration and rule read

**Goal:** Move the backdated rule to stored settings.

**Requirements:** R3, R4

**Dependencies:** none

**Files:** new Single DocType under `helixhr/helixhr/doctype/` (or fields on an existing Single), `helixhr/events.py` (`backdated_grace_days`, `_backdated_exempt`), `helixhr/patches/v1_0/<new>.py`, `helixhr/patches.txt`, `helixhr/install.py` (if siblings do this), `helixhr/preflight.py` (`check_backdated_leave_grace` reads the Single), `helixhr/tests/test_leave_flow.py`, `helixhr/tests/test_preflight.py`

**Test scenarios:**
- Grace days 3 in the Single allows a leave 3 days back and refuses one 4 days back.
- A role in the exempt field is unlimited.
- HR Manager is still unlimited.
- The patch copies the site-config values, and a second run is a no-op.
- Preflight still fails when the HR Settings restriction is on.

### U2. Leave rules tab and endpoints

**Goal:** HR edits the rule in the portal.

**Requirements:** R1, R2

**Dependencies:** U1

**Files:** `helixhr/api.py` (new `get_leave_rules` and `save_leave_rules`, gated like the Leave types endpoints), `frontend/src/pages/Settings.vue` (new `leave-rules` section), `frontend/tests/e2e/settings.spec.ts`, `helixhr/tests/` (the module that tests the settings endpoints; grep `get_portal_config`)

**Test scenarios:**
- HR Manager saves 2 days, and the value persists.
- Days of -1 or 400 are refused.
- A nonexistent role is refused.
- HR User and Employee are refused.
- e2e: HR opens Leave rules, changes the days, and sees the saved value after reload.

### U3. Report access and Portal roles become Portal Admin only

**Goal:** Remove HR Manager and HR User access to the two admin tabs.

**Requirements:** R5, R6, R7

**Dependencies:** none

**Files:** `helixhr/api.py` (the access-matrix and portal-role endpoints; boot flags around `api.py:635`, `can_admin_portal`), `frontend/src/pages/Settings.vue`, `frontend/src/lib/session.js`, `frontend/src/components/AppShell.vue` and `frontend/src/router.js` if they gate these tabs, `helixhr/preflight.py` if it asserts the roles, `helixhr/tests/test_reports_access.py`, `frontend/tests/e2e/settings.spec.ts`, `frontend/tests/e2e/navigation.spec.ts`

**Test scenarios:**
- HR Manager and HR User are refused on every endpoint behind the two tabs.
- Portal Admin and System Manager are allowed.
- Portal Admin granting System Manager is refused (regression).
- e2e: HR no longer sees the tabs; `portal-admin@helixhr.test` sees them.

---

## Verification

Use the AGENTS.md set, with the final run on a brand-new site with strict permissions:
1. Create the site (`bench new-site` with erpnext, hrms and helixhr, `allow_tests` on, Strict User Permissions on via `frappe.db.set_single_value`).
2. Serve it with `bench --site <site> serve`. Clear `frontend/tests/.auth` before e2e.
3. Run e2e inside the container if the port isn't exposed.

---

## Documentation

- `docs/runbook.md`: replace the "set-config" instructions in the Backdated leave section and the config table with the Settings tab and the migrate note.
- `docs/architecture.md`: settings audience (HR tabs vs Portal Admin tabs).

---

## Sources

- `docs/runbook.md` "Backdated leave: the HelixHR grace rule, not HRMS's restriction"
- Prior admin-only move: merge `9cc51e4` (`fix(templates): Email templates page is Portal Admin / System Manager only`, `f96d80c`)
