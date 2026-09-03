"""Go-live preflight: machine-checks the host-side settings the runbook's
checklist asks a human to eyeball.

Run on every site after deploying, before letting people in:

    bench --site <site> execute helixhr.preflight.run

Nothing here lives in the repo -- System Settings, Website Settings,
site_config.json and User Permissions are all per-site data -- so CI can
never see it, and the same command has to be run on staging and again on
production. Exit status is non-zero when any FAIL remains, so it can gate a
deploy script.

Phase: **local (username/password) login**. Entra ID is not configured yet,
so the sign-in checks below assert that password login is still *on* --
turning it off with no enabled Social Login Key locks everyone out. When
Entra goes live, the Office 365 key check becomes the failing one and the
password-login expectation flips; both are marked below.
"""

import os

import frappe

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


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


# --- authorization ----------------------------------------------------------


def check_strict_user_permissions():
	on = frappe.utils.cint(_system("apply_strict_user_permissions"))
	return _result(
		"Apply Strict User Permissions",
		PASS if on else FAIL,
		"on" if on else "off -- a User Permission on Employee does not restrict linked doctypes without it",
	)


def check_employee_user_permissions():
	"""Every Employee with a portal login must be scoped to their own record.
	Without the User Permission, that user can read every employee."""
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
	missing = sorted(e.user_id for e in employees if (e.user_id, e.name) not in scoped)
	if not employees:
		return _result("Employee User Permissions", WARN, "no active Employee has a user_id yet")
	if missing:
		shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
		return _result(
			"Employee User Permissions",
			FAIL,
			f"{len(missing)} of {len(employees)} linked employees have no User Permission: {shown}",
		)
	return _result("Employee User Permissions", PASS, f"all {len(employees)} linked employees scoped")


# --- sign-in (local-login phase) -------------------------------------------


def _entra_enabled():
	return bool(
		frappe.db.exists(
			"Social Login Key", {"social_login_provider": "Office 365", "enable_social_login": 1}
		)
	)


def check_signup_disabled():
	off = frappe.utils.cint(frappe.db.get_single_value("Website Settings", "disable_signup"))
	return _result(
		"Disable Signup",
		PASS if off else FAIL,
		"on" if off else "off -- an unknown sign-in would self-register instead of seeing 'contact HR'",
	)


def check_password_login():
	disabled = frappe.utils.cint(_system("disable_user_pass_login"))
	if disabled and not _entra_enabled():
		return _result(
			"Username/Password Login", FAIL, "disabled with no enabled Social Login Key -- nobody can sign in"
		)
	if disabled:
		return _result("Username/Password Login", PASS, "disabled; Entra ID is the only door")
	return _result("Username/Password Login", PASS, "enabled (local-login phase)")


def check_entra():
	# Informational until the Entra phase; flip to FAIL when it starts.
	if _entra_enabled():
		return _result("Entra ID (Office 365 key)", PASS, "enabled -- verify the OAuth round trip by hand")
	return _result("Entra ID (Office 365 key)", WARN, "not configured (expected in the local-login phase)")


def check_password_policy():
	on = frappe.utils.cint(_system("enable_password_policy"))
	return _result(
		"Password policy",
		PASS if on else WARN,
		"on" if on else "off -- with local login this is the only strength check",
	)


# --- uploads and rate limits ----------------------------------------------


def check_file_settings():
	exts = (_system("allowed_file_extensions") or "").strip()
	size = frappe.utils.cint(_system("max_file_size"))
	problems = []
	if not exts:
		problems.append("Allowed File Extensions unset")
	if not size:
		problems.append("Max File Size unset")
	if problems:
		return _result(
			"Upload limits", WARN, "; ".join(problems) + " -- the app does not constrain type or size"
		)
	return _result("Upload limits", PASS, f"extensions set, max {size} MB")


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
		("Activity Type", "General"),
		("Notification", "HelixHR Timesheet Status Changed"),
		("Notification", "HelixHR Leave Status Changed"),
	]
	missing = [f"{dt} '{name}'" for dt, name in expected if not frappe.db.exists(dt, name)]
	if missing:
		return _result("Fixtures installed", FAIL, "missing " + ", ".join(missing) + " -- run bench migrate")
	return _result("Fixtures installed", PASS, f"{len(expected)} checked")


def check_frontend_built():
	path = frappe.get_app_path("helixhr", "www", "helixhr.html")
	if os.path.exists(path):
		return _result("Frontend built", PASS, "www/helixhr.html present")
	return _result("Frontend built", FAIL, "www/helixhr.html missing -- cd frontend && yarn build")


CHECKS = [
	check_strict_user_permissions,
	check_employee_user_permissions,
	check_signup_disabled,
	check_password_login,
	check_entra,
	check_password_policy,
	check_file_settings,
	check_site_rate_limit,
	check_hr_contact,
	check_fixtures,
	check_frontend_built,
]
