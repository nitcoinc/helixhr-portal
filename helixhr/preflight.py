"""Go-live preflight: machine-checks the host-side settings the runbook's
checklist asks a human to eyeball.

Run on every site after deploying, before letting people in:

    bench --site <site> execute helixhr.preflight.run

Nothing here lives in the repo -- System Settings, Website Settings,
site_config.json and User Permissions are all per-site data -- so CI can
never see it, and the same command has to be run on staging and again on
production. Exit status is non-zero when any FAIL remains, so it can gate a
deploy script.

Sign-in phase is site config, not a code comment: `helixhr_auth_phase` is
"local" (the default -- password login must stay on, since turning it off
with no enabled Social Login Key locks everyone out) or "entra" (the Office
365 key must be enabled and password login must be off). Setting the phase
is what flips both expectations; nothing here has to be edited at go-live.

P2-U9 added the checks that judge *values* rather than presence: the exact
upload extension/size/privacy policy, every named per-user write bound,
`allow_tests`, `ignore_csrf`, and -- given `helixhr_public_url` -- a real
HTTPS fetch that inspects the security headers and the sid cookie's flags.

P3-U1 (P3-R26) added the check-in prerequisites: the attendance workflow
fixture, the HR Settings check-in flags, at least one Shift Type with auto
attendance that can still mark attendance, the effective `Permissions-Policy`
allowing geolocation for self, the coordinate retention key, and a FAIL when
a doctype in the permission-delta table carries no Custom DocPerm row at all.

P4-U6 added the mail checks: the two celebration-reminder senders must not
both be on for the same event (P4-R18), a default outgoing Email Account is
what the HR-queue notifications and the reminders both need -- and a FAIL
without it, because the notifications send inside the escalating save, so
Send to HR is refused rather than merely unannounced -- and an HR Manager
scoped to their own Employee record has no HR queue (P4-R11).
"""

import os

import frappe
from frappe.utils import cint, flt

from helixhr.patches.v1_0.apply_permission_deltas import DELTAS
from helixhr.utils import (
	DOCUMENT_MAX_BYTES,
	DOCUMENT_POLICY,
	PROFILE_CORRECTION_CATEGORY,
	PROFILE_EDITABLE_FIELDS,
	RATE_LIMIT_POLICY,
	portal_home_page,
	rate_limit_bounds,
)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
IT_TEAM = "IT Team"
IT_TEAM_HR_REQUEST_PERMLEVEL_ONE_FIELDS = frozenset(
	{"status", "hr_note", "helixhr_decision_reason", "picked_up_by", "routed_to_role"}
)


def run():
	results = [check() for check in CHECKS]
	width = max(len(r["name"]) for r in results)
	print(f"\nHelixHR preflight -- {frappe.local.site}\n")
	for r in results:
		print(f"  {r['status']:<4} {r['name']:<{width}}  {r['detail']}")
	failed = [r for r in results if r["status"] == FAIL]
	warned = [r for r in results if r["status"] == WARN]
	print(f"\n  {len(results) - len(failed) - len(warned)} pass, {len(warned)} warn, {len(failed)} fail\n")
	if failed:
		raise SystemExit(1)
	return results


def _result(name, status, detail):
	return {"name": name, "status": status, "detail": detail}


def _system(field):
	return frappe.db.get_single_value("System Settings", field)


def _hr_setting(field):
	return frappe.db.get_single_value("HR Settings", field)


# --- authorization ----------------------------------------------------------


def check_strict_user_permissions():
	on = frappe.utils.cint(_system("apply_strict_user_permissions"))
	return _result(
		"Apply Strict User Permissions",
		PASS if on else FAIL,
		"on" if on else "off -- a User Permission on Employee does not restrict linked doctypes without it",
	)


def _hr_manager_users():
	"""The enabled logins holding HR Manager, which two checks need to agree
	about: this role must *not* be self-scoped (P4-R11)."""
	holders = set(
		frappe.get_all(
			"Has Role",
			filters={"role": "HR Manager", "parenttype": "User"},
			pluck="parent",
		)
	) - {"Administrator", "Guest"}
	if not holders:
		return set()
	return set(
		frappe.get_all("User", filters={"enabled": 1, "name": ["in", list(holders)]}, pluck="name")
	)


def check_employee_user_permissions():
	"""Every Employee with a portal login must be scoped to their own record.
	Without the User Permission, that user can read every employee.

	HR Managers are the deliberate exception (P4-R11): a User Permission on
	their own Employee record silently empties their HR queue, because it
	beats HR Manager's own read permission inside
	`frappe.model.workflow.get_transitions`. `check_hr_manager_self_scope`
	owns that case and wants the opposite answer, so counting them as
	missing here would leave every real site with an HR Manager employee
	holding a FAIL that must not be fixed.
	"""
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"]},
		fields=["name", "user_id"],
	)
	scoped = {
		(row.user, row.for_value)
		for row in frappe.get_all(
			"User Permission", filters={"allow": "Employee"}, fields=["user", "for_value"]
		)
	}
	exempt = _hr_manager_users()
	missing = sorted(
		e.user_id
		for e in employees
		if e.user_id not in exempt and (e.user_id, e.name) not in scoped
	)
	if not employees:
		return _result("Employee User Permissions", WARN, "no active Employee has a user_id yet")
	if missing:
		shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
		return _result(
			"Employee User Permissions",
			FAIL,
			f"{len(missing)} of {len(employees)} linked employees have no User Permission: {shown}",
		)
	detail = f"all {len(employees)} linked employees scoped"
	if exempt:
		detail += f" ({len(exempt)} HR Manager login(s) exempt -- see HR queue scoping)"
	return _result("Employee User Permissions", PASS, detail)


def check_custom_docperm_coverage():
	"""Frappe *discards* a doctype's standard DocPerm rows once it has any
	Custom DocPerm row rather than merging them
	(frappe.permissions.get_valid_perms), so a partial set of Custom DocPerm
	rows silently removes every role it does not name.

	`patches.v1_0.apply_permission_deltas` is what keeps that from happening:
	it copies this site's own standard rows in (frappe.permissions
	.setup_custom_perms) before applying this app's deltas. A patch runs once,
	so this is the standing guard afterwards -- an operator editing rules in
	the Role Permissions Manager, or a restored site that missed the patch,
	shows up here. FAIL names the roles that have been left with nothing.

	P3-KTD13: the doctype list is the patch's own delta table, and a doctype
	named there with *no* Custom DocPerm row is a FAIL too -- it means the
	delta never ran (a site migrated before its dated re-run line landed in
	patches.txt), so an employee still holds HRMS's shipped create, write
	and delete on Employee Checkin.

	The deltas themselves are checked value by value, not by presence.
	"Some Custom DocPerm row exists for Employee Checkin" was true on a site
	where somebody had handed role Employee its create and delete back in the
	Role Permissions Manager, and this check said the deltas were carried.
	Each `(role, permlevel, if_owner)` rule the patch names is compared
	ptype by ptype against the row on the site.
	"""
	problems = []
	for doctype, deltas in DELTAS.items():
		ptypes = sorted({ptype for _rule, values in deltas for ptype in values})
		rows = frappe.get_all(
			"Custom DocPerm",
			filters={"parent": doctype},
			fields=["role", "permlevel", "if_owner", *ptypes],
		)
		if not rows:
			problems.append(f"{doctype}: no Custom DocPerm row at all, the permission delta never ran")
			continue

		by_rule = {(row.role, cint(row.permlevel), cint(row.if_owner)): row for row in rows}
		for (role, permlevel, if_owner), values in deltas:
			row = by_rule.get((role, cint(permlevel), cint(if_owner)))
			scope = f"{role} level {permlevel}" + (" (own records)" if cint(if_owner) else "")
			if not row:
				problems.append(f"{doctype}: {scope} has no rule at all")
				continue
			wrong = [
				f"{ptype}={cint(row.get(ptype))} not {cint(expected)}"
				for ptype, expected in values.items()
				if cint(row.get(ptype)) != cint(expected)
			]
			if wrong:
				problems.append(f"{doctype}: {scope} {', '.join(wrong)}")

		standard = set(
			frappe.get_all("DocPerm", filters={"parent": doctype}, pluck="role", parent_doctype="DocType")
		)
		lost = sorted(standard - {row.role for row in rows})
		if lost:
			problems.append(f"{doctype}: {', '.join(lost)}")
	if problems:
		return _result(
			"Custom DocPerm coverage",
			FAIL,
			"; ".join(problems) + " -- re-run helixhr.patches.v1_0.apply_permission_deltas",
		)
	return _result(
		"Custom DocPerm coverage",
		PASS,
		f"{len(DELTAS)} customised doctypes carry their deltas and no role lost access",
	)


def check_leave_approver_mandatory():
	"""P2-R14: HR Settings, not portal copy, is what refuses a leave request
	from an employee whose approver was never set. Without it the request is
	created and then waits on nobody."""
	on = frappe.utils.cint(_hr_setting("leave_approver_mandatory_in_leave_application"))
	return _result(
		"Leave approver mandatory",
		PASS if on else FAIL,
		"on"
		if on
		else "off -- a leave request from an employee with no approver would be accepted and wait on nobody",
	)


def check_self_leave_approval_blocked():
	"""P2-U1 step 3: an employee who is also a leave approver (any manager)
	must not be able to approve their own leave. HRMS enforces this natively
	once the setting is on."""
	on = frappe.utils.cint(_hr_setting("prevent_self_leave_approval"))
	return _result(
		"Self leave approval blocked",
		PASS if on else FAIL,
		"on" if on else "off -- an approver could approve their own leave request",
	)


def check_backdated_leave_grace():
	"""R7: the HelixHR grace rule (R6) replaces HRMS's
	`restrict_backdated_leave_application`, which checks the session user on
	every validate and so would block an approver submitting a late request.
	Both on at once is a FAIL, as is an exempt role that does not exist. The
	PASS detail shows the effective N."""
	from helixhr.events import backdated_grace_days, leave_rule_stored

	problems = []
	if frappe.utils.cint(_hr_setting("restrict_backdated_leave_application")):
		problems.append(
			"HR Settings restrict_backdated_leave_application is on -- it would block approvers"
			" submitting late requests; turn it off, the HelixHR grace rule covers backdating"
		)
	# Plan 2026-10-06-001 U1: the exempt role now lives in the HelixHR Leave
	# Rules Single, set from the Settings Leave rules tab; a role can be
	# deleted in Desk after it was chosen, so this still checks.
	role = (leave_rule_stored("backdated_exempt_role") or "").strip()
	if role and not frappe.db.exists("Role", role):
		problems.append(f"the Leave rules exempt role does not exist: {role}")
	if problems:
		return _result("Backdated leave grace", FAIL, "; ".join(problems))
	grace = backdated_grace_days()
	exempt = "HR Manager" + (f" and {role}" if role else "")
	return _result(
		"Backdated leave grace",
		PASS,
		f"leave may start up to {grace} working day{'' if grace == 1 else 's'} back; {exempt} unlimited",
	)


def check_unsubmitted_approved_leave():
	"""P2-R10 / P2-U1 step 4: rows the pre-P2-U1 portal marked Approved
	without submitting. They consumed no balance and wrote no ledger entry,
	so HR has to submit or reject each one in Desk. Deliberately a WARN:
	nothing is broken going forward, but the backlog is real and only a
	human can decide each case."""
	count = frappe.db.count("Leave Application", {"docstatus": 0, "status": "Approved"})
	if count:
		return _result(
			"Approved-but-unsubmitted leave",
			WARN,
			f"{count} leave request(s) say Approved but were never submitted and consumed no balance "
			"-- submit or reject each one in Desk (see patches/v1_0/report_unsubmitted_approved_leave)",
		)
	return _result("Approved-but-unsubmitted leave", PASS, "none")


def check_document_link_urls():
	"""P2-R19: every stored document link is a plain HTTP(S) address.

	The doctype validates on save, and nothing revalidates a row that is
	never saved again -- a `javascript:` or `data:` link written before
	that rule existed still renders into an `:href`. A FAIL rather than a
	WARN: the row is one click from executing in the reader's page, and
	the fix is to edit or delete it in Desk.
	"""
	from helixhr.helixhr.doctype.helixhr_document_link.helixhr_document_link import (
		document_url_problem,
	)

	bad = [
		row.name
		for row in frappe.get_all("HelixHR Document Link", fields=["name", "url", "file"])
		# An uploaded document has no link to check; one with neither is bad.
		if (row.url or not row.file) and document_url_problem(row.url)
	]
	if bad:
		return _result(
			"Document link URLs",
			FAIL,
			f"{len(bad)} link(s) are not http(s) -- fix or delete in Desk: " + ", ".join(bad[:5]),
		)
	return _result("Document link URLs", PASS, "all http(s)")


# --- sign-in (local-login phase) -------------------------------------------


def _entra_enabled():
	return bool(
		frappe.db.exists(
			"Social Login Key", {"social_login_provider": "Office 365", "enable_social_login": 1}
		)
	)


def check_portal_landing():
	"""Employees must land on the portal, not on Desk.

	`helixhr.utils.portal_home_page` is registered as
	`get_website_user_home_page`, but Frappe consults two Desk-editable
	settings *before* it and one *after* the whole chain, and any of them
	silently sends employees back to Desk with no error anywhere:

	- a `home_page` on the Role doctype wins over every hook;
	- Portal Settings' "Default Portal Home" wins over every hook;
	- a `default_workspace` on the User overrides even the resolved answer.

	This is a FAIL rather than a WARN because the symptom -- "our people keep
	ending up in ERPNext" -- reads as a portal bug and is very hard to trace
	back to a field somebody set in Desk months earlier.
	"""
	problems = []

	for role in ("Employee", "Employee Self Service"):
		if not frappe.db.exists("Role", role):
			continue
		home = frappe.db.get_value("Role", role, "home_page")
		if home:
			problems.append(f"Role {role} sets home page {home!r}, which wins over the app's landing rule")

	portal_home = frappe.db.get_single_value("Portal Settings", "default_portal_home")
	if portal_home:
		problems.append(f"Portal Settings' default portal home is {portal_home!r}, which wins over the app's landing rule")

	pinned = frappe.get_all(
		"User",
		filters={"enabled": 1, "default_workspace": ["is", "set"], "name": ["not in", ("Administrator", "Guest")]},
		pluck="name",
	)
	stuck = [user for user in pinned if portal_home_page(user)]
	if stuck:
		shown = ", ".join(stuck[:5]) + (f" and {len(stuck) - 5} more" if len(stuck) > 5 else "")
		problems.append(f"{len(stuck)} portal user(s) have a default workspace pinned, which overrides it: {shown}")

	if problems:
		return _result("Portal landing", FAIL, "; ".join(problems))
	return _result("Portal landing", PASS, "employees land on /helixhr; Desk users are untouched")


# Employee fields a fresh site legitimately leaves at permlevel 0 beyond the
# employee's own editable set: the nested-set tree bookkeeping, which no
# form exposes. Anything else at level 0 is writable by the employee through
# the raw API.
EMPLOYEE_LEVEL_ZERO_EXEMPT = frozenset({"lft", "rgt", "old_parent"})


def check_employee_open_fields():
	"""Every Employee value field at effective permlevel 0 is one the employee
	may edit (`PROFILE_EDITABLE_FIELDS`) or a reviewed exemption.

	Level 0 is what role Employee writes. A field that lands there by default
	-- a site's own Custom Field, or a regional one HRMS adds when an Indian
	company is set up -- is silently self-editable: the four India payroll
	fields sat there until the fixture locked them. Named here so HR decides
	each one (a permlevel Property Setter via Customize Form) instead of
	finding out from a changed PAN.

	Child tables count: a level-0 Table field lets the employee add and edit
	its rows. A site that decides a field of its own really is
	employee-editable names it in `helixhr_employee_open_fields_exempt`
	(site config, a list) rather than editing this module.
	"""
	from frappe.model import display_fieldtypes

	site_exempt = frappe.conf.get("helixhr_employee_open_fields_exempt") or []
	allowed = set(PROFILE_EDITABLE_FIELDS) | EMPLOYEE_LEVEL_ZERO_EXEMPT | set(site_exempt)
	open_fields = sorted(
		field.fieldname
		for field in frappe.get_meta("Employee").fields
		if field.fieldtype not in display_fieldtypes
		and not cint(field.permlevel)
		and field.fieldname not in allowed
	)
	if open_fields:
		return _result(
			"Employee field locks",
			FAIL,
			f"employee-writable at permlevel 0: {', '.join(open_fields)} -- give each a permlevel "
			"(Customize Form), or list it in site config helixhr_employee_open_fields_exempt",
		)
	return _result("Employee field locks", PASS, "only the employee's own contact fields are at level 0")


def check_it_team_role():
	"""P5-U2: IT Team stays portal-only and its writable request fields are reviewed."""
	problems = []
	role = frappe.db.get_value("Role", IT_TEAM, ["desk_access", "is_custom"], as_dict=True)
	if not role:
		problems.append("Role fixture is missing")
	else:
		if cint(role.desk_access):
			problems.append("desk_access must be 0")
		if cint(role.is_custom):
			problems.append("is_custom must be 0")

	actual = {
		field.fieldname
		for field in frappe.get_meta("HR Request").fields
		if cint(field.permlevel) == 1
	}
	if actual != IT_TEAM_HR_REQUEST_PERMLEVEL_ONE_FIELDS:
		problems.append(
			"HR Request permlevel-1 fields are not reviewed: "
			f"expected {sorted(IT_TEAM_HR_REQUEST_PERMLEVEL_ONE_FIELDS)}, got {sorted(actual)}"
		)

	if problems:
		return _result("IT Team role", FAIL, "; ".join(problems) + " -- run bench migrate")
	return _result("IT Team role", PASS, "portal-only role and HR Request field inventory reviewed")


DELIVERY_MANAGER = "HelixHR Delivery Manager"


def check_delivery_manager_role():
	"""P7-U1: HelixHR Delivery Manager stays portal-only, never acquires the
	doctype-wide `report` permission on Timesheet (KTD3 -- that grant would
	hand the holder every Timesheet report, including
	`Timesheet Billing Summary`'s `billing_amount`), and keeps both its
	Project/Task scope hooks registered -- a hook silently dropped from the
	config is indistinguishable from an unscoped role at runtime (KTD8)."""
	problems = []
	role = frappe.db.get_value("Role", DELIVERY_MANAGER, ["desk_access", "is_custom"], as_dict=True)
	if not role:
		problems.append("Role fixture is missing")
	else:
		if cint(role.desk_access):
			problems.append("desk_access must be 0")
		if cint(role.is_custom):
			problems.append("is_custom must be 0")

	if frappe.db.get_value(
		"Custom DocPerm", {"parent": "Timesheet", "role": DELIVERY_MANAGER, "report": 1}
	):
		problems.append("HelixHR Delivery Manager holds report permission on Timesheet")
	if frappe.db.get_value("Custom DocPerm", {"parent": "Employee", "role": DELIVERY_MANAGER, "read": 1}):
		problems.append("HelixHR Delivery Manager holds read on Employee")

	for doctype in ("Project", "Task"):
		conditions = frappe.get_hooks("permission_query_conditions", {}).get(doctype, [])
		if "helixhr.project_permissions.get_permission_query_conditions" not in conditions:
			problems.append(f"{doctype} has no permission-query-conditions hook registered")
		checks = frappe.get_hooks("has_permission", {}).get(doctype, [])
		if "helixhr.project_permissions.has_permission" not in checks:
			problems.append(f"{doctype} has no has_permission hook registered")

	if problems:
		return _result("HelixHR Delivery Manager role", FAIL, "; ".join(problems) + " -- run bench migrate")
	return _result(
		"HelixHR Delivery Manager role",
		PASS,
		"portal-only role, no Timesheet report grant, Project/Task hooks registered",
	)


def check_signup_disabled():
	off = frappe.utils.cint(frappe.db.get_single_value("Website Settings", "disable_signup"))
	return _result(
		"Disable Signup",
		PASS if off else FAIL,
		"on" if off else "off -- an unknown sign-in would self-register instead of seeing 'contact HR'",
	)


def _auth_phase():
	"""Which sign-in phase this site declares it is in: "local" (the default)
	or "entra". Set it with

	    bench --site <site> set-config helixhr_auth_phase entra

	so that the two checks below stop being phase-blind. Before P2-U9 the
	Entra expectation was a comment asking a human to "flip the two marked
	below" at go-live; a site config value is something preflight can judge.
	"""
	return (frappe.conf.get("helixhr_auth_phase") or "local").strip().lower()


def check_password_login():
	"""P2-U9 scenario 6: an internally inconsistent auth mode FAILs.

	The two inconsistencies that matter are opposites of each other -- no
	door at all (password login off, no enabled key) and two doors when the
	site says there should be one (Entra phase with password login still on,
	which is the whole point of moving to Entra).
	"""
	disabled = frappe.utils.cint(_system("disable_user_pass_login"))
	entra = _entra_enabled()
	phase = _auth_phase()

	if disabled and not entra:
		return _result(
			"Username/Password Login", FAIL, "disabled with no enabled Social Login Key -- nobody can sign in"
		)
	if phase == "entra" and not disabled:
		return _result(
			"Username/Password Login",
			FAIL,
			"helixhr_auth_phase is entra but password login is still enabled -- "
			"turn on System Settings > Disable Username/Password Login",
		)
	if disabled:
		return _result("Username/Password Login", PASS, "disabled; Entra ID is the only door")
	return _result("Username/Password Login", PASS, f"enabled ({phase}-login phase)")


_OFFICE365_CALLBACK = "frappe.integrations.oauth2_logins.login_via_office365"
_OFFICE365_LANDING = "helixhr.api.login_via_office365"


def check_entra():
	"""Phase-aware: informational while the site says it is on local login,
	a FAIL once it says it is on Entra and the key is not there."""
	phase = _auth_phase()
	if _entra_enabled():
		# `override_whitelisted_methods` is last-app-wins across the bench, so
		# another app overriding the same callback silently puts employees
		# back on Desk after Microsoft sign-in (helixhr.api.login_via_office365).
		resolved = frappe.override_whitelisted_method(_OFFICE365_CALLBACK)
		if resolved != _OFFICE365_LANDING:
			return _result(
				"Entra ID (Office 365 key)",
				FAIL,
				f"the Microsoft callback resolves to {resolved}, not {_OFFICE365_LANDING} -- "
				"employees will land on Desk after sign-in",
			)
		return _result(
			"Entra ID (Office 365 key)",
			PASS if phase == "entra" else WARN,
			"enabled -- verify the OAuth round trip by hand"
			if phase == "entra"
			else "enabled while helixhr_auth_phase is still local -- set the phase or disable the key",
		)
	if phase == "entra":
		return _result(
			"Entra ID (Office 365 key)",
			FAIL,
			"helixhr_auth_phase is entra but no Office 365 Social Login Key is enabled",
		)
	return _result("Entra ID (Office 365 key)", WARN, "not configured (expected in the local-login phase)")


def check_password_policy():
	on = frappe.utils.cint(_system("enable_password_policy"))
	return _result(
		"Password policy",
		PASS if on else WARN,
		"on" if on else "off -- with local login this is the only strength check",
	)


# --- uploads and rate limits ----------------------------------------------


# The extensions System Settings is allowed to list, as bare upper-case names
# in the form that field uses. Anything outside this set is a site that would
# accept a file the portal refuses -- SVG and HTML being the ones that matter,
# because both execute in the site's own origin. The widest portal policy is
# HR's published documents (attachment types plus PPTX, 20 MB); Frappe's own
# File checks apply underneath it, so the site must allow at least that much
# and no more.
_ALLOWED_EXTENSION_NAMES = {e.lstrip(".").upper() for e in DOCUMENT_POLICY}
_MAX_FILE_SIZE_MB = DOCUMENT_MAX_BYTES // (1024 * 1024)


def check_file_settings():
	"""P2-U9 step 7: the exact policy, not merely "a value is set".

	`helixhr.utils.validate_portal_upload` is the real gate for anything
	attached to an HR Request, and it needs no help from site settings. This
	check is about everything *else* a logged-in user can upload: an
	`allowed_file_extensions` list that still permits SVG or HTML, a
	`max_file_size` above the portal's own 20 MB (HR documents), guests uploading at all, or
	public uploads left open to non-System-Managers.
	"""
	raw = (_system("allowed_file_extensions") or "").strip()
	size = frappe.utils.cint(_system("max_file_size"))
	guests = frappe.utils.cint(_system("allow_guests_to_upload_files"))
	public_locked = frappe.utils.cint(_system("only_allow_system_managers_to_upload_public_files"))

	problems = []
	if not raw:
		problems.append("Allowed File Extensions unset -- every extension is accepted")
	else:
		listed = {line.strip().lstrip(".").upper() for line in raw.splitlines() if line.strip()}
		extra = sorted(listed - _ALLOWED_EXTENSION_NAMES)
		if extra:
			problems.append("Allowed File Extensions also permits " + ", ".join(extra))
	if not size:
		problems.append("Max File Size unset")
	elif size > _MAX_FILE_SIZE_MB:
		problems.append(f"Max File Size is {size} MB, above the {_MAX_FILE_SIZE_MB} MB policy")
	if guests:
		problems.append("Allow Guests to Upload Files is on")
	if not public_locked:
		problems.append("public uploads are not restricted to System Managers")

	if problems:
		return _result("Upload policy", FAIL, "; ".join(problems))
	return _result(
		"Upload policy",
		PASS,
		f"{', '.join(sorted(_ALLOWED_EXTENSION_NAMES))} only, max {size} MB, no guest or open public upload",
	)


# Plan 2026-09-30-001 R6: the before_save / on_update pair that keeps a
# private photo out of `User.user_image` on every full Employee save.
_EMPLOYEE_PHOTO_HOOKS = {
	"before_save": "helixhr.events.employee_before_save",
	"on_update": "helixhr.events.employee_on_update",
}


def check_employee_photo_hooks():
	"""Without both hooks, the next Employee save copies a private photo
	into `User.user_image` (a second read path) or crashes on a JPEG."""
	events = frappe.get_hooks("doc_events", {}).get("Employee", {})
	missing = [
		f"{event} -> {method}"
		for event, method in _EMPLOYEE_PHOTO_HOOKS.items()
		if method not in (events.get(event) or [])
	]
	if missing:
		return _result("Employee photo hooks", FAIL, "missing: " + "; ".join(missing))
	return _result("Employee photo hooks", PASS, "before_save and on_update registered")


def check_public_employee_photos():
	"""Legacy Desk photos stored public are readable by anyone with the URL.
	WARN, not FAIL: they predate the portal and need a re-upload, not a
	blocked deploy."""
	names = frappe.get_all("Employee", filters={"image": ["like", "/files/%"]}, pluck="name", order_by="name")
	if names:
		shown = ", ".join(names[:20]) + (f" (+{len(names) - 20} more)" if len(names) > 20 else "")
		return _result(
			"Public Employee photos",
			WARN,
			f"{len(names)} Employee(s) with a public /files photo; re-upload as private: {shown}",
		)
	return _result("Public Employee photos", PASS, "no Employee photo is a public /files URL")


def check_rate_limits():
	"""P2-U9 step 7: every named per-user write bound is present and no
	looser than policy.

	`helixhr.utils.rate_limit_bounds` re-derives what this site would
	actually enforce, site-config override included, so a loosened bound is
	visible here rather than only in a code review nobody ran.
	"""
	problems = []
	for action, (limit, seconds) in sorted(RATE_LIMIT_POLICY.items()):
		effective_limit, effective_seconds = rate_limit_bounds(action)
		# Compare rates, not raw limits: 40/2h is the same rate as 20/1h.
		if effective_limit * seconds > limit * effective_seconds:
			problems.append(
				f"{action} {effective_limit}/{effective_seconds}s is looser than {limit}/{seconds}s"
			)
	if problems:
		return _result("Per-user write limits", FAIL, "; ".join(problems))
	return _result(
		"Per-user write limits", PASS, f"{len(RATE_LIMIT_POLICY)} bounds at or tighter than policy"
	)


def check_test_mode():
	"""P2-U9 scenario 6. `allow_tests` opens the fixture entry points *and*
	turns off the per-user write limiter (`helixhr.utils.rate_limits_enforced`
	-- the suites and the limits are otherwise mutually exclusive). Both are
	fine on a test site and neither is survivable on a production one, which
	is what makes this a FAIL rather than a note."""
	on = frappe.utils.cint(frappe.conf.get("allow_tests"))
	if on:
		return _result(
			"Test mode off",
			FAIL,
			"allow_tests is on -- fixture seeding is callable and per-user write limits are disabled "
			"(bench --site <site> set-config allow_tests false)",
		)
	return _result("Test mode off", PASS, "allow_tests is off")


def check_csrf():
	"""The starter advice `frontend/README.md` used to give, found in
	production. With `ignore_csrf` set, every whitelisted POST in this app is
	callable cross-origin from a page the employee happens to be reading."""
	if frappe.utils.cint(frappe.conf.get("ignore_csrf")):
		return _result(
			"CSRF protection",
			FAIL,
			"ignore_csrf is set -- every mutation is callable cross-site "
			"(bench --site <site> set-config ignore_csrf 0)",
		)
	return _result("CSRF protection", PASS, "enforced on every mutation")


def check_public_endpoint():
	"""P2-U9 step 8, as far as a site can see it.

	Cookie flags and response headers are properties of what the *proxy*
	serves, so this is the one check that leaves the site: given
	`helixhr_public_url`, it fetches the portal over the real hostname and
	inspects what came back. Without that setting it stays a WARN naming the
	host-only sign-off in docs/runbook.md rather than a PASS nobody earned.
	"""
	url = (frappe.conf.get("helixhr_public_url") or "").strip()
	if not url:
		return _result(
			"HTTPS headers and cookies",
			WARN,
			"not checked -- host-only sign-off (docs/runbook.md). "
			"bench --site <site> set-config helixhr_public_url https://<host>/helixhr to check it here",
		)
	if not url.startswith("https://"):
		return _result("HTTPS headers and cookies", FAIL, f"helixhr_public_url is not https: {url}")

	import requests

	try:
		response = requests.get(url, timeout=10, allow_redirects=False)
	except Exception as exception:  # network, DNS, TLS -- all the same answer here
		return _result("HTTPS headers and cookies", FAIL, f"could not reach {url}: {exception}")

	headers = {key.lower(): value for key, value in response.headers.items()}
	problems = []
	if "strict-transport-security" not in headers:
		problems.append("no Strict-Transport-Security")
	if "frame-ancestors" not in (headers.get("content-security-policy") or ""):
		problems.append("no Content-Security-Policy frame-ancestors")
	if headers.get("x-content-type-options", "").lower() != "nosniff":
		problems.append("no X-Content-Type-Options: nosniff")
	if "referrer-policy" not in headers:
		problems.append("no Referrer-Policy")
	# P3-KTD12 / P3-AE13: the value, not the presence. A proxy that sets
	# `geolocation=()` wins over the app's `setdefault`, and the browser then
	# reports a denial the check-in sheet cannot tell from the user's choice.
	permissions_policy = headers.get("permissions-policy")
	if permissions_policy is None:
		problems.append("no Permissions-Policy")
	else:
		# The *effective* directive, not a substring: a proxy that appends
		# its own `geolocation=()` after the app's `geolocation=(self)`
		# leaves both in the header, the browser denies, and a plain
		# substring match passed the site anyway (P3-KTD12 / P3-AE13).
		compact = permissions_policy.replace(" ", "")
		if "geolocation=()" in compact or "geolocation=(self)" not in compact:
			problems.append(
				f"Permissions-Policy does not allow geolocation for self: {permissions_policy!r}"
			)

	# requests folds repeated Set-Cookie headers into one comma-joined string
	# on `.headers`; urllib3 keeps them separate on `.raw`. Prefer the raw
	# list where it exists, because the joined form makes "which attribute
	# belongs to which cookie" ambiguous.
	raw = getattr(response, "raw", None)
	raw_headers = getattr(raw, "headers", None)
	if raw_headers is not None and hasattr(raw_headers, "getlist"):
		cookie_lines = raw_headers.getlist("Set-Cookie")
	else:
		cookie_lines = [response.headers.get("Set-Cookie") or ""]
	sid = next((line for line in cookie_lines if "sid=" in line), "")
	if not sid:
		problems.append("no sid cookie was set")
	else:
		lowered = sid.lower()
		for flag in ("secure", "httponly", "samesite"):
			if flag not in lowered:
				problems.append(f"sid cookie has no {flag} attribute")

	if problems:
		return _result("HTTPS headers and cookies", FAIL, "; ".join(problems))
	return _result("HTTPS headers and cookies", PASS, f"{url}: headers and sid cookie flags correct")


def check_site_rate_limit():
	conf = frappe.conf.get("rate_limit")
	if conf and conf.get("limit") and conf.get("window"):
		return _result("Site rate_limit", PASS, f"{conf['limit']} requests per {conf['window']}s")
	return _result(
		"Site rate_limit", WARN, "unset -- only the app's own per-user limits on writes are active"
	)


# --- app configuration ----------------------------------------------------


def check_hr_contact():
	value = frappe.conf.get("helixhr_hr_contact")
	if value:
		return _result("HR contact address", PASS, value)
	return _result(
		"HR contact address",
		WARN,
		"helixhr_hr_contact unset -- the not-linked page shows no address (bench set-config helixhr_hr_contact ...)",
	)


def check_fixtures():
	expected = [
		("Workflow", "Timesheet Approval"),
		# P4-U1: the single-step attendance approval, with Pending HR reached
		# only by Send to HR (was two mandatory steps in P3).
		("Workflow", "Attendance Request Approval"),
		("Workflow", "HR Request Handling"),
		# Its two new states. A Workflow's `workflow_state` values are Links
		# and fixture import runs with `ignore_links`, so a Workflow State
		# row that never installed leaves the workflow itself looking fine.
		("Workflow State", "Pending Manager"),
		("Workflow State", "Pending HR"),
		# P4-KTD1: the state that carries the recoverable "sent back"
		# meaning on both workflows, and the two actions that reach the new
		# outcomes. Same reason as above -- a Workflow's action and state
		# names are Links, and the import runs with `ignore_links`.
		("Workflow State", "Sent Back"),
		("Workflow State", "Waiting on Employee"),
		("Workflow Action Master", "Send Back"),
		("Workflow Action Master", "Send to HR"),
		("Workflow Action Master", "Pick up"),
		("Workflow Action Master", "Need info"),
		("Workflow Action Master", "Done"),
		# Plan 2026-10-04-003 U1: Recall and Cancel, and the Cancelled state
		# they need (same Link/ignore_links reason as the rows above).
		("Workflow State", "Cancelled"),
		("Workflow Action Master", "Recall"),
		("Workflow Action Master", "Cancel"),
		("Activity Type", "General"),
		("Notification", "HelixHR Timesheet Status Changed"),
		("Notification", "HelixHR Leave Status Changed"),
		# The four HR-queue email fixtures (P4-KTD9) were retired by plan
		# 2026-10-02-001 U9; `check_retired_hr_email_notifications` guards
		# that they stay gone.
	]
	missing = [f"{dt} '{name}'" for dt, name in expected if not frappe.db.exists(dt, name)]
	if missing:
		return _result("Fixtures installed", FAIL, "missing " + ", ".join(missing) + " -- run bench migrate")
	return _result("Fixtures installed", PASS, f"{len(expected)} checked")


def check_retired_request_notifications():
	"""P5-U4 / P5-KTD8: the two Notification fixtures the routed-request
	release retired -- the bell-only arrival one (it cannot route to a
	category's role) and the hardcoded status ladder (it would call a
	`Waiting on Employee` request "open", and double every employee-facing
	notification the new code path sends) -- must never come back enabled.

	Neither is in `helixhr/fixtures/notification.json` any more, so a fresh
	site never creates them and this passes trivially. An existing site
	that had already installed them before this plan gets them disabled by
	`helixhr.patches.v1_0.retire_request_notifications`; this is the standing
	guard that a later `bench migrate` regression, or a Desk re-enable,
	cannot bring either one back silently.
	"""
	retired = ("HelixHR New Request For HR", "HelixHR Request Status Changed")
	enabled = [
		name for name in retired if frappe.db.get_value("Notification", name, "enabled")
	]
	if enabled:
		return _result(
			"Retired request notifications",
			FAIL,
			f"{', '.join(enabled)} still enabled -- run helixhr.patches.v1_0.retire_request_notifications",
		)
	return _result("Retired request notifications", PASS, f"{len(retired)} confirmed absent or disabled")


def check_retired_hr_email_notifications():
	"""Plan 2026-10-02-001 U9 / KTD9: HR's "waiting for you" mail is a
	templated doc-event send now, so any of the four retired fixture email
	Notifications coming back -- a restored site, a Desk re-create -- is a
	second, untemplated copy of every one of those emails."""
	from helixhr.patches.v1_0.retire_hr_email_notifications import RETIRED_NOTIFICATIONS

	present = [name for name in RETIRED_NOTIFICATIONS if frappe.db.exists("Notification", name)]
	if present:
		return _result(
			"Retired HR email notifications",
			FAIL,
			f"{', '.join(present)} still present -- run helixhr.patches.v1_0.retire_hr_email_notifications",
		)
	return _result(
		"Retired HR email notifications", PASS, f"{len(RETIRED_NOTIFICATIONS)} confirmed absent"
	)


def check_hrms_leave_notification():
	"""Plan 2026-10-02-001 R21 / KTD10: HelixHR is the only sender of leave
	email. `events.hr_settings_validate` refuses re-enabling HRMS's
	`send_leave_notification`; this is the backstop for routes that skip
	`validate` (a raw `set_single_value`, a restored site)."""
	if cint(_hr_setting("send_leave_notification")):
		return _result(
			"HRMS leave notification",
			FAIL,
			"HR Settings 'Send Leave Notification' is on, so every leave email goes out twice -- "
			"run helixhr.patches.v1_0.turn_off_hrms_leave_notification",
		)
	return _result("HRMS leave notification", PASS, "off; HelixHR sends leave email")


def check_standard_working_hours():
	"""Plan 2026-10-04-003 KTD7: the queue's "hours off" flag measures a
	week against `HR Settings.standard_working_hours`. Unset, the flag
	stays off -- never wrong, just quiet -- so this is a WARN and says what
	went quiet rather than failing a site that measures hours another way."""
	value = flt(frappe.db.get_single_value("HR Settings", "standard_working_hours"))
	if not value:
		return _result(
			"Standard working hours",
			WARN,
			"HR Settings has no standard working hours, so the approval queue's "
			"hours flag stays off -- set it to measure weeks against a full one",
		)
	return _result("Standard working hours", PASS, f"{value} hours a day")


def check_hr_request_workflow_state_order():
	"""P5-KTD4: `Open` must be `states[0]` on the `HR Request Handling`
	workflow, or every `create_my_request` throws -- `HR Request.status`
	defaults to `Open`, and `validate_workflow` has no `_doc_before_save` on
	insert, so it takes the *first* state row as ground truth and refuses a
	document that disagrees with it. A future edit to the fixture (in Desk,
	or a later patch) that reorders the states breaks every request filed
	after it, silently, until someone happens to try.
	"""
	if not frappe.db.exists("Workflow", "HR Request Handling"):
		return _result("HR Request workflow state order", WARN, "HR Request Handling workflow not installed")
	states = frappe.get_doc("Workflow", "HR Request Handling").states
	if not states or states[0].state != "Open":
		return _result(
			"HR Request workflow state order",
			FAIL,
			f"states[0] is {states[0].state if states else 'missing'}, not Open -- every new request will throw",
		)
	return _result("HR Request workflow state order", PASS, "Open is states[0]")


def check_timesheet_workflow_state_order():
	"""Plan 2026-10-04-003 U1 / KTD2: the Timesheet workflow's state order is
	load-bearing the same way the HR Request one is, with two extra edges.

	`Workflow.on_update` backfills a null `workflow_state` by *state order*:
	docstatus-0 rows take the first state, docstatus-1 rows the first state
	with doc_status 1, so `Draft` must stay `states[0]` and `Approved` the
	first doc_status-1 state -- reordering the fixture silently re-stamps
	every legacy row's state. `Cancelled` must be present as the one
	docstatus-2 state and last in the list, because the fixture is
	append-only by design (an insert in the middle would shift what the
	backfill picks)."""
	if not frappe.db.exists("Workflow", "Timesheet Approval"):
		return _result("Timesheet workflow state order", WARN, "Timesheet Approval workflow not installed")
	workflow = frappe.get_doc("Workflow", "Timesheet Approval")
	states = workflow.states
	names = [row.state for row in states]
	if not names or names[0] != "Draft":
		return _result(
			"Timesheet workflow state order",
			FAIL,
			f"states[0] is {names[0] if names else 'missing'}, not Draft -- legacy rows would backfill into a pending state",
		)
	first_submitted = next((row for row in states if str(row.doc_status) == "1"), None)
	if not first_submitted or first_submitted.state != "Approved":
		return _result(
			"Timesheet workflow state order",
			FAIL,
			f"the first doc_status 1 state is {first_submitted.state if first_submitted else 'missing'}, not Approved",
		)
	if "Cancelled" not in names:
		return _result("Timesheet workflow state order", FAIL, "Cancelled state is missing")
	if names[-1] != "Cancelled":
		return _result(
			"Timesheet workflow state order",
			FAIL,
			f"Cancelled is {names.index('Cancelled') + 1} of {len(names)}, not last -- the fixture is append-only",
		)
	return _result("Timesheet workflow state order", PASS, "Draft first, Approved first submitted, Cancelled last")


def check_profile_correction_category():
	"""Profile's "Request a correction" files under this category. With it
	missing or retired the page falls back to a plain "contact HR" banner, so
	employees can still find their details but lose the one-click path."""
	active = frappe.db.get_value("HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "is_active")
	if active is None:
		return _result(
			"Profile correction category",
			WARN,
			f"{PROFILE_CORRECTION_CATEGORY!r} is missing or was renamed -- rename it back in Settings, "
			"or run helixhr.patches.v1_0.seed_profile_correction_category",
		)
	if not cint(active):
		return _result(
			"Profile correction category",
			WARN,
			f"{PROFILE_CORRECTION_CATEGORY!r} is inactive -- Profile shows 'contact HR' instead of a correction form",
		)
	return _result("Profile correction category", PASS, "active")


def check_request_category_routes():
	"""P5-KTD8's fallback ('a category whose role has no enabled holder
	falls back to HR Manager and logs it') is a runtime safety net, not a
	reason to leave the misconfiguration unnoticed at deploy time. An active
	category routed to a role nobody currently holds -- or holds but has
	disabled -- silently sends every new request in that category through
	the HR Manager fallback rather than the queue HR configured, which is
	exactly the "I file one and cannot find it" complaint this plan closes.
	"""
	if not frappe.db.exists("DocType", "HelixHR Request Category"):
		return _result("Request category routes", WARN, "HelixHR Request Category not installed")
	categories = frappe.get_all(
		"HelixHR Request Category", filters={"is_active": 1}, fields=["name", "route_to_role"]
	)
	unrouted = []
	for category in categories:
		holders = frappe.get_all(
			"Has Role", filters={"role": category.route_to_role, "parenttype": "User"}, pluck="parent"
		)
		enabled = frappe.get_all(
			"User",
			filters={"name": ["in", holders or [""]], "enabled": 1},
			pluck="name",
			limit=1,
		)
		if not enabled:
			unrouted.append(f"{category.name} -> {category.route_to_role}")
	if unrouted:
		return _result(
			"Request category routes",
			WARN,
			"no enabled holder, falls back to HR Manager: " + ", ".join(unrouted),
		)
	return _result("Request category routes", PASS, f"{len(categories)} active categor{'y' if len(categories) == 1 else 'ies'} routable")


def check_request_category_prefixes():
	"""Plan 2026-10-04-002 U1: a stored prefix that no longer matches
	`NAME_PREFIX_PATTERN` -- a fixture or Desk-side write that bypassed the
	doctype's own `validate` -- would build a broken naming series for every
	new request in that category. Naming accepts whatever the Data field
	carries, so the corruption surfaces as unusable IDs, not a save error.
	"""
	if not frappe.db.exists("DocType", "HelixHR Request Category"):
		return _result("Request category prefixes", WARN, "HelixHR Request Category not installed")
	from helixhr.helixhr.doctype.helixhr_request_category.helixhr_request_category import (
		NAME_PREFIX_PATTERN,
	)

	bad = [
		category.name
		for category in frappe.get_all("HelixHR Request Category", fields=["name", "name_prefix"])
		if category.name_prefix
		and (
			not NAME_PREFIX_PATTERN.fullmatch(category.name_prefix) or category.name_prefix.endswith("-")
		)
	]
	if bad:
		return _result(
			"Request category prefixes",
			FAIL,
			"stored prefix fails the format, would break naming: " + ", ".join(bad) + " -- fix it in Settings > Categories",
		)
	return _result("Request category prefixes", PASS, "stored prefixes match the format")


# --- check-in (P3-U1 step 6, P3-R26) ---------------------------------------


def check_checkin_settings():
	"""P3-R8: the portal offers the check-in button only while HR Settings
	allows check-in from the mobile app, so a site with it off has a
	feature that never appears -- a WARN, because a site may not want
	check-in at all. `allow_geolocation_tracking` is reported, not judged
	(P3-KTD4): the portal requires coordinates on its own, and turning the
	flag on also makes HRMS refuse coordinate-less device and Desk punches."""
	mobile = frappe.utils.cint(_hr_setting("allow_employee_checkin_from_mobile_app"))
	tracking = frappe.utils.cint(_hr_setting("allow_geolocation_tracking"))
	tracking_note = (
		"HRMS geolocation tracking on (every punch, from any source, needs coordinates)"
		if tracking
		else "HRMS geolocation tracking off (the portal still requires coordinates for its own punches)"
	)
	if not mobile:
		return _result(
			"Check-in settings",
			WARN,
			"Allow Employee Checkin From Mobile App is off -- the portal never shows the check-in button; "
			+ tracking_note,
		)
	return _result("Check-in settings", PASS, "mobile check-in allowed; " + tracking_note)


_LAST_SYNC_STALE_DAYS = 2


def check_shift_types():
	"""P3-KTD5 / P3-R26: a punch only ever becomes Attendance through a Shift
	Type with auto attendance whose `last_sync_of_checkin` keeps advancing
	(HRMS marks attendance only for punches before that timestamp) and whose
	`process_attendance_after` is set. Without one the button never appears;
	with a stalled one every punch stays a bare Employee Checkin and later
	reads as a missing day."""
	shifts = frappe.get_all(
		"Shift Type",
		filters={"enable_auto_attendance": 1},
		fields=["name", "process_attendance_after", "auto_update_last_sync", "last_sync_of_checkin"],
	)
	if not shifts:
		return _result(
			"Shift Types",
			WARN,
			"no Shift Type has Enable Auto Attendance -- check-in is not offered and punches never become attendance",
		)
	problems = []
	stale_before = frappe.utils.add_days(frappe.utils.now_datetime(), -_LAST_SYNC_STALE_DAYS)
	for shift in shifts:
		if not shift.process_attendance_after:
			problems.append(f"{shift.name}: Process Attendance After is empty")
		if not frappe.utils.cint(shift.auto_update_last_sync):
			last_sync = shift.last_sync_of_checkin and frappe.utils.get_datetime(shift.last_sync_of_checkin)
			if not last_sync:
				problems.append(f"{shift.name}: Last Sync of Checkin is empty and not auto-updated")
			elif last_sync < stale_before:
				problems.append(
					f"{shift.name}: Last Sync of Checkin is {shift.last_sync_of_checkin}, older than "
					f"{_LAST_SYNC_STALE_DAYS} days, and not auto-updated"
				)
	if problems:
		return _result("Shift Types", WARN, "; ".join(problems))
	return _result("Shift Types", PASS, f"{len(shifts)} auto-attendance shift type(s) can mark attendance")


def check_checkin_location_retention():
	"""P3-KTD15 / P3-R28: punch coordinates are erased after a site-configured
	number of days. The number is HR and legal's decision, so the job stays
	idle -- and this stays a WARN -- until the key is set."""
	days = frappe.conf.get("helixhr_checkin_location_retention_days")
	if days:
		return _result("Check-in location retention", PASS, f"coordinates erased after {days} days")
	return _result(
		"Check-in location retention",
		WARN,
		"helixhr_checkin_location_retention_days unset -- punch coordinates are kept indefinitely "
		"(bench --site <site> set-config helixhr_checkin_location_retention_days <days>)",
	)


def check_holiday_list_coverage():
	"""P3-R26: every active employee needs a Holiday List that resolves for
	today, through their own Holiday List Assignment or their company's.

	Without one the Holidays page says it cannot tell rather than showing a
	year (P3-R11), the attendance calendar cannot say which days were working
	days, and a Fix a day request cannot tell a holiday from a working day --
	so this is the setting whose absence is quietest and reaches furthest.
	"""
	from hrms.utils.holiday_list import get_holiday_list_for_employee

	employees = frappe.get_all(
		"Employee", filters={"status": "Active"}, fields=["name", "employee_name"], limit=2000
	)
	if not employees:
		return _result("Holiday list coverage", PASS, "no active employees yet")

	uncovered = []
	for employee in employees:
		try:
			if not get_holiday_list_for_employee(employee.name, raise_exception=False):
				uncovered.append(employee.employee_name or employee.name)
		except Exception:
			# A resolver that throws is itself an absent list from the
			# portal's point of view, and this check must never be the thing
			# that stops the preflight run.
			uncovered.append(employee.employee_name or employee.name)

	if not uncovered:
		return _result(
			"Holiday list coverage", PASS, f"{len(employees)} active employee(s) resolve a holiday list"
		)
	shown = ", ".join(uncovered[:5])
	more = f" and {len(uncovered) - 5} more" if len(uncovered) > 5 else ""
	return _result(
		"Holiday list coverage",
		FAIL,
		f"no holiday list resolves for {len(uncovered)} active employee(s): {shown}{more} "
		"-- assign one per employee or per company (Holiday List Assignment)",
	)


# --- celebration reminders and mail (P4-U6, P4-R18) ------------------------


def check_celebration_reminders():
	"""P4-R18 / P4-KTD10: a site must not be left sending two emails for the
	same event. Settings are per company since plan 2026-10-04-004 U1, so
	the check is per (event, company) row: any company's enabled row
	collides with HRMS's stock send.

	Frappe merges `scheduler_events` across apps and offers no way to remove
	HRMS's daily reminder job, so HRMS's stock email keeps going out while
	its own checkbox is ticked and HelixHR's branded one goes out while HR
	has picked a template. `events.hr_settings_validate` refuses the save
	that creates the contradiction; this is the backstop for the routes that
	never reach `validate` -- a fixture import, a raw `db_set`, a restored
	site.

	A picked template that does not exist is the other FAIL: the job logs it
	and sends nothing, so the event goes quiet with no other sign. Neither
	sender on anywhere is a WARN and not a FAIL -- a site may not want
	the email at all -- and either one on is a PASS naming which one sends.
	"""
	from helixhr.reminders import EVENTS

	problems, notes, quiet = [], [], []
	for event, spec in EVENTS.items():
		rows = frappe.get_all(
			"HelixHR Celebration Reminder",
			filters={"event": event},
			fields=["company", "is_enabled", "email_template"],
			order_by="company asc",
		)
		hrms_on = cint(_hr_setting(spec["hrms_field"]))
		enabled = [row for row in rows if row.is_enabled and row.email_template]
		bad = False
		if enabled and hrms_on:
			companies = ", ".join(row.company for row in enabled)
			problems.append(
				f"{spec['label']} ({companies}): both HRMS and HelixHR would send -- untick "
				f"'{spec['hrms_label']}' in HR Settings or disable it on the portal's "
				"Email templates page"
			)
			bad = True
		for row in enabled:
			if not frappe.db.exists("Email Template", row.email_template):
				problems.append(
					f"{spec['label']} ({row.company}): names Email Template "
					f"'{row.email_template}', which does not exist -- nothing is sent"
				)
				bad = True
			elif not bad:
				notes.append(f"{spec['label']} ({row.company}): HelixHR sends '{row.email_template}'")
		if not enabled and not hrms_on:
			# No company has this event on, and HRMS's checkbox is off: the
			# email is simply not wanted, which is a choice and not a defect.
			quiet.append(f"{spec['label']}: nobody sends")
		elif not enabled and hrms_on:
			# Desk has the HRMS checkbox read-only (P8-KTD8), so HR cannot
			# stop this mail anywhere -- the portal toggle only governs
			# HelixHR's own sender.
			problems.append(
				f"{spec['label']}: HRMS still sends its stock email and its checkbox is "
				"read-only in Desk -- run `bench migrate` (turn_off_hrms_celebration_senders)"
			)

	if problems:
		return _result("Celebration reminders", FAIL, "; ".join(problems))
	if quiet:
		return _result("Celebration reminders", WARN, "; ".join(quiet + notes))
	return _result("Celebration reminders", PASS, "; ".join(notes))


def check_cross_company_mailboxes():
	"""Plan 2026-10-04-004 U6 / R6: the data cause the send-time guard works
	around, surfaced.

	Two active Employees in different companies that resolve to one mailbox
	are the reported celebration bug's shape: a stale active duplicate in
	company B puts a company A person on B's list. The sender drops the
	overlap and logs it every run (R3); this check finds the pairs before
	anyone's birthday, naming the Employee records so HR can set the stale
	one to Left.

	The resolution is the pool helpers' own order (`user_id` ->
	`company_email` -> `personal_email`), one query. Sharing an address
	*within* one company is normal (a shared mailbox) and not warned; one
	of a spanning pair being Left removes the pair."""
	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "company", "user_id", "company_email", "personal_email"],
	)
	by_address = {}
	for row in rows:
		address = row.user_id or row.company_email or row.personal_email
		if not address:
			continue
		by_address.setdefault(address, []).append(row)

	problems = []
	for address, group in by_address.items():
		if len({row.company for row in group}) < 2:
			continue
		names = ", ".join(f"{row.name} ({row.company})" for row in group[:3])
		problems.append(f"{address}: {names}")
		if len(problems) >= 20:
			break

	if problems:
		count = (
			# The collection stops at 20, so say "at least" -- an exact-sounding
			# number that is silently a cap misleads a triage read.
			f"at least {len(problems)} " if len(problems) >= 20 else f"{len(problems)} "
		)
		return _result(
			"Cross-company mailboxes",
			WARN,
			f"{count}address(es) resolve for active employees of more than one "
			f"company -- celebration mail to them is dropped at send time (HelixHR "
			f"celebration reminders log); set the stale duplicate to Left: "
			+ "; ".join(problems),
		)
	return _result(
		"Cross-company mailboxes",
		PASS,
		"no mailbox resolves for active employees of more than one company",
	)


def check_outgoing_email():
	"""P4-R18: `frappe.sendmail` throws without a default outgoing Email
	Account. Since plan 2026-10-02-001 U9 every portal email is a templated
	doc-event send that logs the failure instead of failing the save, so a
	missing account no longer refuses any action -- it silently mails
	nobody: no approver, no HR queue, no employee decision, no celebration
	reminder. Still a FAIL for that reason.
	"""
	account = frappe.db.get_value(
		"Email Account", {"enable_outgoing": 1, "default_outgoing": 1}, "name"
	)
	if account:
		return _result("Outgoing email", PASS, f"default outgoing account '{account}'")
	return _result(
		"Outgoing email",
		FAIL,
		"no default outgoing Email Account -- no portal email is sent: approvers, HR queues, "
		"employee decisions and celebration reminders all go unannounced (Desk: Email Account)",
	)


def check_overdue_digests():
	"""Plan 2026-10-02-001 U11: the threshold the overdue digests use, and a
	WARN when the scheduler is off -- the digests then never go out."""
	from frappe.utils.scheduler import is_scheduler_disabled

	from helixhr.api import approval_overdue_days

	threshold = f"overdue after {approval_overdue_days()} day(s) (helixhr_approval_overdue_days)"
	if is_scheduler_disabled(verbose=False):
		return _result(
			"Overdue digests", WARN, f"scheduler disabled -- no overdue digest is sent; {threshold}"
		)
	return _result("Overdue digests", PASS, threshold)


def check_hr_manager_self_scope():
	"""P4-R11: an HR Manager with a User Permission on their own Employee
	record has no HR queue at all.

	A User Permission on Employee beats HR Manager's own read permission on
	Leave Application, Timesheet and Attendance Request, and it does so
	silently: `check_permission("read")` inside `frappe.model.workflow
	.get_transitions` throws for every row that is not theirs, so the
	Approvals page shows HR nothing but their own records and reads as an
	empty queue rather than as a permission problem. HR staff are therefore
	not scoped to themselves -- an Employee record created with HR Settings'
	"Create User Permission" ticked (the default) is where this comes from,
	so it is easy to arrive at by accident.

	Reported, not judged, and a WARN: whether a particular login is meant to
	work the HR queue is HR's call, and `check_employee_user_permissions`
	deliberately wants the scoping on everybody else.
	"""
	# The same helper `check_employee_user_permissions` exempts by, so the two
	# checks can never disagree about who holds the role.
	enabled = sorted(_hr_manager_users())
	if not enabled:
		return _result(
			"HR queue scoping", WARN, "no enabled user holds HR Manager -- nobody works the HR queue"
		)

	scoped = sorted(
		set(
			frappe.get_all(
				"User Permission",
				filters={"allow": "Employee", "user": ["in", enabled]},
				pluck="user",
			)
		)
	)
	if scoped:
		shown = ", ".join(scoped[:5]) + (f" and {len(scoped) - 5} more" if len(scoped) > 5 else "")
		return _result(
			"HR queue scoping",
			WARN,
			f"{len(scoped)} HR Manager(s) have a User Permission on Employee, which empties their "
			f"HR queue: {shown} -- remove it for the people who work the queue",
		)
	return _result("HR queue scoping", PASS, f"{len(enabled)} HR Manager(s), none scoped to one Employee")


def check_template_tokens():
	"""Plan 2026-10-02-001 U8: every event's default renders against its
	sample data in the HelixHR sandbox, and every saved template still passes
	save-time validation -- a row that would now be refused (a migrated
	legacy row, a Desk import) would otherwise fall back to the default on
	every send, silently but for the Error Log."""
	from helixhr.utils import (
		NOTIFICATION_EVENTS,
		TemplateRejected,
		render_message,
		sample_context,
		validate_message_template,
	)

	problems = []
	for event_key, event in NOTIFICATION_EVENTS.items():
		try:
			validate_message_template(event_key, event["subject"], event["body"])
			render_message(event_key, sample_context(event_key))
		except Exception as exc:
			problems.append(f"{event_key} default: {exc}")
	for row in frappe.get_all("HelixHR Message Template", fields=["template_key", "subject", "body"]):
		try:
			validate_message_template(row.template_key, row.subject, row.body)
		except TemplateRejected as exc:
			problems.append(f"{row.template_key}: {exc}")
	if problems:
		return _result("Message template tokens", FAIL, "; ".join(problems))
	return _result("Message template tokens", PASS, f"{len(NOTIFICATION_EVENTS)} events checked")


def check_configuration_field_sets():
	"""P5-U13 / P5-KTD12: the named short field set behind each save_* method
	still exists on its HRMS doctype.

	These are portal-owned allow-lists over doctypes HelixHR does not own --
	an HRMS upgrade that renames or removes one of these fields would make
	the corresponding `save_*` method silently drop a value on every call,
	with no error anywhere. A FAIL here is the loud version of that.
	"""
	from helixhr.utils import (
		HOLIDAY_LIST_EDITABLE_FIELDS,
		LEAVE_TYPE_EDITABLE_FIELDS,
		SHIFT_TYPE_EDITABLE_FIELDS,
	)

	field_sets = {
		"Leave Type": LEAVE_TYPE_EDITABLE_FIELDS,
		"Holiday List": HOLIDAY_LIST_EDITABLE_FIELDS,
		"Shift Type": SHIFT_TYPE_EDITABLE_FIELDS,
	}
	problems = []
	for doctype, fields in field_sets.items():
		meta = frappe.get_meta(doctype)
		missing = [field for field in fields if not meta.has_field(field)]
		if missing:
			problems.append(f"{doctype}: {', '.join(missing)} no longer exist")
	if problems:
		return _result("Configuration field sets", FAIL, "; ".join(problems))
	return _result("Configuration field sets", PASS, f"{len(field_sets)} doctypes checked")


def check_pdf_generator():
	"""P3-R2: the payslip PDF is rendered by a binary on the host, not by this
	app, so a site without one answers 500 on a download that looks fine in
	every other respect. The Frappe production images ship wkhtmltopdf; a
	hand-built bench or a slim container may not."""
	import shutil

	generator = frappe.conf.get("pdf_generator") or "wkhtmltopdf"
	if generator == "chrome":
		found = shutil.which("chromium") or shutil.which("chrome") or shutil.which("google-chrome")
	else:
		found = shutil.which("wkhtmltopdf")

	if found:
		return _result("PDF generator", PASS, f"{generator} at {found}")
	return _result(
		"PDF generator",
		FAIL,
		f"{generator} is not on PATH -- payslip downloads answer 500 "
		"(install it, or set the site's `pdf_generator`)",
	)


def check_frontend_built():
	path = frappe.get_app_path("helixhr", "www", "helixhr.html")
	if os.path.exists(path):
		return _result("Frontend built", PASS, "www/helixhr.html present")
	return _result("Frontend built", FAIL, "www/helixhr.html missing -- cd frontend && yarn build")


def check_curated_reports():
	"""Plan 2026-10-04-001 U2 (generalises P6-U4): every ``frappe``-engine
	catalog entry names a standard, enabled Script Report on this site, and no
	deny-listed report (KTD3) is in the catalog. `prepared_report` on is a
	WARN (resolved decision 11): the portal calls `execute_module` directly,
	so it still gets rows, but Desk users of that report get queued jobs."""
	from helixhr.reports import CATALOG, DENY_LIST, wrapped_report_names

	problems, warnings = [], []
	for entry in CATALOG:
		if entry["report"] in DENY_LIST:
			problems.append(f"{entry['key']}: {entry['report']} is deny-listed")
	for name in wrapped_report_names():
		report = frappe.db.get_value(
			"Report", name, ["report_type", "is_standard", "disabled", "prepared_report"], as_dict=True
		)
		if not report:
			problems.append(f"{name}: not installed")
			continue
		if report.report_type != "Script Report" or report.is_standard != "Yes":
			problems.append(f"{name}: not a standard Script Report")
		if cint(report.disabled):
			problems.append(f"{name}: disabled")
		if cint(report.prepared_report):
			warnings.append(f"{name}: prepared_report is on")
	if problems:
		return _result("Curated reports", FAIL, "; ".join(problems + warnings))
	if warnings:
		return _result("Curated reports", WARN, "; ".join(warnings))
	return _result("Curated reports", PASS, f"{len(wrapped_report_names())} wrapped reports checked")


NOTIFICATION_MANAGER = "HelixHR Notification Manager"


def check_notification_manager_role():
	"""Plan 2026-10-02-001 U7: the Notification Manager stays portal-only.
	It no longer owns email templates (Portal Admin / System Manager do, see
	`check_portal_admin_role`), so who holds it is not checked."""
	role = frappe.db.get_value("Role", NOTIFICATION_MANAGER, ["desk_access", "is_custom"], as_dict=True)
	problems = []
	if not role:
		problems.append("Role fixture is missing")
	else:
		if cint(role.desk_access):
			problems.append("desk_access must be 0")
		if cint(role.is_custom):
			problems.append("is_custom must be 0")
	if problems:
		return _result("Notification Manager role", FAIL, "; ".join(problems) + " -- run bench migrate")
	return _result("Notification Manager role", PASS, "portal-only role")


REPORT_MANAGER = "HelixHR Report Manager"


def _no_grant_portal_role(role_name, label):
	"""A portal-only role (desk_access 0, fixture-owned) that holds no DocPerm
	reaching Desk's report, export or write paths."""
	role = frappe.db.get_value("Role", role_name, ["desk_access", "is_custom"], as_dict=True)
	problems = []
	if not role:
		problems.append("Role fixture is missing")
	else:
		if cint(role.desk_access):
			problems.append("desk_access must be 0")
		if cint(role.is_custom):
			problems.append("is_custom must be 0")
	for doctype in ("DocPerm", "Custom DocPerm"):
		for right in ("report", "export", "write", "create"):
			granted = frappe.get_all(doctype, filters={"role": role_name, right: 1}, pluck="parent")
			if granted:
				problems.append(f"holds {right} on {', '.join(sorted(set(granted)))} ({doctype})")
	if problems:
		return _result(label, FAIL, "; ".join(problems))
	return _result(label, PASS, "portal-only role with no report/export/write/create grant")


def check_report_manager_role():
	"""Plan 2026-10-04-001 U1 / R23: the Report Manager stays portal-only and
	holds no DocPerm that would reach Desk's report or export paths. It needs
	none: wrapped reports run elevated after HelixHR's own gate."""
	return _no_grant_portal_role(REPORT_MANAGER, "Report Manager role")


PORTAL_ADMIN = "HelixHR Portal Admin"


def check_portal_admin_role():
	"""The Portal Admin stays portal-only with no DocPerm at all that reaches
	report, export or write. It needs none: its endpoints (access matrix,
	export log, portal roles) gate themselves and save with
	``ignore_permissions``. WARNs while no enabled user holds it: then only
	System Manager can edit the portal's email theme and templates."""
	result = _no_grant_portal_role(PORTAL_ADMIN, "Portal Admin role")
	if result["status"] != PASS:
		return result
	holders = frappe.get_all("Has Role", filters={"role": PORTAL_ADMIN, "parenttype": "User"}, pluck="parent")
	if not holders or not frappe.db.exists("User", {"name": ("in", holders), "enabled": 1}):
		return _result(
			"Portal Admin role",
			WARN,
			"no enabled user holds it -- grant it in Desk (User > Roles) so someone owns email templates",
		)
	return result


def _fixture_roles():
	"""Every role `helixhr/fixtures/role.json` ships -- the one list, so a new
	portal role is guarded without editing this module."""
	import json

	with open(frappe.get_app_path("helixhr", "fixtures", "role.json")) as handle:
		return [row["name"] for row in json.load(handle)]


def check_no_timesheet_report_permission():
	"""P7-U8 / R16: no role this app grants -- every entry in
	`helixhr/fixtures/role.json`, not just `HelixHR Delivery Manager` --
	holds the doctype-wide `report` permission on Timesheet.

	`check_delivery_manager_role` already guards that one role specifically,
	as part of U1's own acceptance criterion; this check is the standing,
	general guard KTD3 asks for, so a *future* fixture role (or a Custom
	DocPerm hand-added in Desk) reopens the same bypass and is caught the
	same way: granting `report` on Timesheet is doctype-wide, and Frappe's
	report engine is directly callable by any signed-in user, so that grant
	would hand the holder every Timesheet report -- including
	`Timesheet Billing Summary`'s `billing_amount` -- with HelixHR's curated
	list offering no protection at all, because the bypass never goes
	through HelixHR (verified by exploit on the dev bench, see the plan's
	Sources and Research).

	Pre-existing and deliberately out of scope: `HR Manager` and `HR User`
	already hold `report` on Timesheet today via ERPNext/HRMS's own DocPerm
	fixtures, not a grant this app made -- narrowing that is a separate
	decision about existing roles, not this plan's.
	"""
	fixture_roles = _fixture_roles()
	granted_roles = frappe.get_all("Role", filters={"name": ["in", fixture_roles]}, pluck="name")
	problems = []
	for role in granted_roles:
		if frappe.db.get_value("Custom DocPerm", {"parent": "Timesheet", "role": role, "report": 1}):
			problems.append(f"{role} holds report permission on Timesheet (Custom DocPerm)")
		if frappe.db.get_value("DocPerm", {"parent": "Timesheet", "role": role, "report": 1}):
			problems.append(f"{role} holds report permission on Timesheet (standard DocPerm)")

	if problems:
		return _result("Timesheet report guard", FAIL, "; ".join(problems))
	return _result(
		"Timesheet report guard",
		PASS,
		f"no role this app grants ({', '.join(fixture_roles)}) holds report on Timesheet",
	)


CHECKS = [
	check_strict_user_permissions,
	check_employee_user_permissions,
	check_custom_docperm_coverage,
	check_leave_approver_mandatory,
	check_self_leave_approval_blocked,
	check_backdated_leave_grace,
	check_unsubmitted_approved_leave,
	check_document_link_urls,
	check_portal_landing,
	check_employee_open_fields,
	check_it_team_role,
	check_delivery_manager_role,
	check_notification_manager_role,
	check_report_manager_role,
	check_portal_admin_role,
	check_signup_disabled,
	check_password_login,
	check_entra,
	check_password_policy,
	check_file_settings,
	check_employee_photo_hooks,
	check_public_employee_photos,
	check_rate_limits,
	check_site_rate_limit,
	check_test_mode,
	check_csrf,
	check_public_endpoint,
	check_hr_contact,
	check_fixtures,
	check_retired_request_notifications,
	check_retired_hr_email_notifications,
	check_hrms_leave_notification,
	check_standard_working_hours,
	check_hr_request_workflow_state_order,
	check_timesheet_workflow_state_order,
	check_request_category_routes,
	check_request_category_prefixes,
	check_profile_correction_category,
	check_checkin_settings,
	check_shift_types,
	check_checkin_location_retention,
	check_holiday_list_coverage,
	check_celebration_reminders,
	check_cross_company_mailboxes,
	check_outgoing_email,
	check_overdue_digests,
	check_hr_manager_self_scope,
	check_template_tokens,
	check_configuration_field_sets,
	check_pdf_generator,
	check_frontend_built,
	check_curated_reports,
	check_no_timesheet_report_permission,
]
