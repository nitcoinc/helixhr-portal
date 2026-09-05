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
"""

import os

import frappe

from helixhr.utils import (
	ALLOWED_UPLOAD_EXTENSIONS,
	RATE_LIMIT_POLICY,
	UPLOAD_MAX_BYTES,
	rate_limit_bounds,
)

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
	"""
	problems = []
	for doctype in ("Employee", "Leave Application", "Timesheet"):
		custom = set(frappe.get_all("Custom DocPerm", filters={"parent": doctype}, pluck="role"))
		if not custom:
			continue
		standard = set(
			frappe.get_all("DocPerm", filters={"parent": doctype}, pluck="role", parent_doctype="DocType")
		)
		lost = sorted(standard - custom)
		if lost:
			problems.append(f"{doctype}: {', '.join(lost)}")
	if problems:
		return _result(
			"Custom DocPerm coverage",
			FAIL,
			"Custom DocPerm rows replaced the standard ones and left these roles with no access -- "
			+ "; ".join(problems)
			+ " -- re-run helixhr.patches.v1_0.apply_permission_deltas",
		)
	return _result("Custom DocPerm coverage", PASS, "no role lost access to a customised doctype")


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


def check_entra():
	"""Phase-aware: informational while the site says it is on local login,
	a FAIL once it says it is on Entra and the key is not there."""
	phase = _auth_phase()
	if _entra_enabled():
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
# because both execute in the site's own origin.
_ALLOWED_EXTENSION_NAMES = {e.lstrip(".").upper() for e in ALLOWED_UPLOAD_EXTENSIONS}
_MAX_FILE_SIZE_MB = UPLOAD_MAX_BYTES // (1024 * 1024)


def check_file_settings():
	"""P2-U9 step 7: the exact policy, not merely "a value is set".

	`helixhr.utils.validate_portal_upload` is the real gate for anything
	attached to an HR Request, and it needs no help from site settings. This
	check is about everything *else* a logged-in user can upload: an
	`allowed_file_extensions` list that still permits SVG or HTML, a
	`max_file_size` above the portal's own 10MB, guests uploading at all, or
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
	if "permissions-policy" not in headers:
		problems.append("no Permissions-Policy")

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
	check_custom_docperm_coverage,
	check_leave_approver_mandatory,
	check_self_leave_approval_blocked,
	check_unsubmitted_approved_leave,
	check_signup_disabled,
	check_password_login,
	check_entra,
	check_password_policy,
	check_file_settings,
	check_rate_limits,
	check_site_rate_limit,
	check_test_mode,
	check_csrf,
	check_public_endpoint,
	check_hr_contact,
	check_fixtures,
	check_frontend_built,
]
