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
what the HR-queue notifications and the reminders both need, and an HR
Manager scoped to their own Employee record has no HR queue (P4-R11).
"""

import os

import frappe
from frappe.utils import cint

from helixhr.patches.v1_0.apply_permission_deltas import DELTAS
from helixhr.utils import (
	ALLOWED_UPLOAD_EXTENSIONS,
	RATE_LIMIT_POLICY,
	UPLOAD_MAX_BYTES,
	portal_home_page,
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
		for row in frappe.get_all("HelixHR Document Link", fields=["name", "url"])
		if document_url_problem(row.url)
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
		# P3-KTD6 / P3-R26: the two-step attendance approval (P3-U5).
		("Workflow", "Attendance Request Approval"),
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
		("Workflow Action Master", "Send Back"),
		("Workflow Action Master", "Send to HR"),
		("Activity Type", "General"),
		("Notification", "HelixHR Timesheet Status Changed"),
		("Notification", "HelixHR Leave Status Changed"),
		# P4-KTD9 / P4-R12: HR is told a request reached its queue by these
		# four fixture Notifications and by nothing in code, so a missing one
		# is a queue nobody is watching. Leave needs two -- Frappe skips
		# Value Change while `flags.in_insert`, and an HR-approves leave is
		# *inserted* in the HR stage.
		("Notification", "HelixHR Leave Sent To HR"),
		("Notification", "HelixHR New Leave For HR"),
		("Notification", "HelixHR Timesheet Sent To HR"),
		("Notification", "HelixHR Attendance Request Sent To HR"),
	]
	missing = [f"{dt} '{name}'" for dt, name in expected if not frappe.db.exists(dt, name)]
	if missing:
		return _result("Fixtures installed", FAIL, "missing " + ", ".join(missing) + " -- run bench migrate")
	return _result("Fixtures installed", PASS, f"{len(expected)} checked")


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
	same event.

	Frappe merges `scheduler_events` across apps and offers no way to remove
	HRMS's daily reminder job, so HRMS's stock email keeps going out while
	its own checkbox is ticked and HelixHR's branded one goes out while HR
	has picked a template. `events.hr_settings_validate` refuses the save
	that creates the contradiction; this is the backstop for the routes that
	never reach `validate` -- a fixture import, a raw `db_set`, a restored
	site.

	A picked template that does not exist is the other FAIL: the job logs it
	and sends nothing, so the event goes quiet with no other sign. Neither
	sender on for an event is a WARN and not a FAIL -- a site may not want
	the email at all -- and either one on is a PASS naming which one sends.
	"""
	from helixhr.reminders import EVENTS

	problems, notes, quiet = [], [], []
	for spec in EVENTS.values():
		template = _hr_setting(spec["template_field"])
		hrms_on = cint(_hr_setting(spec["hrms_field"]))
		if template and hrms_on:
			problems.append(
				f"{spec['label']}: both HRMS and HelixHR would send -- untick "
				f"'{spec['hrms_label']}' in HR Settings or clear '{spec['template_label']}'"
			)
		elif template and not frappe.db.exists("Email Template", template):
			problems.append(
				f"{spec['label']}: '{spec['template_label']}' names Email Template "
				f"'{template}', which does not exist -- nothing is sent"
			)
		elif template:
			notes.append(f"{spec['label']}: HelixHR sends '{template}'")
		elif hrms_on:
			notes.append(f"{spec['label']}: HRMS sends its own")
		else:
			quiet.append(f"{spec['label']}: nobody sends")

	if problems:
		return _result("Celebration reminders", FAIL, "; ".join(problems))
	if quiet:
		return _result("Celebration reminders", WARN, "; ".join(quiet + notes))
	return _result("Celebration reminders", PASS, "; ".join(notes))


def check_outgoing_email():
	"""P4-R18: `frappe.sendmail` throws without a default outgoing Email
	Account, and two things now depend on it -- the HR-queue Notifications,
	which send from inside the save that escalates a request (P4-R12), and
	the celebration reminders. A WARN rather than a FAIL because a site can
	run the portal with no mail at all; what it cannot do is escalate to HR
	and have the email arrive."""
	account = frappe.db.get_value(
		"Email Account", {"enable_outgoing": 1, "default_outgoing": 1}, "name"
	)
	if account:
		return _result("Outgoing email", PASS, f"default outgoing account '{account}'")
	return _result(
		"Outgoing email",
		WARN,
		"no default outgoing Email Account -- the HR-queue notifications and the celebration "
		"reminders both fail to send (Desk: Email Account)",
	)


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


CHECKS = [
	check_strict_user_permissions,
	check_employee_user_permissions,
	check_custom_docperm_coverage,
	check_leave_approver_mandatory,
	check_self_leave_approval_blocked,
	check_unsubmitted_approved_leave,
	check_document_link_urls,
	check_portal_landing,
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
	check_checkin_settings,
	check_shift_types,
	check_checkin_location_retention,
	check_holiday_list_coverage,
	check_celebration_reminders,
	check_outgoing_email,
	check_hr_manager_self_scope,
	check_pdf_generator,
	check_frontend_built,
]
